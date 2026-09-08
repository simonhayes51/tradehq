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
        return ["proof-row"]


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
    assert rows == ["proof-row"]
    assert raw.fetch_calls == [("SELECT proof_history", ())]


def test_installer_preserves_original_proof_function_and_changes_only_connection(monkeypatch):
    from app.services import discord_trading_feed as feed

    seen = {}

    async def fake_stats(conn):
        seen["conn"] = conn
        # Simulate the one current/live count inside the otherwise historical
        # proof calculation.
        open_count = await conn.fetchval(
            "SELECT COUNT(*) FROM futgg_recommendation_snapshots"
        )
        return {"open": open_count, "proof_untouched": True}

    monkeypatch.setattr(feed, "_free_overview_stats", fake_stats)
    override.install_discord_current_state_overrides()

    raw = _Conn()
    result = run(feed._free_overview_stats(raw))
    assert result == {"open": 4, "proof_untouched": True}
    assert isinstance(seen["conn"], override._CurrentOpenCountConn)
