# app/routers/v2/recommendations.py
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from app.auth.entitlements import require_feature
from app.db import get_core_pool
from app.services.player_card_ondemand import ensure_cards_requested

router = APIRouter(tags=["v2-recommendations"])
log = logging.getLogger("v2_recommendations")

_JSONB_COLUMNS = (
    "market_drivers", "similar_events", "qualified_strategies",
    "strategy_results", "failed_gate_reasons", "held_decision_reasons",
)


def _player_pool(request: Request):
    pool = getattr(request.app.state, "player_pool", None)
    if pool is None:
        raise HTTPException(503, "player pool not ready")
    return pool


def _versioned_card_url(url: Optional[str], generated_at: Optional[datetime]) -> Optional[str]:
    if not url or not generated_at:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={int(generated_at.timestamp() * 1000)}"


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    for key in _JSONB_COLUMNS:
        if isinstance(d.get(key), str):
            d[key] = json.loads(d[key])
    if d.get("computed_at"):
        d["computed_at"] = d["computed_at"].isoformat()
    if d.get("expected_net_roi") is None and "expected_net_roi_source" not in d:
        d["expected_net_roi_source"] = "unavailable_until_validated_model"
    d["display_name"] = d.get("display_name") or d.get("nickname") or d.get("card_name") or d.get("name")
    d["generated_card_url"] = _versioned_card_url(
        d.get("generated_card_url"), d.get("generated_card_at")
    )
    if d.get("generated_card_at"):
        d["generated_card_at"] = d["generated_card_at"].isoformat()
    return d


_PLAYER_COLUMNS = """
    p.name, p.display_name, p.nickname, p.rating, p.version, p.position, p.image_url,
    p.card_bg_image, p.card_cutout_image, p.card_cutout_type, p.card_name,
    p.generated_card_url, p.generated_card_status, p.generated_card_at, p.generated_card_flagged,
    p.nation_image, p.league_image, p.club_image,
    p.pace, p.shooting, p.passing, p.dribbling, p.defending, p.physicality
"""


def _needs_card(d: Dict[str, Any]) -> bool:
    return d.get("generated_card_status") != "ready" or bool(d.get("generated_card_flagged"))


_TRACK_RECORD_LABEL = {
    "quick_flip": "Quick Flip",
    "swing_trade": "Swing Trade",
    "long_hold": "Long Hold",
}

# ml_feature_pipeline.py opens one ml_labels row per horizon for every
# card it snapshots (not just ones a live BUY was issued for), so a raw
# hit-rate over every label would understate the engine's real accuracy
# with cards that never would have qualified for a live call in the
# first place. eligibility_tier='LIVE_ELIGIBLE' is the same hard-gate
# result the live engine itself would have produced (see
# ml_feature_pipeline.snapshot_card's would_pass_live_gates), so this is
# "how did the calls we'd have actually made turn out", not "how did
# every card in the database do" - see HORIZON_STRATEGY in
# ml_feature_pipeline.py for the horizon->strategy mapping this mirrors.
_TRACK_RECORD_MIN_SAMPLE = 20


# window query param -> window_days. "all" is not literally unbounded
# (an unindexed full-table scan with no lower bound is a real cost for
# no honest benefit - migration 040 is recent enough that a 2-year
# window already covers "everything that could exist"), so it is
# implemented as a generously large, explicit bound instead of a special
# no-filter code path.
_TRACK_RECORD_WINDOW_DAYS = {"7d": 7, "30d": 30, "90d": 90, "all": 730}


