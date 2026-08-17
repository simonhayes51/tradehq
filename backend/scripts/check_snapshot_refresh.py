#!/usr/bin/env python
# scripts/check_snapshot_refresh.py
#
# Follow-up to why_no_trades.py, for exactly the case it already
# diagnosed: "futgg_market_snapshot last refreshed N min ago" with N
# large. That message alone doesn't say WHY the refresher stopped -
# app/services/market_data_provider.py's refresher_loop() only refreshes
# when the source watermark (greatest(max(last_seen_at),
# max(price_updated_at)) on futgg_players) has moved AND it can win the
# REFRESH MATERIALIZED VIEW CONCURRENTLY advisory lock (key 7741011).
# Either half failing produces the identical "stale snapshot" symptom:
#
#   - the advisory lock is held elsewhere and never released (a backend
#     process crashed/was killed between acquiring it and the `finally`
#     unlock, or another instance is mid-refresh and just slow)
#   - the watermark itself isn't moving (price/player sync isn't writing
#     last_seen_at/price_updated_at, even though price_checked_at is -
#     these are deliberately different columns, see
#     auto_sync/futgg_bulk_price_sync.py's own comments on why)
#
# Read-only by default. Pass --force-refresh to actually run the same
# REFRESH MATERIALIZED VIEW CONCURRENTLY the refresher_loop itself runs,
# but ONLY if this reports the lock as free - if it's genuinely held by
# a live, in-progress refresh, forcing a second one would just block
# behind it, not fix anything.
#
#   python backend/scripts/check_snapshot_refresh.py
#   python backend/scripts/check_snapshot_refresh.py --force-refresh
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.market_data_provider import REFRESH_ADVISORY_LOCK_KEY  # noqa: E402

BAR = "=" * 72


def _h(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = ap.parse_args()

    if not args.database_url:
        print("Set DATABASE_URL (or pass --database-url).", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(args.database_url, min_size=1, max_size=2)
    try:
        _h("1. IS THE REFRESH ADVISORY LOCK STUCK?")
        async with pool.acquire() as conn:
            got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", REFRESH_ADVISORY_LOCK_KEY)
            if got:
                await conn.execute("SELECT pg_advisory_unlock($1)", REFRESH_ADVISORY_LOCK_KEY)
                print(f"  lock {REFRESH_ADVISORY_LOCK_KEY}: FREE - nothing is holding it right now.")
                lock_free = True
            else:
                print(f"  lock {REFRESH_ADVISORY_LOCK_KEY}: HELD by another session.")
                lock_free = False
                holders = await conn.fetch(
                    """
                    SELECT l.pid, a.state, a.query, a.query_start, a.state_change,
                           now() - a.query_start AS running_for
                    FROM pg_locks l
                    JOIN pg_stat_activity a ON a.pid = l.pid
                    WHERE l.locktype = 'advisory'
                      AND l.objid = $1
                    """,
                    REFRESH_ADVISORY_LOCK_KEY,
                )
                if not holders:
                    print(
                        "  >> pg_try_advisory_lock failed but pg_locks shows no holder - "
                        "the lock was likely released between the two queries (a live, "
                        "fast refresh). Not necessarily stuck; re-run this in a minute."
                    )
                for row in holders:
                    print(f"    pid={row['pid']} state={row['state']} running_for={row['running_for']}")
                    print(f"    query: {(row['query'] or '')[:200]}")
                    if row["state"] == "idle" and row["query"] and "REFRESH MATERIALIZED VIEW" in row["query"]:
                        print(
                            "    >> This connection is IDLE while still holding the refresh "
                            "lock - it finished (or crashed) without releasing it. That is "
                            "the stuck-lock case: every future refresh attempt will keep "
                            "losing pg_try_advisory_lock to a session that isn't doing "
                            "anything anymore. Terminating this pid "
                            f"(SELECT pg_terminate_backend({row['pid']});) releases the "
                            "lock - Postgres always releases session-level advisory locks "
                            "when the session ends, even on a hard kill."
                        )

        _h("2. IS THE SOURCE WATERMARK MOVING?")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT greatest(max(last_seen_at), max(price_updated_at)) AS watermark,
                       max(price_updated_at) AS max_price_updated_at,
                       max(last_seen_at) AS max_last_seen_at,
                       max(price_checked_at) AS max_price_checked_at
                FROM futgg_players
                """
            )
            snap_age_min = await conn.fetchval(
                "SELECT EXTRACT(EPOCH FROM (now() - max(snapshot_computed_at))) / 60.0"
                " FROM futgg_market_snapshot"
            )
        print(f"  watermark (refresher_loop's own trigger)      : {row['watermark']}")
        print(f"  max(price_updated_at)                          : {row['max_price_updated_at']}")
        print(f"  max(last_seen_at)                               : {row['max_last_seen_at']}")
        print(f"  max(price_checked_at)  (informational only)    : {row['max_price_checked_at']}")
        print(f"  futgg_market_snapshot.snapshot_computed_at age : {snap_age_min:.1f} min ago" if snap_age_min else "  snapshot_computed_at: none")

        if row["max_price_checked_at"] and row["max_price_updated_at"]:
            drift_min = (row["max_price_checked_at"] - row["max_price_updated_at"]).total_seconds() / 60.0
            if drift_min > 15:
                print(
                    f"\n  >> price_checked_at is {drift_min:.0f} min fresher than "
                    "price_updated_at. That means recent price-sync cycles are hitting "
                    "the 'unchanged, skip' path in futgg_bulk_price_sync.py (which only "
                    "bumps price_checked_at) rather than the 'written' path (which bumps "
                    "both) - so the watermark this refresher waits on may not actually be "
                    "advancing even though prices ARE being checked."
                )

        if not lock_free:
            print("\n  >> Lock is held - see section 1 for what's holding it and whether it")
            print("     looks stuck. Not attempting a refresh.")
            return 0

        if args.force_refresh:
            _h("3. FORCING ONE REFRESH (lock was free)")
            t0 = time.monotonic()
            async with pool.acquire() as conn:
                got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", REFRESH_ADVISORY_LOCK_KEY)
                if not got:
                    print("  Lost the race for the lock to another session between checks - not refreshing.")
                    return 0
                try:
                    await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY futgg_market_snapshot")
                    print(f"  Refreshed successfully in {time.monotonic() - t0:.1f}s.")
                finally:
                    await conn.execute("SELECT pg_advisory_unlock($1)", REFRESH_ADVISORY_LOCK_KEY)
        else:
            print("\n  Lock is free. Pass --force-refresh to actually run the refresh now")
            print("  (same REFRESH MATERIALIZED VIEW CONCURRENTLY the backend's own")
            print("  refresher_loop runs) instead of waiting for it to notice on its own.")

        print(f"\n{BAR}\n{'No changes made.' if not args.force_refresh else 'Refresh was the only write performed.'}\n{BAR}")
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
