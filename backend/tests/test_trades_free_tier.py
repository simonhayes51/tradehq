# tests/test_trades_free_tier.py
#
# Direct unit tests for app/routers/trades.py's Free/Premium (£3/mo)
# gating: the open-trade limit on POST /api/trades/ (scenario 8) and the
# portfolio-history-days cap on GET /api/trades/. Same "call the router
# function directly with a fake db + asyncio.run()" pattern
# tests/test_watchlist_router.py already established for this repo (no
# pytest-asyncio installed).
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

import app.routers.trades as trades_router


def run_async(coro_fn):
    return asyncio.run(coro_fn())


class FakeTradesDB:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None, open_count: int = 0):
        self.rows = rows or []
        self.open_count = open_count
        self.executed: List[Any] = []
        self.fetchval_calls: List[str] = []
        self.fetch_calls: List[Any] = []

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append(query)
        if "status='open'" in query:
            return self.open_count
        if "COUNT(*) FROM trades" in query:
            return len(self.rows)
        return 0

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        return [dict(r) for r in self.rows]

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "INSERT 1"


def _free_ent(user_id: str = "free-user-1", open_trade_limit: int = 3, history_days: Optional[int] = 7) -> Dict[str, Any]:
    return {
        "user_id": user_id, "is_premium": False,
        "limits": {"open_trade_limit": open_trade_limit, "portfolio_history_days": history_days},
    }


def _premium_ent(user_id: str = "premium-user-1") -> Dict[str, Any]:
    return {
        "user_id": user_id, "is_premium": True,
        "limits": {"open_trade_limit": None, "portfolio_history_days": None},
    }


def _trade_payload(**overrides):
    defaults = dict(
        trade_id=1, player="Haaland", version="Standard", buy=40000, sell=50000,
        quantity=1, platform="ps", tag="", notes="",
    )
    defaults.update(overrides)
    return trades_router.TradeCreate(**defaults)


class FakeRequest:
    pass  # compute_entitlements is monkeypatched away, so this is never touched


# ---- Scenario 8: Free open-trade limit ----------------------------------

def test_free_open_trade_limit_blocks_creating_a_trade_at_the_cap(monkeypatch):
    monkeypatch.setattr(trades_router, "compute_entitlements", lambda req: _async_return(_free_ent(open_trade_limit=3)))
    db = FakeTradesDB(open_count=3)  # already at the cap

    async def run():
        return await trades_router.create_trade(FakeRequest(), _trade_payload(), db=db)

    with pytest.raises(HTTPException) as exc_info:
        run_async(run)
    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["error"] == "limit_reached"
    assert exc_info.value.detail["feature"] == "trade_journal"
    # And no INSERT should have been attempted once blocked.
    assert not db.executed


def test_free_open_trade_limit_allows_creating_below_the_cap(monkeypatch):
    monkeypatch.setattr(trades_router, "compute_entitlements", lambda req: _async_return(_free_ent(open_trade_limit=3)))
    db = FakeTradesDB(open_count=2)  # one below the cap

    async def run():
        return await trades_router.create_trade(FakeRequest(), _trade_payload(), db=db)

    result = run_async(run)
    assert result["ok"] is True
    assert db.executed  # the trade was actually inserted


def test_open_trade_limit_checked_against_status_open_specifically(monkeypatch):
    # The check must be scoped to status='open' - not every trade a user
    # has ever logged (mostly closed rows, per migration 032's design).
    monkeypatch.setattr(trades_router, "compute_entitlements", lambda req: _async_return(_free_ent(open_trade_limit=3)))
    db = FakeTradesDB(open_count=0)

    async def run():
        return await trades_router.create_trade(FakeRequest(), _trade_payload(), db=db)

    run_async(run)
    open_count_queries = [q for q in db.fetchval_calls if "status='open'" in q]
    assert open_count_queries, "create_trade must query the open-trade count before inserting"


def test_premium_has_no_open_trade_limit(monkeypatch):
    monkeypatch.setattr(trades_router, "compute_entitlements", lambda req: _async_return(_premium_ent()))
    db = FakeTradesDB(open_count=999)  # would fail any finite cap

    async def run():
        return await trades_router.create_trade(FakeRequest(), _trade_payload(), db=db)

    result = run_async(run)
    assert result["ok"] is True
    # Premium (limit=None) must never even query the open-trade count.
    assert not any("status='open'" in q for q in db.fetchval_calls)


# ---- Portfolio history capped to portfolio_history_days for Free --------

def test_free_trade_history_query_is_scoped_to_history_window(monkeypatch):
    monkeypatch.setattr(trades_router, "compute_entitlements", lambda req: _async_return(_free_ent(history_days=7)))
    db = FakeTradesDB(rows=[{"id": 1}])

    async def run():
        return await trades_router.get_my_trades(FakeRequest(), page=1, limit=100, db=db)

    result = run_async(run)
    assert result["historyLimitedToDays"] == 7
    assert any("timestamp >=" in q for q, _a in db.fetch_calls)


def test_premium_trade_history_is_not_windowed(monkeypatch):
    monkeypatch.setattr(trades_router, "compute_entitlements", lambda req: _async_return(_premium_ent()))
    db = FakeTradesDB(rows=[{"id": 1}])

    async def run():
        return await trades_router.get_my_trades(FakeRequest(), page=1, limit=100, db=db)

    result = run_async(run)
    assert result["historyLimitedToDays"] is None
    assert not any("timestamp >=" in q for q, _a in db.fetch_calls)


async def _async_return(value):
    return value
