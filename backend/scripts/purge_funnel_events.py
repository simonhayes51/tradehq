#!/usr/bin/env python3
"""Delete funnel events past the retention horizon.

The same purge the scheduled worker runs monthly
(app/services/funnel_retention), as a command - for a first run against a
backlog, or to see the size of one before letting it go.

    # how many rows are expired, deleting nothing
    python -m scripts.purge_funnel_events --count

    # what a purge would delete, touching nothing
    python -m scripts.purge_funnel_events --dry-run

    # do it
    python -m scripts.purge_funnel_events

    # override the horizon for one run (does not change the default)
    python -m scripts.purge_funnel_events --days 730

Safe to run alongside the scheduled worker: both take the same advisory
lock, and whichever loses reports that another purge holds it.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


async def _main(args: argparse.Namespace) -> int:
    from app.db import get_core_pool
    from app.services import funnel_retention

    pool = await get_core_pool()

    if args.count:
        counts = await funnel_retention.count_expired(pool, retention_days=args.days)
        for key, value in counts.items():
            print(f"  {key:<16} {value}")
        return 0

    result = await funnel_retention.purge_with_lock(
        pool, retention_days=args.days, dry_run=args.dry_run,
    )
    if result is None:
        print("Another purge is already running. Nothing to do.")
        return 0

    if result.dry_run:
        print("DRY RUN - nothing was deleted\n")
    for key, value in result.as_dict().items():
        print(f"  {key:<18} {value}")
    return 0 if result.status in ("completed", "partial") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", action="store_true", help="report how many rows are expired, and exit")
    parser.add_argument("--dry-run", action="store_true", help="report what would be deleted")
    parser.add_argument("--days", type=int, default=None, help="retention horizon for this run")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
