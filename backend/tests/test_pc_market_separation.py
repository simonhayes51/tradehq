import pytest
from unittest.mock import patch
from app.routers.v2.futgg_market import _pc_market_rows


class FakeConn:
    async def fetch(self, query, ids, game_year):
        assert "FROM futbot_pc_bin_history" in query
        assert "platform='pc'" in query
        assert "30 minutes" in query
        assert ids == [123]
        return []


class FakeAcquire:
    async def __aenter__(self):
        return FakeConn()

    async def __aexit__(self, *args):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class FakeProvider:
    _pool = FakePool()


@pytest.mark.asyncio
async def test_pc_market_does_not_fall_back_to_console_price():
    console = {"source_card_id": 123, "current_bin": 100000, "sales_count": 20}
    with patch("app.services.game_cycle.active_game_year", return_value=27):
        result = await _pc_market_rows(FakeProvider(), [console])
    assert result == []
    assert console["current_bin"] == 100000