@router.get("/track-record")
async def track_record(request: Request, window: str = Query("90d")) -> Dict[str, Any]:
    """Aggregated, honest hit-rate - real closed-window outcomes, not a
    claim invented for marketing copy. Deliberately ungated (no
    require_feature call): this is a trust signal, and a trust signal
    only free/prospective users have to take on faith is not doing its
    job.

    REPOINTED to the FUT.GG outcome pipeline (migration 040 +
    app/services/futgg_outcome_grader.py). The legacy implementation
    below reads ml_labels, which app/services/ml_feature_pipeline.py
    populates exclusively from fair_value_mv - the broken FUTBIN
    materialized view that no longer drives any user-visible
    recommendation. It was therefore reporting the track record of a
    pipeline nobody uses, while the engine that actually produces every
    live recommendation had no outcome feedback whatsoever.

    The legacy path is kept as a fallback purely for the transition
    window: migration 040's tables start empty and only fill once the
    scanner has run and a horizon has fully elapsed, so until then this
    returns the legacy shape rather than an empty page. Once FUT.GG
    outcomes are flowing this branch stops being reachable and the
    ml_labels query below can be deleted outright.

    WHAT "total_recommendations" ACTUALLY COUNTS (read before changing
    the response shape): every row here is one background-scanner
    evaluation pass that scored buy/strong_buy for a card (see
    app/services/futgg_scanner.py, ~every 10 minutes, up to ~1,500
    candidates/pass, no dedup across passes) - NOT a distinct,
    deliberately-published, user-visible recommendation. There is no
    "published to a user" concept anywhere in this schema. The honest
    label for this whole endpoint is automated signal validation, not a
    log of trades a specific person was shown - the frontend copy must
    not imply the latter. See PR description for the full audit.

    `summary` below is the fields the UI should actually render;
    `strategies`/`sampleSize`/`hitRatePct` are kept byte-for-byte for the
    existing AI chat tool (app/routers/v2/ai_chat.py's get_track_record)
    and any other current consumer - this is an additive change, not a
    breaking one. Do not remove the `strategies` shape without checking
    ai_chat.py first.

    IMPORTANT: `sampleSize` in `strategies[0]` is `total_recommendations`
    (every graded call, including ones that never reached entry), NOT the
    same base `hitRatePct` is computed over (`entered`). Showing those two
    numbers side by side without qualification is the exact "75.5% / N
    closed calls" mislabeling this endpoint used to produce - see
    `summary.entered` for the true win-rate denominator.
    """
    from app.services.futgg_recommendation_store import track_record as futgg_track_record

    window_days = _TRACK_RECORD_WINDOW_DAYS.get(window, 90)

    try:
        core_pool = await get_core_pool()
        futgg = await futgg_track_record(core_pool, horizon="24h", window_days=window_days)
        if int(futgg.get("headline", {}).get("total_recommendations") or 0) > 0:
            head = futgg["headline"]
            no_entry = int(head["total_recommendations"]) - int(head["entered"])
            return {
                "source": "futgg",
                "engine": "futgg-outcome-grader",
                "window": window if window in _TRACK_RECORD_WINDOW_DAYS else "90d",
                "window_days": window_days,
                # The fields the rebuilt Track Record UI actually renders.
                # Every rate here is paired with its real denominator.
                "summary": {
                    "total_graded": head["total_recommendations"],
                    "entered": head["entered"],
                    "no_entry": no_entry,
                    "entry_rate_pct": head.get("entry_rate_pct"),
                    # "win" = realised_roi > 0 among entered trades - see
                    # futgg_recommendation_store.py's _outcome_result for
                    # why this, not target_hit, is the primary win/loss
                    # split shown per-outcome.
                    "win_rate_pct": head.get("profitable_rate_pct"),
                    "target_hit_rate_pct": head.get("target_hit_rate_pct"),
                    "avg_roi_pct": head.get("avg_roi_pct"),
                    "median_roi_pct": head.get("median_roi_pct"),
                    "avg_hold_minutes": head.get("avg_hold_minutes"),
                    "has_enough_data": futgg["has_enough_data"],
                    "min_sample": futgg["min_sample"],
                },
                "strategies": [
                    {
                        "strategy": "mispricing",
                        "label": "Mispricing calls",
                        "horizon": futgg["horizon"],
                        "sampleSize": head["total_recommendations"],
                        "hasEnoughData": futgg["has_enough_data"],
                        "hitRatePct": head.get("target_hit_rate_pct"),
                        "avgRealizedReturnPct": head.get("avg_roi_pct"),
                        "entryRatePct": head.get("entry_rate_pct"),
                        "profitableRatePct": head.get("profitable_rate_pct"),
                        "windowStart": None,
                        "windowEnd": None,
                    }
                ],
                "breakdowns": futgg.get("breakdowns", {}),
                "methodology": futgg["methodology"],
            }
    except Exception:
        # Falls back to the legacy ml_labels path below - normal and
        # expected on a database where migration 040 hasn't run yet, but
        # a silent `pass` here made a real bug (any exception in the
        # query/aggregation above) indistinguishable from that expected
        # case, with zero trace in the logs either way. Logged now so a
        # real failure is diagnosable from a backend log line instead of
        # requiring a full manual SQL investigation to even confirm
        # something's actually wrong.
        log.warning("futgg-backed /track-record failed; falling back to legacy ml_labels path", exc_info=True)

    pool = _player_pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                l.horizon,
                COUNT(*) FILTER (WHERE l.label_closed_at IS NOT NULL) AS closed_count,
                COUNT(*) FILTER (WHERE l.label_closed_at IS NOT NULL AND l.target_reached) AS hit_count,
                AVG(l.strategy_realized_return) FILTER (WHERE l.label_closed_at IS NOT NULL) AS avg_realized_return,
                MIN(s.snapshot_at) FILTER (WHERE l.label_closed_at IS NOT NULL) AS earliest_snapshot_at,
                MAX(l.label_closed_at) AS latest_closed_at
            FROM ml_labels l
            JOIN ml_feature_snapshots s ON s.id = l.feature_snapshot_id
            WHERE s.eligibility_tier = 'LIVE_ELIGIBLE'
              AND s.snapshot_at >= now() - interval '90 days'
            GROUP BY l.horizon
            """
        )

    by_horizon = {r["horizon"]: r for r in rows}
    strategies = []
    for horizon, strategy_name in (("24h", "quick_flip"), ("48h", "swing_trade"), ("7d", "long_hold")):
        r = by_horizon.get(horizon)
        closed = int(r["closed_count"]) if r else 0
        hits = int(r["hit_count"]) if r else 0
        has_enough_data = closed >= _TRACK_RECORD_MIN_SAMPLE
        strategies.append({
            "strategy": strategy_name,
            "label": _TRACK_RECORD_LABEL[strategy_name],
            "horizon": horizon,
            "sampleSize": closed,
            "hasEnoughData": has_enough_data,
            "hitRatePct": round(100 * hits / closed, 1) if has_enough_data else None,
            "avgRealizedReturnPct": round(float(r["avg_realized_return"]) * 100, 1) if has_enough_data and r["avg_realized_return"] is not None else None,
            "windowStart": r["earliest_snapshot_at"].isoformat() if r and r["earliest_snapshot_at"] else None,
            "windowEnd": r["latest_closed_at"].isoformat() if r and r["latest_closed_at"] else None,
        })

    return {
        "strategies": strategies,
        "methodology": (
            "Every recommendation the engine could have made is snapshotted and graded against what "
            "actually happened in the real market afterwards - this is not a backtest or a projection, "
            "only closed, already-happened outcomes count."
        ),
    }


_RECENT_OUTCOME_FILTERS = frozenset({"all", "wins", "losses", "no_entry"})


@router.get("/track-record/recent")
async def track_record_recent(
    window: str = Query("90d"),
    filter: str = Query("all", description="all|wins|losses|no_entry"),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """Individual recent graded outcomes - the "did our calls actually
    work" list, not just the aggregate. Deliberately ungated, same
    trust-signal reasoning as /track-record above.

    Returns an honest empty list (never a synthesized/backfilled one) if
    migration 040's tables have no graded rows yet - there is no legacy
    fallback here because the legacy ml_labels pipeline never had
    per-recommendation card identity worth showing a user (see
    /track-record's own docstring on why that path is being retired).
    """
    if filter not in _RECENT_OUTCOME_FILTERS:
        raise HTTPException(400, f"filter must be one of {sorted(_RECENT_OUTCOME_FILTERS)}")
    window_days = _TRACK_RECORD_WINDOW_DAYS.get(window, 90)

    from app.services.futgg_recommendation_store import recent_outcomes as futgg_recent_outcomes

    try:
        core_pool = await get_core_pool()
        outcomes = await futgg_recent_outcomes(
            core_pool, horizon="24h", window_days=window_days,
            result_filter=filter, limit=limit,
        )
    except Exception:
        log.warning("/track-record/recent failed (likely migration 040 not yet applied)", exc_info=True)
        outcomes = []

    return {
        "window": window if window in _TRACK_RECORD_WINDOW_DAYS else "90d",
        "window_days": window_days,
        "filter": filter,
        "outcomes": outcomes,
    }


@router.get("/players/{card_id}/recommendation")
async def get_player_recommendation(card_id: int, request: Request) -> Dict[str, Any]:
    await require_feature("ai_recommendations")(request)
    async with _player_pool(request).acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT r.*, {_PLAYER_COLUMNS}
            FROM recommendations_latest r
            LEFT JOIN fut_players p ON p.card_id=r.card_id
            WHERE r.card_id=$1 AND r.platform='ps'""", card_id,
        )
    if not row:
        raise HTTPException(404, "No recommendation computed for this card yet")
    d = _row_to_dict(row)
    if _needs_card(d):
        await ensure_cards_requested(_player_pool(request), [str(card_id)])
    return d


