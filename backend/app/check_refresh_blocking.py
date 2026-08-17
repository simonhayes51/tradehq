#!/usr/bin/env python
# scripts/check_refresh_blocking.py
#
# One-shot follow-up to check_snapshot_refresh.py: when the refresh
# advisory lock is held by an "active" session that isn't progressing,
# "active" alone doesn't distinguish real work from a query that is
# blocked waiting on some OTHER lock (wait_event_type/wait_event, and
# pg_blocking_pids(), tell them apart - state stays 'active' either way).
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg


async def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Set DATABASE_URL.", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pid, state, wait_event_type, wait_event,
                       now() - query_start AS running_for,
                       pg_blocking_pids(pid) AS blocked_by,
                       query
                FROM pg_stat_activity
                WHERE query ILIKE 'REFRESH MATERIALIZED VIEW CONCURRENTLY futgg_market_snapshot%'
                """
            )
        if not rows:
            print("No matching REFRESH query currently running.")
            return 0
        for r in rows:
            print(f"pid={r['pid']} state={r['state']} running_for={r['running_for']}")
            print(f"wait_event_type={r['wait_event_type']} wait_event={r['wait_event']}")
            print(f"blocked_by pids={list(r['blocked_by'])}")
            if r["wait_event_type"] == "Lock" or r["blocked_by"]:
                blockers = await conn.fetch(
                    """
                    SELECT pid, state, wait_event_type, wait_event,
                           now() - query_start AS running_for, query
                    FROM pg_stat_activity WHERE pid = ANY($1::int[])
                    """,
                    list(r["blocked_by"]),
                )
                print("\n>> BLOCKED on a lock. Blocking session(s):")
                for b in blockers:
                    print(f"  pid={b['pid']} state={b['state']} running_for={b['running_for']}")
                    print(f"  query: {(b['query'] or '')[:200]}")
            else:
                print("\n>> Not lock-blocked - this is genuinely doing I/O work, just slow.")
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
