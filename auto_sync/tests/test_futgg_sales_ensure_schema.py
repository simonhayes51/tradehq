"""
ensure_schema() must not take locks it does not need, and must not treat
a lost lock race as a missing schema.

WHY THIS EXISTS
---------------
Production, 2026-08-14 15:05:22 - SalesSync died on startup:

    File "/app/futgg_sales_sync.py", line 1003, in ensure_schema
      await conn.execute(
    TimeoutError

Line 1003 was `ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS
next_sales_due_at` - a column that had existed for weeks. IF NOT EXISTS
makes a statement idempotent in effect but not in cost: Postgres still
takes an ACCESS EXCLUSIVE lock to discover there is nothing to do.
futgg_players is the busiest table in the system - futgg_bulk_price_sync
holds row locks on it for the 62-66 seconds its write stage takes, every
cycle - so the ALTER queued, waited out the pool's full 120s
command_timeout, and killed the worker.

Two compounding factors made it worse than a one-off: this worker runs
bounded (MAX_RUNTIME_MINUTES) and is restarted, so the DDL re-ran every
45 minutes forever; and a queued ACCESS EXCLUSIVE request blocks every
writer behind it for as long as it waits, so the failing worker degraded
the bulk sync too.

These tests need a real Postgres - lock behaviour is the entire subject,
and no mock reproduces it. Skipped unless FUTGG_TEST_DATABASE_URL is set,
the same opt-in pattern as test_futgg_price_outcomes.py.
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_sales_sync as ss  # noqa: E402

DB_URL = os.getenv("FUTGG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="FUTGG_TEST_DATABASE_URL not set")

PLAYERS_DDL = """
CREATE TABLE IF NOT EXISTS futgg_players (
    source_card_id BIGINT PRIMARY KEY,
    source_player_id BIGINT NOT NULL,
    source_slug TEXT NOT NULL,
    source_url TEXT NOT NULL,
    game_year SMALLINT NOT NULL,
    rating SMALLINT,
    price_tier TEXT NOT NULL DEFAULT 'bronze',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_tradeable BOOLEAN,
    price_updated_at TIMESTAMPTZ
)
"""


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.new_event_loop().run_until_complete(fn(*args, **kwargs))
    return wrapper


async def _fresh(conn):
    await conn.execute("DROP TABLE IF EXISTS futgg_sales_history CASCADE")
    await conn.execute("DROP TABLE IF EXISTS futgg_players CASCADE")
    await conn.execute(PLAYERS_DDL)
    await conn.execute(
        """
        INSERT INTO futgg_players (
            source_card_id, source_player_id, source_slug, source_url, game_year
        ) VALUES (1, 1, 'x', 'u', 26)
        """
    )


class _Blocker:
    """Holds a write lock on futgg_players, as the bulk price sync does
    for 62+ seconds of every cycle."""

    def __init__(self, conn):
        self._conn = conn
        self._tx = None

    async def __aenter__(self):
        self._tx = self._conn.transaction()
        await self._tx.start()
        await self._conn.execute("UPDATE futgg_players SET price_updated_at = now()")
        return self

    async def __aexit__(self, *exc):
        await self._tx.rollback()


@sync
async def _cold_then_warm():
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        await _fresh(conn)
        await ss.ensure_schema(conn)            # applies everything
        cols = await ss._existing_columns(conn, "futgg_players")
        idx = await ss._existing_indexes(conn)
        return cols, idx
    finally:
        await conn.close()


@sync
async def _warm_under_lock():
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    blocker_conn = await asyncpg.connect(DB_URL)
    try:
        await _fresh(conn)
        await ss.ensure_schema(conn)            # schema now current
        async with _Blocker(blocker_conn):
            started = time.monotonic()
            await ss.ensure_schema(conn)        # must not need any lock
            return time.monotonic() - started
    finally:
        await conn.close()
        await blocker_conn.close()


@sync
async def _missing_under_lock():
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    blocker_conn = await asyncpg.connect(DB_URL)
    try:
        await _fresh(conn)
        await ss.ensure_schema(conn)
        await conn.execute(
            "ALTER TABLE futgg_players DROP COLUMN sales_consecutive_failures"
        )
        async with _Blocker(blocker_conn):
            started = time.monotonic()
            with pytest.raises(RuntimeError) as excinfo:
                await ss.ensure_schema(conn)
            return time.monotonic() - started, str(excinfo.value)
    finally:
        await conn.close()
        await blocker_conn.close()


@sync
async def _lock_timeout_after():
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        await _fresh(conn)
        await ss.ensure_schema(conn)
        return await conn.fetchval("SHOW lock_timeout")
    finally:
        await conn.close()


class TestEnsureSchema:
    def test_a_cold_database_gets_every_object(self):
        cols, idx = _cold_then_warm()
        assert {
            "next_sales_due_at", "last_sales_status", "last_sales_status_at",
            "sales_first_read_at", "sales_consecutive_failures",
        } <= cols
        assert {
            "futgg_players_sales_due_idx", "futgg_players_sales_first_read_idx",
            "futgg_sales_history_card_sold_idx",
        } <= idx

    def test_a_current_schema_does_not_wait_on_a_busy_table(self):
        """The production failure, inverted. futgg_players is held under a
        write lock exactly as the bulk price sync holds it; ensure_schema
        must notice there is nothing to do WITHOUT asking for a lock, and
        return immediately rather than queueing an ACCESS EXCLUSIVE
        request behind the writer."""
        elapsed = _warm_under_lock()
        assert elapsed < 1.0, f"took {elapsed:.1f}s - it waited for a lock it did not need"

    def test_a_genuinely_missing_column_under_lock_still_aborts(self):
        """The fix must not turn a real schema problem into a silent one.
        A column that is actually absent, and cannot be added because the
        table is locked, is a legitimate startup failure - but it must
        fail at the lock timeout, not at the pool's 120s command_timeout,
        and it must say which object and why."""
        elapsed, message = _missing_under_lock()
        assert elapsed < 30, f"took {elapsed:.1f}s - lock_timeout did not apply"
        assert "sales_consecutive_failures" in message
        assert "lock" in message.lower()

    def test_the_short_lock_timeout_does_not_leak_to_the_worker(self):
        """The pool hands this connection straight on to the scraping
        loop. If a 5-second lock_timeout survived ensure_schema, ordinary
        writes would start failing under exactly the contention this
        worker already runs in."""
        assert _lock_timeout_after() in ("0", "0ms")
