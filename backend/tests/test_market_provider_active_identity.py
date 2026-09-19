import asyncio
from unittest.mock import AsyncMock, patch

import app.services.market_data_provider as market


class _Connection:
    def __init__(self):
        self.calls = []
        self.fetchrow_result = None

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return []

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return "UPDATE 1"


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


def test_get_player_uses_active_game_cycle_for_colliding_card_ids():
    conn = _Connection()
    conn.fetchrow_result = {"source_card_id": 246104, "game_year": 27}
    provider = market.FutggMarketDataProvider(_Pool(conn))

    with (
        patch.object(market, "active_game_year", return_value=27),
        patch.object(market, "resolve_snapshot_columns", new=AsyncMock(return_value="source_card_id, game_year")),
        patch.object(market, "_attach_sbc_reward_watch", new=AsyncMock()),
    ):
        result = asyncio.run(provider.get_player(246104))

    _, query, args = conn.calls[0]
    assert "source_card_id = $1 AND game_year = $2" in query
    assert args == (246104, 27)
    assert result["game_year"] == 27


def test_live_card_state_scopes_bin_and_sales_to_active_console_market():
    conn = _Connection()
    provider = market.FutggMarketDataProvider(_Pool(conn))

    with patch.object(market, "active_game_year", return_value=27):
        asyncio.run(provider.get_live_card_market_state(246104))

    _, query, args = conn.calls[0]
    assert query.count("game_year = $2") == 3
    assert query.count("platform = $3") == 2
    assert args == (246104, 27, "ps5")


def test_detail_chart_history_uses_the_same_active_market():
    conn = _Connection()
    provider = market.FutggMarketDataProvider(_Pool(conn))

    with patch.object(market, "active_game_year", return_value=27):
        asyncio.run(provider.get_sales_series(246104, days=7))
        asyncio.run(provider.get_price_history(246104, period="7d"))

    _, sales_query, sales_args = conn.calls[0]
    _, bin_query, bin_args = conn.calls[1]
    assert "game_year = $4" in sales_query and "platform = $5" in sales_query
    assert sales_args[3:] == (27, "ps5")
    assert "game_year = $3" in bin_query and "platform = $4" in bin_query
    assert bin_args[2:] == (27, "ps5")
