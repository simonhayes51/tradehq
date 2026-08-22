# backend/scripts/backfill_fc26_cohorts.py
"""
One-off: populate investment_cohort_memberships for FC26 so the market
event study (scripts/market_event_study.py) can show a per-cohort
price-movement breakdown instead of an empty one.

Does NOT run the Investment Engine. Calls ONLY
investment_engine/runner.py's existing ensure_cohort_membership() -
the exact same classifier the live engine uses, unmodified - to assign
cohorts. Never touches investment_signals, investment_market_states, or
any state-machine/strategy code. Purely a research-data population step.

Idempotent: ensure_cohort_membership() is ON CONFLICT DO NOTHING per
migration 058's unique index, so re-running this is always safe and
never reclassifies an already-classified card.

Usage:
    python -m scripts.backfill_fc26_cohorts
    python -m scripts.backfill_fc26_cohorts --dsn postgres://...
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("backfill_fc26_cohorts")

GAME_YEAR = 26


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None)
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    dsn = args.dsn or os.getenv("PLAYER_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        log.error("need --dsn or PLAYER_DATABASE_URL/DATABASE_URL")
        return 1

    import asyncpg
    from app.services.investment_engine import cohorts as ch
    from app.services.investment_engine.config import INVESTMENT_ENGINE_CONFIG
    from app.services.investment_engine.runner import ensure_cohort_membership

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            already = await conn.fetchval(
                "SELECT count(*) FROM investment_cohort_memberships WHERE game_year = $1",
                GAME_YEAR,
            )
            rows = await conn.fetch(
                """
                SELECT source_card_id, primary_position, current_bin, price_updated_at
                FROM futgg_market_snapshot
                WHERE game_year = $1 AND current_bin IS NOT NULL
                """,
                GAME_YEAR,
            )
            cards = [
                ch.CohortInput(
                    card_id=r["source_card_id"],
                    reference_price=float(r["current_bin"]),
                    primary_position=r["primary_position"],
                    reference_captured_at=r["price_updated_at"],
                )
                for r in rows
            ]
            log.info(
                "FC26: %d cards already classified, %d priced cards eligible for classification",
                already, len(cards),
            )
            if not cards:
                log.warning(
                    "No priced FC26 cards found in futgg_market_snapshot - nothing to classify. "
                    "Is the FC26 price pipeline still running?"
                )
                return 0

            inserted = await ensure_cohort_membership(
                conn, game_year=GAME_YEAR, cards=cards, config=INVESTMENT_ENGINE_CONFIG,
            )

            summary = await conn.fetch(
                """
                SELECT cohort_key, count(*) AS n
                FROM investment_cohort_memberships
                WHERE game_year = $1
                GROUP BY cohort_key ORDER BY n DESC
                """,
                GAME_YEAR,
            )
    finally:
        await pool.close()

    log.info("classify_cohorts attempted %d new row(s) (ON CONFLICT DO NOTHING - already-classified cards untouched)", inserted)
    log.info("FC26 cohort sizes now:")
    for r in summary:
        log.info("  %-20s %d", r["cohort_key"], r["n"])
    log.info("Re-run scripts/market_event_study.py --game-year 26 now to see the per-cohort breakdown.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
