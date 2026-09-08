from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.bin_valuation_loader import (
    SET_BASED_BATCH_THRESHOLD,
    _HOURLY_VALUATION_LATERAL_QUERY,
    _HOURLY_VALUATION_SET_QUERY,
    load_hourly_bin_valuation_observations,
)


class _Conn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn
        self.acquire_calls = 0

    def acquire(self):
        self.acquire_calls += 1
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_empty_ids_do_not_hit_database():
    conn = _Conn()
    pool = _Pool(conn)

    result = await load_hourly_bin_valuation_observations(pool, [])

    assert result == {}
    assert pool.acquire_calls == 0
    assert conn.calls == []


def test_both_queries_preserve_same_hourly_median_evidence_model():
    for sql in (_HOURLY_VALUATION_LATERAL_QUERY, _HOURLY_VALUATION_SET_QUERY):
        lowered = sql.lower()
        assert "percentile_cont(0.5)" in lowered
        assert "date_trunc('hour'" in lowered
        assert "futgg_bin_history" in lowered
        assert "make_interval(days => $2)" in lowered
        assert "bh.lowest_bin > 0" in lowered


@pytest.mark.asyncio
async def test_small_batches_use_lateral_plan():
    conn = _Conn()
    pool = _Pool(conn)
    ids = list(range(1, SET_BASED_BATCH_THRESHOLD))

    await load_hourly_bin_valuation_observations(pool, ids, days=7)

    query, args = conn.calls[0]
    assert query == _HOURLY_VALUATION_LATERAL_QUERY
    assert args == (ids, 7)


@pytest.mark.asyncio
async def test_scanner_sized_batches_use_set_based_plan_and_deduplicate_ids():
    conn = _Conn()
    pool = _Pool(conn)
    ids = list(range(1, SET_BASED_BATCH_THRESHOLD + 1)) + [1, 2, 3]

    await load_hourly_bin_valuation_observations(pool, ids, days=7)

    query, args = conn.calls[0]
    assert query == _HOURLY_VALUATION_SET_QUERY
    assert args == (list(range(1, SET_BASED_BATCH_THRESHOLD + 1)), 7)


@pytest.mark.asyncio
async def test_rows_are_grouped_by_card_and_prices_are_integral():
    at = datetime(2026, 9, 7, 12, 30, tzinfo=timezone.utc)
    conn = _Conn([
        {"source_card_id": 10, "lowest_bin": 1234.5, "captured_at": at},
        {"source_card_id": 10, "lowest_bin": 1300.0, "captured_at": at},
        {"source_card_id": 20, "lowest_bin": 2500.0, "captured_at": at},
    ])
    pool = _Pool(conn)

    result = await load_hourly_bin_valuation_observations(pool, [20, 10, 10], days=7)

    assert result == {
        10: [(1234, at), (1300, at)],
        20: [(2500, at)],
    }
    assert len(conn.calls) == 1
    _, args = conn.calls[0]
    assert args == ([10, 20], 7)
