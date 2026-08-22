# backend/scripts/reconstruct_market_events.py
"""
Rebuild the content-event timeline from FUT.GG card release timestamps.

    # See what WOULD be created, without writing:
    python -m scripts.reconstruct_market_events --game-year 26 --dry-run

    # Write / refresh events:
    python -m scripts.reconstruct_market_events --game-year 26

Idempotent - re-running updates existing events in place rather than
duplicating them (migration 064's (game_year, event_key, released_at)
unique index), so this is safe to run repeatedly as the release-metadata
backfill progresses and more timestamps become known.

Requires migrations 063 (futgg_players.released_at) and 064
(market_events) to have been applied, and enough of the backfill to have
run that released_at is populated. See auto_sync/futgg_player_sync.py's
FUTGG_BACKFILL_RELEASE_METADATA mode.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("reconstruct_market_events")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=None,
                         help="Restrict to one game year (default: all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dsn", default=None)
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    dsn = args.dsn or os.getenv("PLAYER_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        log.error("need --dsn or PLAYER_DATABASE_URL/DATABASE_URL")
        return 1

    import asyncpg
    from app.services.market_event_reconstruction import reconstruct_events

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        summary = await reconstruct_events(
            pool, game_year=args.game_year, dry_run=args.dry_run,
        )
    finally:
        await pool.close()

    log.info(
        "%s: %d card(s) with a release timestamp -> %d event(s) %s",
        "DRY RUN" if args.dry_run else "reconstructed",
        summary["cards_considered"], summary["events_derived"],
        "(nothing written)" if args.dry_run else f"({summary['written']} written)",
    )
    log.info("  by scale: %s", summary["by_scale"])
    log.info("  by type:  %s", summary["by_type"])
    if summary["events_derived"] == 0 and summary["cards_considered"] == 0:
        log.warning(
            "No cards had both released_at AND squad populated. Has the "
            "FUTGG_BACKFILL_RELEASE_METADATA backfill run yet?"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