@router.get("/recommendations/card-images")
async def generated_card_images(request: Request, card_ids: str = Query("")) -> Dict[str, Any]:
    ids = []
    for raw in card_ids.split(","):
        try:
            if raw.strip():
                ids.append(int(raw.strip()))
        except ValueError:
            pass
    ids = list(dict.fromkeys(ids))[:100]
    if not ids:
        return {"images": {}}
    pool = _player_pool(request)
    async with pool.acquire() as conn:
        all_rows = await conn.fetch(
            """SELECT card_id, generated_card_url, generated_card_at,
                      generated_card_status, generated_card_flagged
            FROM fut_players WHERE card_id=ANY($1::bigint[])""", ids,
        )

    needs_card = [
        str(r["card_id"]) for r in all_rows
        if r["generated_card_status"] != "ready" or r["generated_card_flagged"]
    ]
    if needs_card:
        await ensure_cards_requested(pool, needs_card)

    return {
        "images": {
            str(r["card_id"]): _versioned_card_url(
                r["generated_card_url"], r["generated_card_at"]
            )
            for r in all_rows
            if r["generated_card_status"] == "ready"
            and r["generated_card_url"]
            and not r["generated_card_flagged"]
        },
        "statuses": {
            str(r["card_id"]): r["generated_card_status"] for r in all_rows
        },
    }


