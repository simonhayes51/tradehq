"""Populate live Investment Engine cohort memberships from the priced market.

The Investment Engine's strategy loop only reads ``investment_cohort_memberships``.
Historically that table was populated by a one-off FC26 research backfill, while
the live FC27 runner defined ``ensure_cohort_membership`` but never called it.
That leaves an otherwise healthy SHADOW engine permanently at ``cards=0``.

This managed background job closes that wiring gap without changing strategy
maths or publication policy:

* only cards already priced for the configured Investment game year are used;
* the existing cohort classifier is reused unchanged;
* assignments remain immutable because the existing store is
  ``ON CONFLICT DO NOTHING``;
* a missing/not-yet-launched game-year universe is a harmless zero-card pass;
* the job is lifecycle-managed and cancelled cleanly on shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services import game_cycle
from app.services.investment_engine import cohorts as ch
from app.services.investment_engine.config import INVESTMENT_ENGINE_CONFIG
from app.services.investment_engine.runner import ensure_cohort_membership

log = logging.getLogger("investment_cohort_bootstrap")

INITIAL_DELAY_SECONDS = 35


async def populate_live_cohorts(pool, *, game_year: int) -> dict[str, Any]:
    """Classify the current priced universe for ``game_year`` once.

    We deliberately feed the classifier the full currently-priced universe, not
    merely unclassified rows, because percentile cohorts are position-relative.
    Existing rows remain frozen by ``ensure_cohort_membership``'s conflict rule;
    newly-arrived cards are therefore classified against the market that exists
    when they first become eligible.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_card_id, primary_position, current_bin, price_updated_at
            FROM futgg_market_snapshot
            WHERE game_year = $1
              AND current_bin IS NOT NULL
              AND is_tradeable IS DISTINCT FROM FALSE
            """,
            int(game_year),
        )
        if not rows:
            return {
                "game_year": int(game_year),
                "priced_cards": 0,
                "memberships": 0,
                "classification_attempts": 0,
            }

        cards = [
            ch.CohortInput(
                card_id=int(row["source_card_id"]),
                reference_price=float(row["current_bin"]),
                primary_position=row["primary_position"],
                reference_captured_at=row["price_updated_at"],
            )
            for row in rows
        ]
        attempted = await ensure_cohort_membership(
            conn,
            game_year=int(game_year),
            cards=cards,
            config=INVESTMENT_ENGINE_CONFIG,
        )
        memberships = await conn.fetchval(
            """
            SELECT count(*)
            FROM investment_cohort_memberships
            WHERE game_year = $1 AND classifier_version = $2
            """,
            int(game_year),
            ch.CLASSIFIER_VERSION,
        )

    return {
        "game_year": int(game_year),
        "priced_cards": len(rows),
        "memberships": int(memberships or 0),
        "classification_attempts": int(attempted or 0),
    }


async def investment_cohort_bootstrap_loop(pool) -> None:
    """Refresh cohort membership periodically while the engine is enabled."""
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    game_year = game_cycle.investment_game_year()
    interval = max(300, int(INVESTMENT_ENGINE_CONFIG.cohort_refresh_interval_seconds))

    while True:
        try:
            consistency = game_cycle.consistency_report()
            if not consistency.get("consistent"):
                log.warning(
                    "investment cohort bootstrap game-year mismatch: %s",
                    "; ".join(consistency.get("problems") or []),
                )

            result = await populate_live_cohorts(pool, game_year=game_year)
            if result["priced_cards"]:
                log.info(
                    "investment cohorts: game_year=%d priced=%d memberships=%d attempts=%d",
                    result["game_year"],
                    result["priced_cards"],
                    result["memberships"],
                    result["classification_attempts"],
                )
            else:
                log.info(
                    "investment cohorts: game_year=%d has no priced tradeable cards yet",
                    result["game_year"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("investment cohort bootstrap pass failed", exc_info=True)

        await asyncio.sleep(interval)


def install_investment_cohort_bootstrap(application) -> None:
    """Register the cohort loop with the existing managed lifespan wrapper."""
    if not INVESTMENT_ENGINE_CONFIG.enabled:
        return

    from app.services.managed_startup_tasks import register_startup_job

    async def job() -> None:
        pool = (
            getattr(application.app.state, "db_pool", None)
            or getattr(application.app.state, "pool", None)
        )
        if pool is None:
            from app.db import get_core_pool
            pool = await get_core_pool()
        await investment_cohort_bootstrap_loop(pool)

    register_startup_job("investment_cohort_bootstrap", job)
