#!/usr/bin/env python3
"""Reconcile Discord roles against billing entitlement, by hand.

The same sweep the scheduled worker runs (app/services/discord_reconcile),
exposed as a command for the cases a schedule does not cover: after a
Discord outage, after a bulk billing correction, or to see what a sweep
WOULD do before letting it.

    # what would change, touching nothing
    python -m scripts.reconcile_discord_roles --dry-run

    # do it
    python -m scripts.reconcile_discord_roles

    # a bounded pass, for a first run against a large member list
    python -m scripts.reconcile_discord_roles --limit 50

Safe to run while the scheduled worker is also running: both take the
same advisory lock, and whichever loses simply reports that another pass
holds it.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


async def _main(args: argparse.Namespace) -> int:
    from app.db import get_core_pool
    from app.services import discord_reconcile

    pool = await get_core_pool()

    if args.status:
        history = await discord_reconcile.last_run(pool)
        if not history:
            print("No reconciliation history. Has migration 078 been applied?")
            return 1
        for label, key in (("Most recent pass", "latest"), ("Last successful full sweep", "last_successful")):
            row = history.get(key)
            print(f"\n{label}:")
            if not row:
                print("  none recorded")
                continue
            for field in ("started_at", "finished_at", "status", "dry_run", "accounts_checked",
                          "roles_granted", "roles_revoked", "already_correct", "skipped",
                          "failed", "failure_reason"):
                print(f"  {field:<17} {row.get(field)}")
        return 0

    summary = await discord_reconcile.reconcile_with_lock(
        pool, dry_run=args.dry_run, limit_accounts=args.limit,
    )
    if summary is None:
        print("Another reconciliation pass is already running. Nothing to do.")
        return 0

    print("DRY RUN - nothing was changed\n" if summary.dry_run else "")
    for key, value in summary.as_dict().items():
        print(f"  {key:<17} {value}")
    # A pass that could not run is a failure of the command, not a
    # successful report of zero work.
    return 0 if summary.status == "completed" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching Discord")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many accounts")
    parser.add_argument("--status", action="store_true",
                        help="show the last pass and the last successful sweep, and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
