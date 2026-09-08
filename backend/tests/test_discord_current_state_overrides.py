from __future__ import annotations

import asyncio

from app.services import discord_current_state_overrides as override


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self):
        self.fetchval_calls = []
        self.fetch_calls = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        if "futgg_current_intelligence" in query:
            return 4
        return 99

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return ["row"]


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

    def acquire(self):
        return _Acquire(self.conn)


def test_wrapper_redirects_only_historical_live_count_query():
    raw = _Conn()
    conn = override._CurrentOpenCountConn(raw)

    value = run(conn.fetchval(
        "SELECT COUNT(*) FROM futgg_recommendation_snapshots WHERE true"
    ))
    assert value == 4
    query, args = raw.fetchval_calls[-1]
    assert "FROM futgg_current_intelligence c" in query
    assert "m.current_bin = c.current_bin" in query
    assert "c.expires_at IS NULL OR c.expires_at > NOW()" in query
    assert args == (override.DEFAULT_MAX_AGE_MINUTES,)

    ordinary = run(conn.fetchval("SELECT 123"))
    assert ordinary == 99
    assert raw.fetchval_calls[-1][0] == "SELECT 123"

    rows = run(conn.fetch("SELECT proof_history"))
    assert rows == ["row"]
    assert raw.fetch_calls == [("SELECT proof_history", ())]


def test_digest_wrapper_replaces_only_historical_candidate_query():
    raw = _Conn()
    conn = override._CurrentDigestConn(raw)

    rows = run(conn.fetch(
        "SELECT candidate.id FROM futgg_recommendation_snapshots candidate",
        180,
        24,
    ))
    assert rows == ["row"]
    query, args = raw.fetch_calls[-1]

    assert "FROM futgg_intelligence_transitions transition" in query
    assert "JOIN futgg_current_intelligence current" in query
    assert "JOIN futgg_market_snapshot market" in query
    assert "futgg_recommendation_snapshots" not in query
    assert "transition.new_signal IN ('buy', 'strong_buy')" in query
    # A BUY -> STRONG BUY quality change is not a newly qualifying trade.
    assert "transition.previous_signal NOT IN ('buy', 'strong_buy')" in query
    assert "market.current_bin = current.current_bin" in query
    assert "current.status NOT IN ('expired', 'invalidated')" in query
    assert "current.expires_at IS NULL OR current.expires_at > NOW()" in query
    assert args == (180, 24, override.DEFAULT_MAX_AGE_MINUTES)

    ordinary = run(conn.fetch("SELECT completed_history", "x"))
    assert ordinary == ["row"]
    assert raw.fetch_calls[-1] == ("SELECT completed_history", ("x",))


def test_installer_preserves_existing_functions_and_changes_only_connections(monkeypatch):
    from app.services import discord_integration as integration
    from app.services import discord_trading_feed as feed

    seen = {}

    async def fake_stats(conn):
        seen["overview_conn"] = conn
        open_count = await conn.fetchval(
            "SELECT COUNT(*) FROM futgg_recommendation_snapshots"
        )
        return {"open": open_count, "proof_untouched": True}

    async def fake_digest(pool, client=None):
        seen["digest_pool"] = pool
        seen["client"] = client
        return True

    monkeypatch.setattr(feed, "_free_overview_stats", fake_stats)
    monkeypatch.setattr(integration, "publish_opportunity_digest", fake_digest)
    override.install_discord_current_state_overrides()

    raw = _Conn()
    result = run(feed._free_overview_stats(raw))
    assert result == {"open": 4, "proof_untouched": True}
    assert isinstance(seen["overview_conn"], override._CurrentOpenCountConn)

    pool = _Pool(raw)
    assert run(integration.publish_opportunity_digest(pool, client="client")) is True
    assert isinstance(seen["digest_pool"], override._CurrentDigestPool)
    assert seen["client"] == "client"
