# tests/test_futgg_scan_cursor.py
#
# futgg_candidate_segments.py's Segment.refresh_minutes always documented
# itself as "how often a card in this segment should be re-evaluated", but
# select_candidates_by_segment() never actually applied it - every scan
# pass re-selected the same deterministic top-N rows per segment forever,
# leaving ~92% of the catalogue never evaluated at all (see migration 083's
# comment for the production symptom this caused). These tests cover the
# fix: a NOT EXISTS filter against futgg_scan_cursor, and the write path
# (record_scan_cursor / futgg_scanner.run_scan) that keeps that table
# populated for every card a pass evaluates, not just the ones that
# produced a persisted recommendation.
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import app.services.market_data_provider as mdp
from app.services.market_data_provider import FutggMarketDataProvider


def run(coro):
    return asyncio.run(coro)


AS_OF = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_module_caches():
    # Both are resolved-once-per-process caches; tests must not leak state
    # between each other or into unrelated test modules.
    mdp._scan_cursor_table_present = None
    old_columns, old_checked_at = mdp._snapshot_columns_sql, mdp._snapshot_has_checked_at
    yield
    mdp._scan_cursor_table_present = None
    mdp._snapshot_columns_sql, mdp._snapshot_has_checked_at = old_columns, old_checked_at


_ALL_SNAPSHOT_COLUMNS = [
    c.strip() for c in mdp._SNAPSHOT_COLUMNS.replace("\n", " ").split(",") if c.strip()
]


class FakeConn:
    def __init__(self, *, cursor_table_exists, snapshot_columns=None):
        self.cursor_table_exists = cursor_table_exists
        self.snapshot_columns = snapshot_columns or _ALL_SNAPSHOT_COLUMNS
        self.fetch_calls = []
        self.executemany_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if "pg_attribute" in sql:
            return [{"attname": c} for c in self.snapshot_columns]
        return []

    async def fetchval(self, sql, *args):
        if "to_regclass" in sql:
            return "futgg_scan_cursor" if self.cursor_table_exists else None
        return None

    async def executemany(self, sql, args_list):
        self.executemany_calls.append((sql, list(args_list)))


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return Ctx()


class TestSelectCandidatesAppliesRecencyFilter:
    def test_default_never_hides_cards_from_live_callers(self):
        # The default (no kwarg - exactly what _segmented_candidates() in
        # futgg_market.py calls, which backs /opportunities, /trade-finder,
        # /dashboard's free picks, and the Discord live-opportunity feed)
        # must NEVER apply the recency filter, even when the cursor table
        # exists - a live request needs the best current signal regardless
        # of whether the background scanner touched the card 10 minutes
        # ago. This is the regression this fix almost shipped.
        conn = FakeConn(cursor_table_exists=True)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.select_candidates_by_segment())
        segment_queries = [sql for sql, args in conn.fetch_calls if "is_tradeable IS DISTINCT FROM FALSE" in sql]
        assert segment_queries, "expected at least one per-segment query"
        for sql in segment_queries:
            assert "futgg_scan_cursor" not in sql

    def test_opt_in_includes_not_exists_clause_when_cursor_table_present(self):
        conn = FakeConn(cursor_table_exists=True)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.select_candidates_by_segment(exclude_recently_evaluated=True))
        segment_queries = [sql for sql, args in conn.fetch_calls if "is_tradeable IS DISTINCT FROM FALSE" in sql]
        assert segment_queries, "expected at least one per-segment query"
        for sql in segment_queries:
            assert "NOT EXISTS" in sql
            assert "futgg_scan_cursor" in sql

    def test_opt_in_omits_recency_filter_when_cursor_table_absent(self):
        conn = FakeConn(cursor_table_exists=False)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.select_candidates_by_segment(exclude_recently_evaluated=True))
        segment_queries = [sql for sql, args in conn.fetch_calls if "is_tradeable IS DISTINCT FROM FALSE" in sql]
        assert segment_queries
        for sql in segment_queries:
            assert "futgg_scan_cursor" not in sql

    def test_each_segment_uses_its_own_refresh_minutes(self):
        from app.services.futgg_candidate_segments import SEGMENTS

        conn = FakeConn(cursor_table_exists=True)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.select_candidates_by_segment(exclude_recently_evaluated=True))
        segment_queries = [sql for sql, args in conn.fetch_calls if "is_tradeable IS DISTINCT FROM FALSE" in sql]
        refresh_minutes_seen = {s.refresh_minutes for s in SEGMENTS}
        for minutes in refresh_minutes_seen:
            assert any(f"interval '{minutes} minutes'" in sql for sql in segment_queries)


class TestSelectCandidatesExcludesSilverAndBronze:
    # "Silver and Bronze cards don't need to be evaluated - the profit is
    # pointless" - a standing product rule, not a speed hack, though it
    # also materially shrinks evaluate_card()'s per-pass workload since
    # silver/bronze make up a large share of the total card pool.
    def test_every_segment_query_excludes_silver_and_bronze(self):
        conn = FakeConn(cursor_table_exists=False)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.select_candidates_by_segment())
        segment_queries = [sql for sql, args in conn.fetch_calls if "is_tradeable IS DISTINCT FROM FALSE" in sql]
        assert segment_queries, "expected at least one per-segment query"
        for sql in segment_queries:
            assert "price_tier NOT IN" in sql
            assert "'silver'" in sql
            assert "'bronze'" in sql

    def test_exclusion_applies_regardless_of_the_recency_filter(self):
        # Must hold for both the scanner's own opt-in call and the
        # default live-caller path - this is a pool-composition rule,
        # unrelated to the rotation feature.
        conn = FakeConn(cursor_table_exists=True)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.select_candidates_by_segment(exclude_recently_evaluated=True))
        segment_queries = [sql for sql, args in conn.fetch_calls if "is_tradeable IS DISTINCT FROM FALSE" in sql]
        for sql in segment_queries:
            assert "price_tier NOT IN" in sql


class TestRecordScanCursor:
    def test_upserts_every_card_id_when_table_present(self):
        conn = FakeConn(cursor_table_exists=True)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.record_scan_cursor([1, 2, 3], AS_OF))
        assert len(conn.executemany_calls) == 1
        sql, rows = conn.executemany_calls[0]
        assert "ON CONFLICT (source_card_id)" in sql
        assert sorted(r[0] for r in rows) == [1, 2, 3]
        assert all(r[1] == AS_OF for r in rows)

    def test_no_op_for_empty_card_ids(self):
        conn = FakeConn(cursor_table_exists=True)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.record_scan_cursor([], AS_OF))
        assert conn.executemany_calls == []

    def test_no_op_when_table_absent(self):
        conn = FakeConn(cursor_table_exists=False)
        provider = FutggMarketDataProvider(FakePool(conn))
        run(provider.record_scan_cursor([1, 2], AS_OF))
        assert conn.executemany_calls == []
