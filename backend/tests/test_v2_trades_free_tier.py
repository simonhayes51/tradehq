# tests/test_v2_trades_free_tier.py
#
# Direct unit tests for app/routers/v2/trades.py's Free/Premium (£3/mo)
# gating - the REAL open-position tracker the live v2 Portfolio page
# (frontend/src/v2/pages/.../Club.jsx) actually calls, as distinct from
# app/routers/trades.py (the legacy /api/trades/ prefix v1 uses, covered
# separately in test_trades_free_tier.py). This router's POST /open never
# requires a sell price, so it is the one place an "open trade" can
# genuinely be created - the open_trade_limit check here has a live
# trigger, unlike the analogous check on the legacy router.
#
# Same "call the router function directly with a fake pool +
# asyncio.run()" pattern already established in this repo (no
# pytest-asyncio installed) - see test_watchlist_router.py/
# test_trades_free_tier.py.
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

import app.routers.v2.trades as v2_trades


def run_async(coro_fn):
    return asyncio.run(coro_fn())


class FakeConn:
    def __init__(self, open_count: int = 0, rows: Optional[List[Dict[str, Any]]] = None, summary: Optional[Dict[str, Any]] = None):
        self.open_count = open_count
        self.rows = rows or []
        self.summary = summary or {
            "open_positions": 0, "closed_trades": 0, "wins": 0, "total_profit": 0,
            "profit_today": 0, "profit_week": 0, "total_ea_tax": 0,
            "average_hold_hours": 0, "best_profit": None, "worst_profit": None,
            "average_roi": 0,
        }
        self.executed: List[Any] = []
        self.fetchval_calls: List[str] = []
        self.fetch_calls: List[Any] = []
        self.fetchrow_calls: List[Any] = []

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append(query)
        if "status='open'" in query:
            return self.open_count
        return 0

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        return [dict(r) for r in self.rows]

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        return dict(self.summary)

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "INSERT 1"

    # async context manager passthrough - the router does
    # `async with pool.acquire() as conn`.
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    def acquire(self):
        return self._conn


class FakeApp:
    def __init__(self, pool: FakePool):
        class State:
            pass
        self.state = State()
        self.state.pool = pool


class FakeRequest:
    def __init__(self, pool: FakePool):
        self.app = FakeApp(pool)


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


async def _async_return(value):
    return value


def _open_trade_payload(**overrides):
    defaults = dict(player="Haaland", buy=40000, quantity=1)
    defaults.update(overrides)
    return v2_trades.OpenTrade(**defaults)


# ---- Free open-trade limit on the REAL open-position endpoint ----------

def test_free_open_trade_limit_blocks_at_the_cap(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_free_ent(open_trade_limit=3)))
    conn = FakeConn(open_count=3)
    request = FakeRequest(FakePool(conn))

    async def run():
        return await v2_trades.open_trade(request, _open_trade_payload())

    with pytest.raises(HTTPException) as exc_info:
        run_async(run)
    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["error"] == "limit_reached"
    assert not conn.executed


def test_free_open_trade_limit_allows_below_the_cap(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_free_ent(open_trade_limit=3)))
    conn = FakeConn(open_count=2)
    request = FakeRequest(FakePool(conn))

    async def run():
        return await v2_trades.open_trade(request, _open_trade_payload())

    result = run_async(run)
    assert result["ok"] is True
    assert result["status"] == "open"
    assert conn.executed


def test_premium_has_no_open_trade_limit_on_v2_router(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_premium_ent()))
    conn = FakeConn(open_count=999)
    request = FakeRequest(FakePool(conn))

    async def run():
        return await v2_trades.open_trade(request, _open_trade_payload())

    result = run_async(run)
    assert result["ok"] is True
    assert not any("status='open'" in q for q in conn.fetchval_calls)


# ---- Free history/performance windowing ---------------------------------

def test_free_trade_history_is_windowed_and_labelled(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_free_ent(history_days=7)))
    conn = FakeConn(rows=[{"id": 1}])
    request = FakeRequest(FakePool(conn))

    result = run_async(lambda: v2_trades.trade_history(request))
    assert result["historyLimitedToDays"] == 7
    assert any("sold_at >=" in q for q, _a in conn.fetch_calls)


def test_premium_trade_history_is_not_windowed(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_premium_ent()))
    conn = FakeConn(rows=[{"id": 1}])
    request = FakeRequest(FakePool(conn))

    result = run_async(lambda: v2_trades.trade_history(request))
    assert result["historyLimitedToDays"] is None
    assert not any("sold_at >=" in q for q, _a in conn.fetch_calls)


def test_free_performance_omits_strategy_and_confidence_breakdowns(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_free_ent(history_days=7)))
    conn = FakeConn()
    request = FakeRequest(FakePool(conn))

    result = run_async(lambda: v2_trades.trading_performance(request))
    assert result["historyLimitedToDays"] == 7
    assert result["strategies"] == []
    assert result["confidence_accuracy"] == []


def test_premium_performance_includes_strategy_and_confidence_breakdowns(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_premium_ent()))
    conn = FakeConn(rows=[{"strategy": "quick_flip", "trades": 5, "wins": 3, "profit": 1000, "roi": 5.0}])
    request = FakeRequest(FakePool(conn))

    result = run_async(lambda: v2_trades.trading_performance(request))
    assert result["historyLimitedToDays"] is None
    assert result["strategies"] != []


def test_free_profit_timeline_is_clamped_to_history_days(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_free_ent(history_days=7)))
    conn = FakeConn(rows=[])
    request = FakeRequest(FakePool(conn))

    run_async(lambda: v2_trades.profit_timeline(request, days=30))
    # The clamped `days` value (7, not 30) must be what's actually bound
    # into the query.
    assert any(args and args[-1] == 7 for _q, args in conn.fetch_calls)


def test_premium_profit_timeline_is_not_clamped(monkeypatch):
    monkeypatch.setattr(v2_trades, "compute_entitlements", lambda req: _async_return(_premium_ent()))
    conn = FakeConn(rows=[])
    request = FakeRequest(FakePool(conn))

    run_async(lambda: v2_trades.profit_timeline(request, days=30))
    assert any(args and args[-1] == 30 for _q, args in conn.fetch_calls)
