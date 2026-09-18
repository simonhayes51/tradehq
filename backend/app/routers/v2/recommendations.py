# app/routers/v2/recommendations.py
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.auth.access import Viewer, get_viewer
from app.auth.entitlements import FREE_TRACK_RECORD_WINDOW_DAYS, require_feature
from app.db import get_core_pool
from app.services.player_card_ondemand import ensure_cards_requested
from app.services.game_cycle import active_game_year

router = APIRouter(tags=["v2-recommendations"])

# Named so tests can override it by identity - require_feature() returns a
# fresh closure per call, which cannot be addressed through
# dependency_overrides.
require_full_track_record = require_feature("full_track_record")
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


# window query param -> window_days. "all" is not literally unbounded
# (an unindexed full-table scan with no lower bound is a real cost for
# no honest benefit - migration 040 is recent enough that a 2-year
# window already covers "everything that could exist"), so it is
# implemented as a generously large, explicit bound instead of a special
# no-filter code path.
_TRACK_RECORD_WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "all": 730}


async def _model_validation(window: str, window_days: int) -> Dict[str, Any]:
    """The FUT.GG scanner's own signal-grading dataset - background
    evaluations of every buy/strong_buy candidate on a ~10-minute
    cadence, no dedup across passes, no "published to a user" concept.
    Genuinely useful as an at-scale technical validation of the engine,
    but NOT a customer trade history - see track_record()'s docstring
    for the full incident this naming exists to prevent repeating.
    "entered"/"win"/"loss" language is deliberately avoided here in
    favour of "signal"/"entry reached"/"entry not reached", so this
    section cannot be read as a personal record of trades a user saw.
    """
    from app.services.futgg_recommendation_store import track_record as futgg_track_record

    try:
        core_pool = await get_core_pool()
        # This response only ever reads `headline` below - never
        # `breakdowns` - so skip the 7 extra per-dimension/reason-code
        # GROUP BY scans track_record() otherwise runs on every call.
        futgg = await futgg_track_record(core_pool, horizon="24h", window_days=window_days, include_breakdowns=False)
        if int(futgg.get("headline", {}).get("total_recommendations") or 0) > 0:
            head = futgg["headline"]
            entry_reached = int(head["entered"])
            graded_signals = int(head["total_recommendations"])
            return {
                "source": "futgg",
                "graded_signals": graded_signals,
                "entry_reached": entry_reached,
                "entry_not_reached": graded_signals - entry_reached,
                "entry_reached_rate_pct": head.get("entry_rate_pct"),
                "profitable_rate_pct": head.get("profitable_rate_pct"),
                "target_hit_rate_pct": head.get("target_hit_rate_pct"),
                "avg_roi_pct": head.get("avg_roi_pct"),
                "median_roi_pct": head.get("median_roi_pct"),
                "avg_hold_minutes": head.get("avg_hold_minutes"),
                "has_enough_data": futgg["has_enough_data"],
                "min_sample": futgg["min_sample"],
                "methodology": futgg["methodology"],
                # Kept byte-for-byte for the existing AI chat tool
                # (app/routers/v2/ai_chat.py's get_track_record) and any
                # other current consumer of the old response shape - do
                # not remove without checking ai_chat.py first.
                "strategies": [
                    {
                        "strategy": "mispricing",
                        "label": "Mispricing calls",
                        "horizon": futgg["horizon"],
                        "sampleSize": graded_signals,
                        "hasEnoughData": futgg["has_enough_data"],
                        "hitRatePct": head.get("target_hit_rate_pct"),
                        "avgRealizedReturnPct": head.get("avg_roi_pct"),
                        "entryRatePct": head.get("entry_rate_pct"),
                        "profitableRatePct": head.get("profitable_rate_pct"),
                        "windowStart": None,
                        "windowEnd": None,
                    }
                ],
            }
    except Exception:
        log.warning("futgg-backed model-validation track record failed", exc_info=True)

    return {
        "source": "unavailable",
        "graded_signals": 0,
        "entry_reached": 0,
        "entry_not_reached": 0,
        "has_enough_data": False,
        "strategies": [],
    }


