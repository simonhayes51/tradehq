# app/routers/launch_health.py
"""
GET /api/ops/launch-health - the one-screen launch-morning answer.

Distinct from /api/ops/freshness (app/routers/ops.py), which answers "are
the pipelines writing" for an uptime monitor. This answers the different
question a human asks on launch day: "is the new game cycle actually
working end to end, and what is each engine doing right now?"

Deliberately built only from metrics that genuinely exist. Where a check
cannot be performed (a table absent, a pipeline not yet reporting), the
result is the explicit string "unknown" - never a fabricated green tick.
A health check that lies is worse than no health check, especially at
06:00 on launch day.

Admin-only: it exposes shadow-mode internals (unpublished signal counts,
suppression reasons) which must never reach ordinary users.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request

from app.db import get_core_pool
from app.routers.admin import require_admin
from app.services import game_cycle as gc
from app.services.futgg_config import ENGINE_CONFIG
from app.services.investment_engine.config import INVESTMENT_ENGINE_CONFIG

router = APIRouter(prefix="/api/ops", tags=["ops"])
log = logging.getLogger("launch_health")

STATUS_HEALTHY = "HEALTHY"
STATUS_DEGRADED = "DEGRADED"
STATUS_DOWN = "DOWN"
STATUS_UNKNOWN = "unknown"


async def _scalar(conn, sql: str, *args) -> Optional[Any]:
    """Every check is individually fault-tolerant: a missing table (a
    migration not yet applied on this environment) must degrade THAT
    check to unknown, never 500 the whole health report."""
    try:
        return await conn.fetchval(sql, *args)
    except Exception:
        return None


def _pipeline_status(age_seconds: Optional[float], degraded_after: float, down_after: float) -> str:
    if age_seconds is None:
        return STATUS_UNKNOWN
    if age_seconds > down_after:
        return STATUS_DOWN
    if age_seconds > degraded_after:
        return STATUS_DEGRADED
    return STATUS_HEALTHY


def _opportunity_status(
    active_opportunities: Optional[int],
    last_price_age: Optional[float],
    last_sale_age: Optional[float],
    last_bin_age: Optional[float],
) -> str:
    if active_opportunities is None:
        return STATUS_UNKNOWN
    if active_opportunities <= 0:
        return STATUS_DEGRADED
    ages = [age for age in (last_price_age, last_sale_age, last_bin_age) if age is not None]
    if not ages:
        return STATUS_UNKNOWN
    if last_price_age is not None and last_price_age > 3600:
        return STATUS_DOWN
    if any(age > 14400 for age in ages):
        return STATUS_DOWN
    if last_price_age is not None and last_price_age > 900:
        return STATUS_DEGRADED
    if any(age > 5400 for age in ages):
        return STATUS_DEGRADED
    return STATUS_HEALTHY


@router.get("/launch-health")
async def launch_health(
    request: Request,
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    await require_admin(request)
    now = datetime.now(timezone.utc)
    game_year = gc.active_game_year()

    async with pool.acquire() as conn:
        # ---- Catalogue / pricing coverage -------------------------------
        cards_discovered = await _scalar(
            conn, "SELECT count(*) FROM futgg_players WHERE game_year = $1", game_year,
        )
        cards_priced = await _scalar(
            conn,
            "SELECT count(*) FROM futgg_market_snapshot m "
            "JOIN futgg_players p ON p.source_card_id = m.source_card_id "
            "WHERE p.game_year = $1 AND m.current_bin IS NOT NULL",
            game_year,
        )
        fresh_priced = await _scalar(
            conn,
            "SELECT count(*) FROM futgg_market_snapshot m "
            "JOIN futgg_players p ON p.source_card_id = m.source_card_id "
            "WHERE p.game_year = $1 AND m.current_bin IS NOT NULL "
            "AND m.price_updated_at >= now() - interval '60 minutes'",
            game_year,
        )
        # Cards from a PREVIOUS game cycle still marked active. The
        # cutover mechanism is futgg_players.is_active (futgg_market_
        # snapshot filters on it), not a game_year predicate - so during
        # a transition, old-cycle cards linger until the player sync
        # deactivates them. Their prices go stale and the existing
        # freshness gates exclude them from recommendations, so this is
        # not dangerous; but it IS worth seeing, because a number that
        # stays high for days means the deactivation side of the cutover
        # is not running.
        stale_cycle_active = await _scalar(
            conn,
            "SELECT count(*) FROM futgg_players WHERE is_active AND game_year <> $1",
            game_year,
        )

        # ---- Pipeline recency -------------------------------------------
        last_price_age = await _scalar(
            conn, "SELECT EXTRACT(EPOCH FROM (now() - max(price_updated_at))) FROM futgg_market_snapshot",
        )
        last_sale_age = await _scalar(
            conn, "SELECT EXTRACT(EPOCH FROM (now() - max(approximate_sold_at))) FROM futgg_sales_history",
        )
        last_bin_age = await _scalar(
            conn, "SELECT EXTRACT(EPOCH FROM (now() - max(captured_at))) FROM futgg_bin_history",
        )

        # ---- Opportunity engine -----------------------------------------
        active_opportunities = await _scalar(
            conn,
            "SELECT count(*) FROM futgg_recommendations "
            "WHERE status = 'active' AND expires_at > now()",
        )

        # ---- Investment engine ------------------------------------------
        market_states = []
        try:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (strategy_key, cohort_key)
                    strategy_key, cohort_key, state, severity_class, computed_at
                FROM investment_market_states
                WHERE game_year = $1
                ORDER BY strategy_key, cohort_key, computed_at DESC
                """,
                game_year,
            )
            market_states = [dict(r) for r in rows]
        except Exception:
            market_states = []

        shadow_signals = await _scalar(
            conn,
            "SELECT count(*) FROM investment_signals "
            "WHERE game_year = $1 AND status = 'active' AND shadow = TRUE", game_year,
        )
        published_signals = await _scalar(
            conn,
            "SELECT count(*) FROM investment_signals "
            "WHERE game_year = $1 AND status = 'active' AND shadow = FALSE", game_year,
        )
        suppression_rows = []
        try:
            suppression_rows = await conn.fetch(
                """
                SELECT reason, count(*) AS n
                FROM investment_decision_log
                WHERE game_year = $1 AND decision = 'suppressed' AND superseded_at IS NULL
                GROUP BY reason ORDER BY n DESC
                """,
                game_year,
            )
        except Exception:
            suppression_rows = []

        last_engine_run_age = await _scalar(
            conn,
            "SELECT EXTRACT(EPOCH FROM (now() - max(created_at))) FROM investment_engine_runs "
            "WHERE game_year = $1", game_year,
        )

    priced_pct = (
        round(100.0 * cards_priced / cards_discovered, 1)
        if cards_discovered else None
    )
    fresh_pct = (
        round(100.0 * fresh_priced / cards_priced, 1)
        if cards_priced else None
    )

    # The Investment Engine's mode is derived from the flags themselves,
    # never from a separate variable that could drift out of sync with
    # what the engine is really doing.
    if not INVESTMENT_ENGINE_CONFIG.enabled:
        investment_mode = "DISABLED"
    elif INVESTMENT_ENGINE_CONFIG.publish_signals:
        investment_mode = "PUBLISHING"
    else:
        investment_mode = "SHADOW"

    return {
        "generated_at": now.isoformat(),
        "game_cycle": gc.describe(now),
        "catalogue": {
            "cards_discovered": cards_discovered,
            "cards_priced": cards_priced,
            "priced_pct": priced_pct,
            "fresh_priced": fresh_priced,
            "fresh_price_pct": fresh_pct,
            "previous_cycle_cards_still_active": stale_cycle_active,
        },
        "pipelines": {
            "price": {
                "last_update_age_seconds": round(last_price_age) if last_price_age is not None else None,
                "status": _pipeline_status(last_price_age, 900, 3600),
            },
            "sales": {
                "last_update_age_seconds": round(last_sale_age) if last_sale_age is not None else None,
                "status": _pipeline_status(last_sale_age, 5400, 14400),
            },
            "bin": {
                "last_update_age_seconds": round(last_bin_age) if last_bin_age is not None else None,
                "status": _pipeline_status(last_bin_age, 5400, 14400),
            },
        },
        "opportunity_engine": {
            "status": _opportunity_status(active_opportunities, last_price_age, last_sale_age, last_bin_age),
            "engine_version": ENGINE_CONFIG.version,
            "active_opportunities": active_opportunities,
            "unverifiable_discount_guard": ENGINE_CONFIG.unverifiable_discount_guard_enabled,
        },
        "investment_engine": {
            "mode": investment_mode,
            "enabled": INVESTMENT_ENGINE_CONFIG.enabled,
            "publishing": INVESTMENT_ENGINE_CONFIG.publish_signals,
            "last_run_age_seconds": round(last_engine_run_age) if last_engine_run_age is not None else None,
            "market_states": [
                {
                    "strategy": r["strategy_key"], "cohort": r["cohort_key"],
                    "state": r["state"], "severity": r["severity_class"],
                    "computed_at": r["computed_at"].isoformat() if r["computed_at"] else None,
                }
                for r in market_states
            ],
            "shadow_signals_active": shadow_signals,
            "published_signals_active": published_signals,
            "suppression_reasons": {r["reason"]: r["n"] for r in suppression_rows},
        },
    }
