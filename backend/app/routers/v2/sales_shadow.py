"""Admin report for the information-value SalesSync selector.

This endpoint reports the evidence used to decide whether information-value
cards deserve some of the existing detailed-sales request budget. The live
worker may now reallocate a bounded slice of that same budget, so this report
also distinguishes same-cycle pickups from cards the baseline scheduler picked
up later. Reallocation never increases the configured FUT.GG request count.

A shadow event is never called a miss until its full pickup window has elapsed.
Fresh events stay pending; an early pickup can still be credited immediately.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.routers.admin import require_admin
from app.services.game_cycle import active_game_year

router = APIRouter(tags=["v2-sales-shadow"])


def _pool(request: Request):
    return getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "pool", None)


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _card_note_map(row) -> Dict[int, Dict[str, Any]]:
    notes = _json_object(row["notes"])
    out: Dict[int, Dict[str, Any]] = {}
    cards = notes.get("cards")
    if not isinstance(cards, list):
        return out
    for item in cards:
        if not isinstance(item, dict):
            continue
        try:
            card_id = int(item.get("card_id"))
        except (TypeError, ValueError):
            continue
        out[card_id] = item
    return out


@router.get("/market/sales-information-shadow")
async def sales_information_shadow(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    pickup_window_minutes: int = Query(30, ge=5, le=180),
    _admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = _pool(request)
    if pool is None:
        raise HTTPException(503, "Core database pool unavailable")

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT observed_at, due_count, triggered_count,
                       triggered_new_count, user_driven_count,
                       held_position_count, due_card_ids, triggered_card_ids,
                       reason_counts, selector_version, notes
                FROM futgg_trigger_shadow_observations
                WHERE observed_at >= now() - ($1::int * interval '1 hour')
                ORDER BY observed_at
                """,
                int(hours),
            )
    except Exception as exc:
        if exc.__class__.__name__ in {"UndefinedTableError", "UndefinedColumnError"}:
            return {
                "status": "warming",
                "hours": int(hours),
                "cycles": 0,
                "reason": "shadow_schema_not_ready",
            }
        raise

    if not rows:
        return {
            "status": "warming",
            "hours": int(hours),
            "cycles": 0,
            "reason": "no_shadow_observations_yet",
        }

    reasons: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    due_times_by_card: Dict[int, list] = defaultdict(list)
    live_rebalance_cycles = 0
    for row in rows:
        reasons.update({str(k): int(v) for k, v in _json_object(row["reason_counts"]).items()})
        versions[str(row["selector_version"])] += 1
        if _json_object(row["notes"]).get("live_scheduler_changed"):
            live_rebalance_cycles += 1
        observed_at = row["observed_at"]
        for raw_card_id in row["due_card_ids"] or []:
            due_times_by_card[int(raw_card_id)].append(observed_at)

    pickup_delta = timedelta(minutes=int(pickup_window_minutes))
    measurement_cutoff = datetime.now(timezone.utc) - pickup_delta
    per_card: Dict[int, Dict[str, Any]] = {}
    measured_event_count = 0
    pending_event_count = 0
    pickup_count = 0
    same_cycle_pickup_count = 0
    user_measured_count = 0
    user_pending_count = 0
    user_pickup_count = 0

    for row in rows:
        observed_at = row["observed_at"]
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        note_map = _card_note_map(row)
        row_due_ids = {int(raw_id) for raw_id in (row["due_card_ids"] or [])}
        for raw_card_id in row["triggered_card_ids"] or []:
            card_id = int(raw_card_id)
            note = note_map.get(card_id, {})
            user_driven = bool(note.get("user_driven"))

            # Under the live rebalancer, a triggered card can be inserted into
            # THIS cycle's due set. The old bisect-right logic deliberately
            # looked only at later observations, so same-cycle reallocation was
            # incorrectly reported as a miss even though the scheduler had
            # selected it immediately.
            picked_up_same_cycle = card_id in row_due_ids
            due_times = due_times_by_card.get(card_id, [])
            idx = bisect_right(due_times, observed_at)
            next_due_at = due_times[idx] if idx < len(due_times) else None
            picked_up = picked_up_same_cycle or bool(
                next_due_at and next_due_at <= observed_at + pickup_delta
            )
            mature = observed_at <= measurement_cutoff
            pending = not picked_up and not mature

            if picked_up or mature:
                measured_event_count += 1
                if user_driven:
                    user_measured_count += 1
            else:
                pending_event_count += 1
                if user_driven:
                    user_pending_count += 1

            if picked_up:
                pickup_count += 1
                if picked_up_same_cycle:
                    same_cycle_pickup_count += 1
                if user_driven:
                    user_pickup_count += 1

            card = per_card.setdefault(card_id, {
                "card_id": card_id,
                "shadow_events": 0,
                "measured_events": 0,
                "pending_events": 0,
                "picked_up_events": 0,
                "same_cycle_pickups": 0,
                "missed_events": 0,
                "user_driven_events": 0,
                "reasons": Counter(),
                "first_shadow_at": observed_at,
                "last_shadow_at": observed_at,
            })
            card["shadow_events"] += 1
            card["measured_events"] += int(picked_up or mature)
            card["pending_events"] += int(pending)
            card["picked_up_events"] += int(picked_up)
            card["same_cycle_pickups"] += int(picked_up_same_cycle)
            card["missed_events"] += int(mature and not picked_up)
            card["user_driven_events"] += int(user_driven and (picked_up or mature))
            card["first_shadow_at"] = min(card["first_shadow_at"], observed_at)
            card["last_shadow_at"] = max(card["last_shadow_at"], observed_at)
            for reason in note.get("reasons") or []:
                card["reasons"][str(reason)] += 1

    repeated_missed = [
        card for card in per_card.values()
        if card["missed_events"] >= 2
    ]
    repeated_missed.sort(
        key=lambda card: (
            -card["missed_events"],
            -card["user_driven_events"],
            -card["shadow_events"],
            card["card_id"],
        )
    )

    # Enrich only the small operator-facing shortlist. This is observability,
    # not a hot product path, so one bounded second query is preferable to
    # making the observation table duplicate player identity/market state.
    shortlist = repeated_missed[:20]
    shortlist_ids = [card["card_id"] for card in shortlist]
    metadata: Dict[int, Dict[str, Any]] = {}
    if shortlist_ids:
        try:
            async with pool.acquire() as conn:
                meta_rows = await conn.fetch(
                    """
                    SELECT m.source_card_id, m.name, m.rating, m.rarity,
                           m.current_bin, c.signal, c.evaluated_at
                    FROM futgg_market_snapshot m
                    LEFT JOIN futgg_current_intelligence c
                      ON c.source_card_id = m.source_card_id AND c.game_year = m.game_year
                    WHERE m.source_card_id = ANY($1::bigint[])
                      AND m.game_year = $2
                    """,
                    shortlist_ids, active_game_year(),
                )
            metadata = {int(row["source_card_id"]): dict(row) for row in meta_rows}
        except Exception:
            metadata = {}

    repeated_missed_out = []
    for card in shortlist:
        meta = metadata.get(card["card_id"], {})
        repeated_missed_out.append({
            "card_id": card["card_id"],
            "name": meta.get("name"),
            "rating": meta.get("rating"),
            "rarity": meta.get("rarity"),
            "current_bin": meta.get("current_bin"),
            "current_signal": meta.get("signal"),
            "current_evaluated_at": meta.get("evaluated_at").isoformat() if meta.get("evaluated_at") else None,
            "shadow_events": card["shadow_events"],
            "measured_events": card["measured_events"],
            "pending_events": card["pending_events"],
            "picked_up_events": card["picked_up_events"],
            "same_cycle_pickups": card["same_cycle_pickups"],
            "missed_events": card["missed_events"],
            "user_driven_events": card["user_driven_events"],
            "reasons": dict(card["reasons"].most_common()),
            "first_shadow_at": card["first_shadow_at"].isoformat(),
            "last_shadow_at": card["last_shadow_at"].isoformat(),
        })

    cycles = len(rows)
    due_total = sum(int(row["due_count"] or 0) for row in rows)
    triggered_total = sum(int(row["triggered_new_count"] or 0) for row in rows)
    user_total = sum(int(row["user_driven_count"] or 0) for row in rows)
    held_total = sum(int(row["held_position_count"] or 0) for row in rows)
    missed_count = max(0, measured_event_count - pickup_count)

    return {
        "status": "ok",
        "hours": int(hours),
        "cycles": cycles,
        "first_observed_at": rows[0]["observed_at"].isoformat(),
        "last_observed_at": rows[-1]["observed_at"].isoformat(),
        "live_due_requests_observed": due_total,
        "shadow_priority_events": triggered_total,
        "avg_live_due_per_cycle": round(due_total / cycles, 2),
        "avg_shadow_priority_per_cycle": round(triggered_total / cycles, 2),
        "user_driven_shadow_events": user_total,
        "user_driven_share_pct": round(100.0 * user_total / triggered_total, 2) if triggered_total else 0.0,
        "held_position_shadow_events": held_total,
        "reason_counts": dict(reasons.most_common()),
        "selector_versions": dict(versions),
        "pickup_analysis": {
            "window_minutes": int(pickup_window_minutes),
            "measured_shadow_card_events": measured_event_count,
            "pending_shadow_card_events": pending_event_count,
            "picked_up_by_live_scheduler": pickup_count,
            "same_cycle_reallocated": same_cycle_pickup_count,
            "still_missed_after_window": missed_count,
            "pickup_rate_pct": (
                round(100.0 * pickup_count / measured_event_count, 2)
                if measured_event_count else None
            ),
            "user_driven_events_with_card_detail": user_measured_count,
            "user_driven_pending_events": user_pending_count,
            "user_driven_picked_up": user_pickup_count,
            "user_driven_pickup_rate_pct": (
                round(100.0 * user_pickup_count / user_measured_count, 2)
                if user_measured_count else None
            ),
            "repeated_missed_cards": repeated_missed_out,
            "note": (
                "Fresh information-value events remain pending until the full pickup window elapses. "
                "Same-cycle reallocated cards count as immediate pickups. Per-card reason/user classification "
                "is available from selector v1.2 onward."
            ),
        },
        "live_scheduler_changed": live_rebalance_cycles > 0,
        "live_rebalance_cycles": live_rebalance_cycles,
        "extra_futgg_sales_requests": 0,
        "decision_note": (
            "A bounded share of the existing cycle is now reallocated to information-value cards. "
            "No extra FUT.GG request budget is added; continue watching repeated misses and pickup rate."
            if live_rebalance_cycles > 0
            else "Shadow-only evidence is still being collected; the live scheduler has not reallocated slots in this window."
        ),
    }
