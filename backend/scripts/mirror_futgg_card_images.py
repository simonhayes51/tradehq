#!/usr/bin/env python
# scripts/mirror_futgg_card_images.py
#
# One-shot catch-up for app/services/futgg_card_image_mirror.py's
# background loop (which already runs continuously via main.py's
# lifespan, default every 60s / 50 cards per pass). Not required for
# correctness - the loop backfills the whole catalogue on its own given
# enough time - but useful right after this feature first ships, to reach
# a fully-mirrored catalogue immediately instead of waiting out however
# many passes a large catalogue would otherwise take.
#
# Usage:
#   python -m scripts.mirror_futgg_card_images --limit=2000
#   python -m scripts.mirror_futgg_card_images --limit=500 --batch-size=100
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import asyncpg
from dotenv import load_dotenv

from app.services.futgg_card_image_mirror import mirror_pending_batch

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mirror_futgg_card_images")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill card_image_stored_url for FUT.GG cards")
    p.add_argument("--limit", type=int, default=1000, help="max cards to process this run")
    p.add_argument("--batch-size", type=int, default=50, help="cards claimed per mirror_pending_batch() call")
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL is required")
        return 1

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    try:
        total_claimed = 0
        while total_claimed < args.limit:
            remaining = args.limit - total_claimed
            claimed = await mirror_pending_batch(pool, batch_size=min(args.batch_size, remaining))
            if claimed == 0:
                logger.info("No more pending cards - catalogue fully mirrored (or nothing eligible).")
                break
            total_claimed += claimed
            logger.info("Mirrored %d cards this batch (%d total this run)", claimed, total_claimed)
    finally:
        await pool.close()

    logger.info("Done. total_claimed=%d", total_claimed)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
