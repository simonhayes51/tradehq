#!/usr/bin/env python
"""Production SalesSync launcher with read-only information-value shadowing.

The only monkeypatch here wraps select_due(). It always returns the exact rows
selected by the original function unchanged. The shadow selector performs DB
reads and writes an observation row, but makes no FUT.GG request and never
consumes a trigger queue. Any shadow failure is swallowed and logged so sales
collection cannot fail because observability failed.
"""
from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

import futgg_sales_sync as sales
from futgg_sales_information_shadow import observe_after_due_selection

log = logging.getLogger("futgg_sales_shadow_worker")


async def run() -> None:
    original_select_due = sales.select_due
    watchlist_pool = None

    watchlist_dsn = (os.getenv("WATCHLIST_DATABASE_URL") or "").strip()
    core_dsn = (os.getenv("DATABASE_URL") or "").strip()
    if watchlist_dsn:
        try:
            # If both logical stores share one Postgres URL, a tiny separate pool
            # is still safe and avoids trying to reuse select_due's already-held
            # connection for an unrelated query while it is in scope.
            watchlist_pool = await asyncpg.create_pool(
                dsn=watchlist_dsn,
                min_size=1,
                max_size=2,
                command_timeout=20,
            )
            log.info("sales information shadow: watchlist/alert source enabled")
        except Exception:
            log.warning("sales information shadow: watchlist pool unavailable; continuing without alert source", exc_info=True)

    async def observed_select_due(conn):
        due = await original_select_due(conn)
        try:
            if watchlist_pool is None:
                await observe_after_due_selection(conn, due, watchlist_conn=None)
            else:
                async with watchlist_pool.acquire() as watchlist_conn:
                    await observe_after_due_selection(
                        conn,
                        due,
                        watchlist_conn=watchlist_conn,
                    )
        except Exception:
            log.warning(
                "sales information shadow failed; live due selection is unchanged",
                exc_info=True,
            )
        return due

    sales.select_due = observed_select_due
    log.info(
        "installed read-only information-value sales shadow; live scheduler/request budget unchanged"
    )

    try:
        await sales.run_forever()
    finally:
        sales.select_due = original_select_due
        if watchlist_pool is not None:
            await watchlist_pool.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