@router.get("/track-record")
async def track_record(
    request: Request,
    window: str = Query("90d"),
    viewer: Viewer = Depends(get_viewer),
) -> Dict[str, Any]:
    """Two distinct, clearly separated datasets - never presented as
    interchangeable. See PR description for the full audit this
    restructure was built from.

    `published`: recommendations actually posted to real users via the
    Discord Proof Ledger (discord_daily_proof_publications, status=
    'sent' rows only - see app/services/discord_daily_proof.py's
    published_track_record()). This is the primary, customer-facing
    number: genuine wins/losses/win-rate/profit for opportunities that
    were actually surfaced, not internal model scoring.

    `model_validation`: the FUT.GG scanner's own background signal
    grading - a much larger, technical dataset (every buy/strong_buy
    evaluation, ~10-minute cadence) that is useful as engine validation
    but is NOT a customer trade history. Labelled with "signal"/"entry
    reached" language, never "our calls"/"entered trades", so it cannot
    be mistaken for the primary section.

    Both are deliberately ungated (no require_feature call): a trust
    signal only free/prospective users have to take on faith is not
    doing its job.
    """
    from app.services.discord_daily_proof import (
        _fetch_published_eligible_rows,
        published_recent_wins_and_losses,
        published_track_record,
    )

    window_days = _TRACK_RECORD_WINDOW_DAYS.get(window, 90)
    resolved_window = window if window in _TRACK_RECORD_WINDOW_DAYS else "90d"

    # The headline summary stays public: a track record only works as a
    # trust signal if the people deciding whether to trust us can see it.
    # What is gated is DEPTH - a viewer without the full-history feature
    # is capped to a recent window, so "recent summary" and "full history"
    # are genuinely different products rather than the same page with a
    # different heading.
    capped_window = False
    if not viewer.has("full_track_record"):
        free_days = FREE_TRACK_RECORD_WINDOW_DAYS
        if window_days > free_days:
            window_days = free_days
            capped_window = True

    try:
        core_pool = await get_core_pool()
        # Fetched once and shared: published_track_record() (the
        # aggregate) and published_recent_wins_and_losses() (the 5+5
        # ledger rows below) both derive from the exact same per-sent-day
        # eligible-outcome fetch. Calling them independently ran that
        # fetch - one _SELECT_DAILY_OUTCOMES query per sent day in the
        # window, each with 3 correlated EXISTS subqueries - twice per
        # request.
        eligible_rows = await _fetch_published_eligible_rows(core_pool, window_days)
        published = await published_track_record(core_pool, window_days=window_days, eligible_rows=eligible_rows)
        if published.get("has_published_data"):
            # 5 most recent wins + 5 most recent losses, always both
            # visible - the primary customer-facing evidence, embedded
            # directly rather than a second round trip since it's a
            # handful of rows either way.
            recent = await published_recent_wins_and_losses(
                core_pool, window_days=window_days, limit=5, eligible_rows=eligible_rows,
            )
            published["recent_target_hits"] = recent["recent_target_hits"]
            published["recent_target_misses"] = recent["recent_target_misses"]
    except Exception:
        log.warning("published_track_record failed", exc_info=True)
        published = {"has_published_data": False, "tracking_since": None, "window_days": window_days}

    model_validation = await _model_validation(resolved_window, window_days)

    return {
        "window": resolved_window,
        "window_days": window_days,
        "game_year": active_game_year(),
        # Said plainly, so the UI can explain why a 90-day tab is showing
        # 30 days of data instead of silently mislabelling it.
        "window_capped": capped_window,
        "full_history_available": viewer.has("full_track_record"),
        "published": published,
        "model_validation": model_validation,
        # Back-compat top-level alias for ai_chat.py and any other
        # existing consumer of the pre-hotfix shape's `strategies` key.
        "strategies": model_validation.get("strategies", []),
    }


_RECENT_OUTCOME_FILTERS = frozenset({"all", "wins", "losses", "no_entry"})


@router.get("/track-record/recent")
async def track_record_recent(
    window: str = Query("90d"),
    filter: str = Query("all", description="all|wins|losses|no_entry"),
    limit: int = Query(20, ge=1, le=100),
    _ent: Dict[str, Any] = Depends(require_full_track_record),
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
        "game_year": active_game_year(),
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
