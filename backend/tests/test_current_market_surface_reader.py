from __future__ import annotations

import asyncio
import inspect

from app.services import current_intelligence_surfaces as current
from app.services import snapshot_market_surfaces as surface


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.query = None
        self.args = None

    async def fetch(self, query, *args):
        self.query = query
        self.args = args
        return self.rows


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, rows=None):
        self.conn = _Conn(rows)

    def acquire(self):
        return _Acquire(self.conn)


class _Provider:
    def __init__(self, rows=None):
        self._pool = _Pool(rows)


def test_live_market_surface_reader_uses_current_state_not_recommendation_audit_table():
    provider = _Provider()
    assert run(surface.load_market_surface_pairs(provider, max_age_minutes=30)) == []

    query = provider._pool.conn.query
    assert "FROM futgg_current_intelligence c" in query
    assert "futgg_recommendation_snapshots" not in query
    assert provider._pool.conn.args == (30,)


def test_live_market_surface_reader_fails_closed_on_bin_age_and_expiry():
    query = surface._QUERY
    assert "m.current_bin = c.current_bin" in query
    assert "c.evaluated_at >= now() - ($1::int * interval '1 minute')" in query
    assert "(c.expires_at IS NULL OR c.expires_at > now())" in query
    assert "c.status NOT IN ('expired', 'invalidated')" in query
    assert "c.signal IN ('buy', 'strong_buy', 'watch')" in query


def test_current_intelligence_reader_can_require_unexpired_without_changing_dashboard_default():
    provider = _Provider()
    run(current.load_current_intelligence_pairs(
        provider,
        card_ids=[3, 1, 3],
        max_age_minutes=45,
        require_unexpired=True,
    ))

    query = provider._pool.conn.query
    assert "c.source_card_id = ANY($1::bigint[])" in query
    assert "c.evaluated_at >= now() - ($2::int * interval '1 minute')" in query
    assert "(c.expires_at IS NULL OR c.expires_at > now())" in query
    assert provider._pool.conn.args == ([1, 3], 45)


def test_current_reader_itself_never_queries_historical_recommendation_snapshots():
    source = inspect.getsource(current.load_current_intelligence_pairs)
    base_query = current._BASE_QUERY
    assert "futgg_recommendation_snapshots" not in source
    assert "futgg_recommendation_snapshots" not in base_query
    assert "m.current_bin = c.current_bin" in base_query