async def _trigger_missing_cards(pool, items: list) -> None:
    needs_card = [str(item["card_id"]) for item in items if _needs_card(item)]
    if needs_card:
        await ensure_cards_requested(pool, needs_card)


async def _feed(pool, where: str, order: str, limit: int) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""SELECT r.*, {_PLAYER_COLUMNS}
            FROM recommendations_latest r LEFT JOIN fut_players p ON p.card_id=r.card_id
            WHERE {where} ORDER BY {order} LIMIT $1""", limit)
    items = [_row_to_dict(r) for r in rows]
    await _trigger_missing_cards(pool, items)
    return {"items": items, "count": len(items)}


@router.get("/recommendations/opportunities")
async def opportunities(request: Request, limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    await require_feature("opportunity_feed")(request)
    return await _feed(_player_pool(request), "r.status='BUY'", "r.confidence DESC NULLS LAST", limit)


@router.get("/recommendations/high-confidence")
async def high_confidence(request: Request, limit: int=Query(20,ge=1,le=100), min_confidence: float=Query(70,ge=0,le=100)) -> Dict[str,Any]:
    await require_feature("opportunity_feed")(request)
    pool = _player_pool(request)
    async with pool.acquire() as conn:
        rows=await conn.fetch(f"""SELECT r.*, {_PLAYER_COLUMNS} FROM recommendations_latest r
        LEFT JOIN fut_players p ON p.card_id=r.card_id WHERE r.status='BUY' AND r.confidence >= $1
        ORDER BY r.confidence DESC NULLS LAST LIMIT $2""",min_confidence,limit)
    items=[_row_to_dict(r) for r in rows]
    await _trigger_missing_cards(pool, items)
    return {"items":items,"count":len(items)}


@router.get("/recommendations/avoid")
async def avoid(request: Request, limit: int=Query(20,ge=1,le=100))->Dict[str,Any]:
    await require_feature("opportunity_feed")(request)
    return await _feed(_player_pool(request),"r.status='AVOID'","r.computed_at DESC",limit)


_STRATEGY_ORDER: Dict[str,str] = {
 "quick_flip":"r.score_liquidity DESC NULLS LAST, r.likely_net_roi DESC NULLS LAST, r.score_confidence DESC NULLS LAST",
 "swing_trade":"r.likely_net_roi DESC NULLS LAST, r.score_momentum DESC NULLS LAST, r.score_confidence DESC NULLS LAST",
 "low_risk":"r.score_risk ASC NULLS LAST, r.score_confidence DESC NULLS LAST, r.conservative_net_roi DESC NULLS LAST",
 "long_hold":"r.score_momentum DESC NULLS LAST, r.bullish_net_roi DESC NULLS LAST, r.score_confidence DESC NULLS LAST",
 "lazy_buyer":"r.score_liquidity DESC NULLS LAST, r.likely_net_roi DESC NULLS LAST",
 "sbc":"COALESCE((r.strategy_results->'sbc'->>'score')::numeric,0) DESC, r.likely_net_roi DESC NULLS LAST",
}

_STRATEGY_FALLBACK = {
 "quick_flip":"r.status='BUY' AND r.score_liquidity >= 55 AND r.likely_net_roi > 0",
 "swing_trade":"r.status='BUY' AND r.score_momentum >= 45 AND r.likely_net_roi >= 3",
 "low_risk":"r.status='BUY' AND r.score_risk <= 45 AND r.score_confidence >= 55",
 "long_hold":"r.status='BUY' AND r.score_momentum >= 55 AND r.bullish_net_roi > 0",
 "lazy_buyer":"r.status='BUY' AND r.score_liquidity >= 45 AND r.likely_net_roi > 0",
 "sbc":"r.status='BUY' AND (r.market_drivers::text ILIKE '%sbc%' OR r.strategy_results ? 'sbc')",
}


@router.get("/recommendations/highest-likely-roi")
async def highest_likely_roi(request: Request, limit:int=Query(20,ge=1,le=100))->Dict[str,Any]:
    await require_feature("opportunity_feed")(request)
    return await _feed(_player_pool(request),"r.status='BUY' AND jsonb_array_length(r.qualified_strategies)>0","r.likely_net_roi DESC NULLS LAST",limit)


@router.get("/recommendations/strategy/{strategy_name}")
async def strategy_feed(strategy_name:str, request:Request, limit:int=Query(20,ge=1,le=100))->Dict[str,Any]:
    if strategy_name not in _STRATEGY_ORDER:
        raise HTTPException(404,f"Unknown strategy: {strategy_name}")
    await require_feature("opportunity_feed")(request)
    order=_STRATEGY_ORDER[strategy_name]
    pool=_player_pool(request)
    async with pool.acquire() as conn:
        rows=await conn.fetch(f"""SELECT r.*, {_PLAYER_COLUMNS} FROM recommendations_latest r
          LEFT JOIN fut_players p ON p.card_id=r.card_id
          WHERE r.qualified_strategies @> $2::jsonb ORDER BY {order} LIMIT $1""",limit,json.dumps([strategy_name]))
        if len(rows) < limit:
            existing=[r["card_id"] for r in rows]
            extra=await conn.fetch(f"""SELECT r.*, {_PLAYER_COLUMNS} FROM recommendations_latest r
              LEFT JOIN fut_players p ON p.card_id=r.card_id
              WHERE {_STRATEGY_FALLBACK[strategy_name]} AND NOT (r.card_id=ANY($2::bigint[]))
              ORDER BY {order} LIMIT $1""",limit-len(rows),existing or [-1])
            rows=list(rows)+list(extra)
    items=[_row_to_dict(r) for r in rows]
    await _trigger_missing_cards(pool, items)
    return {"strategy":strategy_name,"items":items,"count":len(items),"strict_count":len(items)-max(0,len(items)-limit)}
