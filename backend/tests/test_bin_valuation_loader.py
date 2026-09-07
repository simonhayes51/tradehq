from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.bin_valuation_loader import (
    _HOURLY_VALUATION_QUERY,
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


def test_query_reduces_raw_history_to_hourly_medians():
    sql = _HOURLY_VALUATION_QUERY.lower()
    assert "percentile_cont(0.5)" in sql
    assert "date_trunc('hour'" in sql
    assert "futgg_bin_history" in sql
    assert "make_interval(days => $2)" in sql


@pytest.mark.asyncio
async def test_rows_are_grouped_by_card_and_prices_are_integral():
    at = datetime(2026, 9, 7, 12, 30, tzinfo=timezone.utc)
    conn = _Conn([
        {"source_card_id": 10, "lowest_bin": 1234.5, "captured_at": at},
        {"source_card_id": 10, "lowest_bin": 1300.0, "captured_at": at},
        {"source_card_id": 20, "lowest_bin": 2500.0, "captured_at": at},
    ])
    pool = _Pool(conn)

    result = await load_hourly_bin_valuation_observations(pool, [10, 20], days=7)

    assert result == {
        10: [(1234, at), (1300, at)],
        20: [(2500, at)],
    }
    assert len(conn.calls) == 1
    _, args = conn.calls[0]
    assert args == ([10, 20], 7)
