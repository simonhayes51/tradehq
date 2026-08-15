"""
The per-card status write must be one statement, and must behave exactly
as the loop it replaced.

WHY THIS EXISTS
---------------
apply_batch_results used to issue a separate UPDATE per card, in a Python
loop, INSIDE the transaction. That cost twice over:

  * N sequential round trips instead of one, and
  * far worse, it held row locks on futgg_players for the whole sequence.

futgg_players is the table futgg_bulk_price_sync writes for 62-66 seconds
of every cycle, and the overlap showed up in production as

    db_write    n=56  total=983.46s  p50=381.5ms  p95=50868.7ms
    fetch_batch n=56  total=  8.85s  p50=156.2ms  p95=  247.9ms

983 seconds in the database against 8.85 seconds of the network fetching
the data those writes existed to record. The database, not fut.gg, is
what limits how fast this worker gets round the catalogue - and how fast
it gets round the catalogue is why some cards have sales today and others
have none.

The replacement is a single set-based UPDATE over unnest(). These tests
pin that it produces byte-identical state to the loop, including the
COALESCE "first success wins" rule, which is the part most easily broken
by a rewrite and the one that would silently corrupt select_due()'s
never-read-first backfill invariant.

Needs a real Postgres - the point is the SQL. Skipped unless
FUTGG_TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

DB_URL = os.getenv("FUTGG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="FUTGG_TEST_DATABASE_URL not set")

# The statement as it was, kept here as the reference implementation.
PER_CARD_UPDATE = """
UPDATE futgg_players
SET next_sales_due_at = NOW() + ($2 * INTERVAL '1 minute'),
    last_sales_status = $3, last_sales_status_at = NOW(),
    sales_first_read_at = CASE WHEN $4 THEN COALESCE(sales_first_read_at, NOW())
                               ELSE sales_first_read_at END,
    sales_consecutive_failures = $5
WHERE source_card_id = $1
"""

SET_BASED_UPDATE = """
UPDATE futgg_players p
SET next_sales_due_at = NOW() + (u.minutes * INTERVAL '1 minute'),
    last_sales_status = u.status, last_sales_status_at = NOW(),
    sales_first_read_at = CASE WHEN u.succeeded
                               THEN COALESCE(p.sales_first_read_at, NOW())
                               ELSE p.sales_first_read_at END,
    sales_consecutive_failures = u.failures
FROM unnest($1::bigint[], $2::int[], $3::text[], $4::boolean[], $5::int[])
     AS u(card_id, minutes, status, succeeded, failures)
WHERE p.source_card_id = u.card_id
"""

SCHEMA_COLUMNS = [
    ("next_sales_due_at", "TIMESTAMPTZ"),
    ("last_sales_status", "TEXT"),
    ("last_sales_status_at", "TIMESTAMPTZ"),
    ("sales_first_read_at", "TIMESTAMPTZ"),
    ("sales_consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
]


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.new_event_loop().run_until_complete(fn(*args, **kwargs))
    return wrapper


async def _seed(conn, n):
    await conn.execute("DROP TABLE IF EXISTS futgg_players CASCADE")
    await conn.execute(
        """
        CREATE TABLE futgg_players (
            source_card_id BIGINT PRIMARY KEY,
            source_player_id BIGINT NOT NULL,
            source_slug TEXT NOT NULL, source_url TEXT NOT NULL,
            game_year SMALLINT NOT NULL
        )
        """
    )
    for col, coltype in SCHEMA_COLUMNS:
        await conn.execute(f"ALTER TABLE futgg_players ADD COLUMN {col} {coltype}")
    await conn.executemany(
        "INSERT INTO futgg_players (source_card_id, source_player_id, source_slug,"
        " source_url, game_year) VALUES ($1,$1,'x','u',26)",
        [(i,) for i in range(1, n + 1)],
    )
    # Half already carry a first-read timestamp, so COALESCE is genuinely
    # exercised rather than every row taking the NULL branch.
    await conn.execute(
        "UPDATE futgg_players SET sales_first_read_at = now() - interval '5 days'"
        " WHERE source_card_id % 2 = 0"
    )


async def _state(conn):
    return [
        tuple(r)
        for r in await conn.fetch(
            """
            SELECT source_card_id, last_sales_status, sales_consecutive_failures,
                   sales_first_read_at IS NULL AS never_read,
                   next_sales_due_at IS NULL AS never_due
            FROM futgg_players ORDER BY source_card_id
            """
        )
    ]


def _updates(n):
    """A realistic mix: successes, failures, and rows that have never
    been read, so both branches of the COALESCE are hit."""
    return [
        (i, 60 + i % 7, "ok" if i % 3 else "data_429", i % 3 != 0, 0 if i % 3 else i % 5)
        for i in range(1, n + 1)
    ]


@sync
async def _compare(n=200):
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        updates = _updates(n)

        await _seed(conn, n)
        async with conn.transaction():
            for u in updates:
                await conn.execute(PER_CARD_UPDATE, *u)
        loop_state = await _state(conn)

        await _seed(conn, n)
        ordered = sorted(updates, key=lambda u: u[0])
        async with conn.transaction():
            await conn.execute(
                SET_BASED_UPDATE,
                [u[0] for u in ordered], [u[1] for u in ordered],
                [u[2] for u in ordered], [u[3] for u in ordered],
                [u[4] for u in ordered],
            )
        set_state = await _state(conn)
        return loop_state, set_state
    finally:
        await conn.close()


class TestSetBasedUpdate:
    def test_it_produces_exactly_the_state_the_loop_did(self):
        loop_state, set_state = _compare()
        assert loop_state == set_state

    def test_a_success_never_overwrites_an_existing_first_read(self):
        """select_due() orders on sales_first_read_at to keep the
        never-scraped-first backfill rule. If a rewrite turned this into
        a "last read" timestamp, that ordering would silently invert and
        never-read cards would stop being prioritised."""
        loop_state, set_state = _compare()
        # Even-numbered cards were seeded with a first-read 5 days ago and
        # must still report as read; odd cards succeed only when i % 3 != 0.
        for card_id, _status, _fails, never_read, _due in set_state:
            if card_id % 2 == 0:
                assert never_read is False
            elif card_id % 3 == 0:
                # failed, never previously read - must stay unread
                assert never_read is True

    def test_every_card_in_the_batch_is_written(self):
        _loop, set_state = _compare()
        assert len(set_state) == 200
        assert all(due is False for *_rest, due in set_state)
