"""Admin observability for the persisted FUT.GG intelligence pipeline.

Cheap aggregate reads only. This endpoint exists so an operator can answer
"is the scanner producing current intelligence, how much of the catalogue has
it touched recently, are states actually changing, and is Home reading the
persisted layer or its transitional fallback?" without replaying the engine or
scanning the multi-million-row raw history tables.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from app.routers.admin import require_admin
from app.services.dashboard_market_cache import cache_health as dashboard_cache_health

router = APIRouter(tags=["v2-intelligence-health"])
log = logging.getLogger("intelligence_health")


def _pool(request: Request):
    return getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "pool", None)


@router.get("/market/intelligence-health")
async def intelligence_health(
    request: Request,
    _admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = _pool(request)
    if pool is None:
        return {"status": "unavailable", "reason": "pool_missing"}

    try:
        async with pool.acquire() as conn:
            summary = await conn.fetchrow(
                """
                SELECT
                    count(*) AS current_rows,
                    count(*) FILTER (WHERE evaluated_at >= now() - interval '24 hours') AS evaluated_24h,
                    count(*) FILTER (WHERE signal IN ('buy','strong_buy')) AS actionable_now,
                    count(*) FILTER (WHERE signal = 'watch') AS watch_now,
                    count(*) FILTER (WHERE signal = 'avoid') AS avoid_now,
                    count(*) FILTER (WHERE signal = 'insufficient_data') AS insufficient_now,
                    max(evaluated_at) AS newest_evaluated_at,
                    min(evaluated_at) AS oldest_evaluated_at,
                    EXTRACT(EPOCH FROM (now() - max(evaluated_at))) / 60.0 AS newest_age_minutes
                FROM futgg_current_intelligence
                """
            )
            player_pool = await conn.fetchval(
                "SELECT count(*) FROM futgg_players WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE"
            )
            signals = await conn.fetch(
                """
                SELECT signal, count(*) AS card_count
                FROM futgg_current_intelligence
                GROUP BY signal
                ORDER BY signal
                """
            )

            # Transition history is useful observability, but current state is
            # the product dependency. If migration 081 is absent or the audit
            # table is temporarily unreadable, health should still report the
            # current-state picture instead of failing the whole endpoint.
            try:
                transitions = await conn.fetchrow(
                    """
                    SELECT
                        count(*) FILTER (WHERE changed_at >= now() - interval '1 hour') AS transitions_1h,
                        count(*) FILTER (WHERE changed_at >= now() - interval '24 hours') AS transitions_24h,
                        max(changed_at) AS latest_transition_at
                    FROM futgg_intelligence_transitions
                    """
                )
                recent = await conn.fetch(
                    """
                    SELECT source_card_id, changed_at, previous_signal, new_signal,
                           previous_status, new_status, current_bin, fair_value,
                           expected_roi, confidence_score, trend_state
                    FROM futgg_intelligence_transitions
                    ORDER BY changed_at DESC
                    LIMIT 20
                    """
                )
                transition_status = "ok"
            except Exception:
                log.warning("intelligence transition health unavailable", exc_info=True)
                transitions = {
                    "transitions_1h": 0,
                    "transitions_24h": 0,
                    "latest_transition_at": None,
                }
                recent = []
                transition_status = "unavailable"
    except Exception:
        log.warning("current intelligence health query failed", exc_info=True)
        return {
            "status": "unavailable",
            "reason": "current_intelligence_unreadable",
            "dashboard_market_cache": dashboard_cache_health(),
        }

    current_rows = int(summary["current_rows"] or 0)
    pool_size = int(player_pool or 0)
    evaluated_24h = int(summary["evaluated_24h"] or 0)
    newest_age = float(summary["newest_age_minutes"]) if summary["newest_age_minutes"] is not None else None

    # The scanner normally runs every 10 minutes. This is diagnostic rather
    # than a load-balancer readiness gate, so call out a >20m gap explicitly
    # without taking the application out of service.
    status = "ok"
    reason = None
    if current_rows == 0:
        status, reason = "degraded", "no_current_intelligence"
    elif newest_age is None or newest_age > 20:
        status, reason = "degraded", "scanner_state_stale"

    return {
        "status": status,
        "reason": reason,
        "player_pool": pool_size,
        "current_rows": current_rows,
        "current_coverage_pct": round(100.0 * current_rows / pool_size, 2) if pool_size else None,
        "evaluated_24h": evaluated_24h,
        "evaluated_24h_pct": round(100.0 * evaluated_24h / pool_size, 2) if pool_size else None,
        "newest_evaluated_at": summary["newest_evaluated_at"].isoformat() if summary["newest_evaluated_at"] else None,
        "oldest_evaluated_at": summary["oldest_evaluated_at"].isoformat() if summary["oldest_evaluated_at"] else None,
        "newest_age_minutes": round(newest_age, 1) if newest_age is not None else None,
        "actionable_now": int(summary["actionable_now"] or 0),
        "watch_now": int(summary["watch_now"] or 0),
        "avoid_now": int(summary["avoid_now"] or 0),
        "insufficient_now": int(summary["insufficient_now"] or 0),
        "signals": {row["signal"]: int(row["card_count"]) for row in signals},
        "transition_status": transition_status,
        "transitions_1h": int(transitions["transitions_1h"] or 0),
        "transitions_24h": int(transitions["transitions_24h"] or 0),
        "latest_transition_at": transitions["latest_transition_at"].isoformat() if transitions["latest_transition_at"] else None,
        "dashboard_market_cache": dashboard_cache_health(),
        "recent_transitions": [
            {
                **dict(row),
                "changed_at": row["changed_at"].isoformat(),
                "expected_roi": float(row["expected_roi"]) if row["expected_roi"] is not None else None,
                "fair_value": int(row["fair_value"]) if row["fair_value"] is not None else None,
            }
            for row in recent
        ],
    }
