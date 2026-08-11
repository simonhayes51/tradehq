"""
futgg_bulk_price_sync.py must record price_checked_at for EVERY card the
feed priced this cycle - including the ones whose price was unchanged and
therefore got no futgg_bin_history row.

WHY THIS EXISTS
---------------
Change-only writes made bin_captured_at (max(captured_at) per card) stop
meaning "we checked this card recently". For a stable card it advances
only when the per-card heartbeat falls due - up to 29 minutes at the
default settings - while the engine's freshness gate gives special-tier
cards 20 minutes. Every stable special-tier card was consequently
rejected as STALE_MARKET, and production logged
`futgg scan: evaluated=1344 ... recommendations=0` unchanged for hours.

price_checked_at (migration 052) is the clock that answers the question
the gate is actually asking. The rules it has to satisfy:

  * a card written this cycle gets it (trivially - same statement)
  * a card SKIPPED as unchanged gets it too - this is the whole point
  * a card the feed reports with no active market does NOT get it, so
    "the feed says there are no listings" can never be mistaken for
    "the price we hold was just confirmed"

Needs a real Postgres because the logic is in SQL, not in a pure
function. Skipped automatically unless FUTGG_TEST_DATABASE_URL is set,
the same opt-in pattern as test_futgg_price_outcomes.py.
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_bulk_price_sync as bulk  # noqa: E402

DB_URL = os.getenv("FUTGG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="FUTGG_TEST_DATABASE_URL not set"
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

WRITTEN = 101
SKIPPED = 202
NO_MARKET = 303


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.new_event_loop().run_until_complete(fn(*args, **kwargs))
    return wrapper


async def _seed(conn):
    await conn.execute(
        "TRUNCATE futgg_bin_history, futgg_sales_history, futgg_players CASCADE"
    )
    for card_id in (WRITTEN, SKIPPED, NO_MARKET):
        await conn.execute(
            """
            INSERT INTO futgg_players (
                source_card_id, source_player_id, source_slug, source_url,
                game_year, price_tier, is_tradeable
            ) VALUES ($1, $1, 'x', 'u', 26, 'special', TRUE)
            """,
            card_id,
        )


async def _apply_cycle(conn, records, candidates, no_market_ids, now):
    """The write path's UPDATE statements, verbatim in shape."""
    written_ids = {r[0] for r in records}
    if records:
        await conn.copy_records_to_table(
            "futgg_bin_history",
            records=records,
            columns=["source_card_id", "lowest_bin", "captured_at"],
        )
    await conn.execute(
        """
        UPDATE futgg_players p
        SET price_updated_at = $2, price_checked_at = $2,
            last_price_status = 'bulk_feed'
        WHERE p.source_card_id = ANY($1::bigint[])
        """,
        list(written_ids), now,
    )
    skipped_ids = [cid for cid, _ in candidates if cid not in written_ids]
    if skipped_ids:
        await conn.execute(
            """
            UPDATE futgg_players p
            SET price_checked_at = $2
            WHERE p.source_card_id = ANY($1::bigint[])
            """,
            skipped_ids, now,
        )
    if no_market_ids:
        await conn.execute(
            """
            UPDATE futgg_players p
            SET price_updated_at = $2, last_price_status = 'bulk_no_market'
            WHERE p.source_card_id = ANY($1::bigint[])
            """,
            no_market_ids, now,
        )


@pytest.fixture(autouse=True)
def _clean_state():
    bulk._last_written.clear()
    yield
    bulk._last_written.clear()


@sync
async def _run_two_cycles():
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        await _seed(conn)

        # Cycle 1: both priced cards are new, so both are written.
        candidates = [(WRITTEN, 10_000), (SKIPPED, 20_000)]
        records, _, _ = bulk.select_rows_to_write(candidates, NOW)
        await _apply_cycle(conn, records, candidates, [NO_MARKET], NOW)

        # Cycle 2, six minutes later. WRITTEN moved; SKIPPED did not and
        # is inside its heartbeat window, so it produces no history row.
        later = NOW + timedelta(minutes=6)
        candidates = [(WRITTEN, 11_000), (SKIPPED, 20_000)]
        records, changed, heartbeat = bulk.select_rows_to_write(candidates, later)
        assert [r[0] for r in records] == [WRITTEN]
        assert (changed, heartbeat) == (1, 0)
        await _apply_cycle(conn, records, candidates, [NO_MARKET], later)

        rows = {
            r["source_card_id"]: r
            for r in await conn.fetch(
                """
                SELECT p.source_card_id, p.price_checked_at, p.last_price_status,
                       max(b.captured_at) AS bin_captured_at
                FROM futgg_players p
                LEFT JOIN futgg_bin_history b USING (source_card_id)
                GROUP BY p.source_card_id, p.price_checked_at, p.last_price_status
                """
            )
        }
        return rows, later
    finally:
        await conn.close()


class TestPriceCheckedAt:
    def test_the_two_clocks_diverge_exactly_as_intended(self):
        rows, later = _run_two_cycles()

        # The card that moved: both clocks at the current cycle.
        assert rows[WRITTEN]["price_checked_at"] == later
        assert rows[WRITTEN]["bin_captured_at"] == later

        # The stable card: no new history row, so bin_captured_at is
        # stuck at cycle 1 - but we DID check it, and price_checked_at
        # says so. This divergence is the entire bug.
        assert rows[SKIPPED]["bin_captured_at"] == NOW
        assert rows[SKIPPED]["price_checked_at"] == later

        # The no-market card: never confirmed at a real price, so the
        # gate's clock must stay NULL. Bumping it here would make a card
        # with no listings at all read as freshly priced.
        assert rows[NO_MARKET]["price_checked_at"] is None
        assert rows[NO_MARKET]["last_price_status"] == "bulk_no_market"
