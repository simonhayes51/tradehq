"""Read-only market reinforcement research for broad SBC resource demand.

The SBC classifier correctly records broad requirements as segment hypotheses,
not card-level triggers. This module tests those hypotheses against observed
market behaviour without changing sbc_resource_demand.market_status or any
priority weight.

A shadow confirmation needs both price and transaction evidence. Price movement
alone is not enough: promo drift, content timing and the wider market can move a
segment for unrelated reasons. Unsupported resource shapes are reported as
unsupported rather than guessed.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

MIN_MATCHED_CARDS = 10
PRICE_CONFIRM_PCT = 3.0
PRICE_CONFLICT_PCT = -3.0
SALES_ACTIVITY_RATIO = 1.20


def _resource_filter(resource_type: str, resource_value: str) -> Optional[Tuple[str, list[Any]]]:
    kind = str(resource_type or "").upper()
    value = str(resource_value or "").strip()

    if kind == "EXACT_RATING" and value.isdigit():
        return "m.rating = $1", [int(value)]

    if kind == "MIN_RATING":
        match = re.match(r"^(\d+)", value)
        if match:
            return "m.rating >= $1", [int(match.group(1))]
        return None

    # Team rating is a squad-construction constraint, not a card segment.
    if kind == "MIN_TEAM_RATING":
        return None

    rarity_patterns = {
        "ICON": "%icon%",
        "HERO": "%hero%",
        "TOTW": "%team of the week%",
        "TOTS": "%team of the season%",
        "GOLD_RARE": "%gold rare%",
        "SILVER_RARE": "%silver rare%",
        "BRONZE_RARE": "%bronze rare%",
    }
    pattern = rarity_patterns.get(kind)
    if pattern:
        return "lower(COALESCE(m.rarity, '')) LIKE $1", [pattern]

    # PROMO_TYPE vocabulary is provider-derived and not guaranteed to match
    # futgg_market_snapshot.rarity exactly. Do not invent a fuzzy mapping here.
    return None


async def _matching_ids(conn, resource_type: str, resource_value: str, *, limit: int = 250):
    resolved = _resource_filter(resource_type, resource_value)
    if resolved is None:
        return None
    clause, args = resolved
    rows = await conn.fetch(
        f"""
        SELECT m.source_card_id
        FROM futgg_market_snapshot m
        WHERE m.is_tradeable IS DISTINCT FROM FALSE
          AND m.current_bin IS NOT NULL
          AND {clause}
        ORDER BY m.source_card_id
        LIMIT ${len(args) + 1}
        """,
        *args,
        int(limit),
    )
    return [int(row["source_card_id"]) for row in rows]


async def _segment_evidence(conn, card_ids: list[int], event_started_at, *, window_hours: int = 2) -> Dict[str, Any]:
    if not card_ids or event_started_at is None:
        return {
            "matched_cards": len(card_ids or []),
            "baseline_cards": 0,
            "current_cards": 0,
            "baseline_bin": None,
            "current_bin": None,
            "price_change_pct": None,
            "baseline_sales": None,
            "current_sales": None,
            "sales_activity_ratio": None,
        }

    window = timedelta(hours=int(window_hours))
    price = await conn.fetchrow(
        """
        WITH per_card AS (
            SELECT source_card_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY lowest_bin)
                     FILTER (WHERE captured_at >= $2 - $3::interval AND captured_at < $2) AS baseline_bin,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY lowest_bin)
                     FILTER (WHERE captured_at >= now() - $3::interval) AS current_bin
            FROM futgg_bin_history
            WHERE source_card_id = ANY($1::bigint[])
              AND captured_at >= LEAST($2 - $3::interval, now() - $3::interval)
            GROUP BY source_card_id
        )
        SELECT count(*) FILTER (WHERE baseline_bin IS NOT NULL) AS baseline_cards,
               count(*) FILTER (WHERE current_bin IS NOT NULL) AS current_cards,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY baseline_bin) FILTER (WHERE baseline_bin IS NOT NULL) AS baseline_bin,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY current_bin) FILTER (WHERE current_bin IS NOT NULL) AS current_bin
        FROM per_card
        """,
        card_ids,
        event_started_at,
        window,
    )

    sales = await conn.fetchrow(
        """
        SELECT count(*) FILTER (
                   WHERE approximate_sold_at >= $2 - $3::interval AND approximate_sold_at < $2
               ) AS baseline_sales,
               count(*) FILTER (
                   WHERE approximate_sold_at >= now() - $3::interval
               ) AS current_sales
        FROM futgg_sales_history
        WHERE source_card_id = ANY($1::bigint[])
          AND approximate_sold_at >= LEAST($2 - $3::interval, now() - $3::interval)
        """,
        card_ids,
        event_started_at,
        window,
    )

    baseline_bin = float(price["baseline_bin"]) if price and price["baseline_bin"] is not None else None
    current_bin = float(price["current_bin"]) if price and price["current_bin"] is not None else None
    price_change = (
        ((current_bin - baseline_bin) / baseline_bin) * 100.0
        if baseline_bin and current_bin is not None else None
    )
    baseline_sales = int(sales["baseline_sales"] or 0) if sales else 0
    current_sales = int(sales["current_sales"] or 0) if sales else 0
    sales_ratio = (current_sales / baseline_sales) if baseline_sales > 0 else None

    return {
        "matched_cards": len(card_ids),
        "baseline_cards": int(price["baseline_cards"] or 0) if price else 0,
        "current_cards": int(price["current_cards"] or 0) if price else 0,
        "baseline_bin": round(baseline_bin) if baseline_bin is not None else None,
        "current_bin": round(current_bin) if current_bin is not None else None,
        "price_change_pct": round(price_change, 2) if price_change is not None else None,
        "baseline_sales": baseline_sales,
        "current_sales": current_sales,
        "sales_activity_ratio": round(sales_ratio, 2) if sales_ratio is not None else None,
    }


def classify_shadow_reinforcement(evidence: Dict[str, Any]) -> Tuple[str, str]:
    matched = int(evidence.get("matched_cards") or 0)
    baseline_cards = int(evidence.get("baseline_cards") or 0)
    current_cards = int(evidence.get("current_cards") or 0)
    price_change = evidence.get("price_change_pct")
    sales_ratio = evidence.get("sales_activity_ratio")

    if matched < MIN_MATCHED_CARDS or baseline_cards < MIN_MATCHED_CARDS or current_cards < MIN_MATCHED_CARDS:
        return "INSUFFICIENT", "Not enough matched cards have both pre-event and current BIN evidence."
    if price_change is None:
        return "INSUFFICIENT", "No defensible segment price change can be measured."
    if float(price_change) <= PRICE_CONFLICT_PCT:
        return "CONFLICTING", "The matched segment is cheaper than its pre-event baseline, so market behaviour contradicts reinforcement."
    if float(price_change) >= PRICE_CONFIRM_PCT:
        if sales_ratio is not None and float(sales_ratio) >= SALES_ACTIVITY_RATIO:
            return "MARKET_CONFIRMED_CANDIDATE", "Price and completed-sales activity both strengthened after the SBC demand appeared."
        return "PRICE_ONLY", "Price strengthened, but completed-sales activity did not independently confirm the move."
    return "UNCONFIRMED", "Observed segment behaviour is not materially different from the pre-event baseline."


async def reinforcement_shadow_report(pool, *, lookback_hours: int = 72, limit: int = 40) -> Dict[str, Any]:
    interval = timedelta(hours=max(1, int(lookback_hours)))
    async with pool.acquire() as conn:
        demands = await conn.fetch(
            """
            SELECT id, source_event_id, challenge_id, resource_type, resource_value,
                   required_count, event_started_at, event_expires_at,
                   last_confirmed_at, decayed_weight, market_status, raw_text
            FROM sbc_resource_demand
            WHERE ambiguity_status = 'CONFIRMED'
              AND last_confirmed_at >= now() - $1::interval
              AND (event_expires_at IS NULL OR event_expires_at > now())
            ORDER BY last_confirmed_at DESC
            LIMIT $2
            """,
            interval,
            max(1, min(int(limit), 100)),
        )

        items = []
        for demand in demands:
            ids = await _matching_ids(conn, demand["resource_type"], demand["resource_value"])
            if ids is None:
                items.append({
                    "demand_id": int(demand["id"]),
                    "resource_type": demand["resource_type"],
                    "resource_value": demand["resource_value"],
                    "stored_market_status": demand["market_status"],
                    "shadow_status": "UNSUPPORTED",
                    "reason": "This resource shape cannot be mapped to a card segment without guessing.",
                    "evidence": None,
                    "raw_text": demand["raw_text"],
                })
                continue

            evidence = await _segment_evidence(conn, ids, demand["event_started_at"])
            status, reason = classify_shadow_reinforcement(evidence)
            items.append({
                "demand_id": int(demand["id"]),
                "resource_type": demand["resource_type"],
                "resource_value": demand["resource_value"],
                "required_count": int(demand["required_count"] or 0),
                "event_started_at": demand["event_started_at"].isoformat() if demand["event_started_at"] else None,
                "event_expires_at": demand["event_expires_at"].isoformat() if demand["event_expires_at"] else None,
                "decayed_weight": float(demand["decayed_weight"]) if demand["decayed_weight"] is not None else None,
                "stored_market_status": demand["market_status"],
                "shadow_status": status,
                "reason": reason,
                "evidence": evidence,
                "raw_text": demand["raw_text"],
            })

    counts: Dict[str, int] = {}
    for item in items:
        counts[item["shadow_status"]] = counts.get(item["shadow_status"], 0) + 1
    return {
        "mode": "SHADOW_ONLY",
        "lookback_hours": int(lookback_hours),
        "demands_checked": len(items),
        "status_counts": counts,
        "items": items,
        "writes_market_status": False,
        "changes_priority_weight": False,
    }
