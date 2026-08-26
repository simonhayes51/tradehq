# tests/test_investments_free_tier.py
#
# HTTP-level tests for the Free/Premium (£3/mo) gating GET /investments/
# signals now has (app/routers/v2/investments.py::list_signals) - this
# endpoint had zero auth or gating at all before this commit. Own fixtures
# (FastAPI + dependency_overrides for get_core_pool, module monkeypatch
# for compute_entitlements), same pattern tests/test_futgg_market_free_
# tier.py uses for its sibling router.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.v2.investments as investments_module
from app.routers.v2.investments import get_core_pool, router as investments_router

AS_OF = datetime.now(timezone.utc)


def _signal_row(signal_id: int, max_buy: float = 40_000, roi_pct: float = 15.0) -> Dict[str, Any]:
    return {
        "id": signal_id, "source_card_id": signal_id, "name": f"Player {signal_id}",
        "rating": 85, "primary_position": "ST", "current_bin": max_buy,
        "strategy_key": "cohort_dip", "cohort_key": "gold_rare_85_89", "status": "active",
        "max_buy": max_buy, "target_price": max_buy * (1 + roi_pct / 100),
        "expected_roi_pct": roi_pct, "expected_hold_hours_min": 6, "expected_hold_hours_max": 24,
        "confidence": 0.7, "liquidity": "medium", "investability": 0.6,
        "rationale": "Historically undervalued vs cohort median.", "risk_flags": [],
        "opened_at": AS_OF - timedelta(hours=2), "expires_at": AS_OF + timedelta(hours=22),
        "max_hold_at": AS_OF + timedelta(hours=24), "closed_at": None, "close_price": None,
        "outcome": None, "after_tax_profit": None, "after_tax_roi_pct": None, "exit_reason": None,
    }


class FakeConn:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows

    async def fetch(self, query: str, *args):
        return [dict(r) for r in self.rows]


class FakeAcquireCtx:
    def __init__(self, conn: FakeConn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.conn = FakeConn(rows)

    def acquire(self):
        return FakeAcquireCtx(self.conn)


def _free_ent(user_id: str = "free-user-1") -> Dict[str, Any]:
    return {
        "user_id": user_id, "tier": "free", "is_premium": False,
        "limits": {"daily_opportunity_limit": 2}, "capabilities": {"is_premium": False},
    }


def _premium_ent(user_id: str = "premium-user-1") -> Dict[str, Any]:
    return {
        "user_id": user_id, "tier": "pro", "is_premium": True,
        "limits": {"daily_opportunity_limit": None}, "capabilities": {"is_premium": True},
    }


def _client(rows: List[Dict[str, Any]], ent: Dict[str, Any], monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(investments_router, prefix="/api/v2")
    app.dependency_overrides[get_core_pool] = lambda: FakePool(rows)

    async def fake_compute_entitlements(_request):
        return dict(ent)

    monkeypatch.setattr(investments_module, "compute_entitlements", fake_compute_entitlements)
    return TestClient(app)


_FIVE_SIGNALS = [_signal_row(i, max_buy=10_000 * i, roi_pct=10 + i) for i in range(1, 6)]


# ---- Scenario 3: Premium gets the full eligible feed --------------------

def test_premium_gets_the_full_signal_feed(monkeypatch):
    client = _client(_FIVE_SIGNALS, _premium_ent(), monkeypatch)
    resp = client.get("/api/v2/investments/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["signals"]) == 5
    assert "locked" not in body
    for signal in body["signals"]:
        assert signal["max_buy"] is not None
        assert signal["name"] is not None


# ---- Free gets exactly one preview signal, deterministically ------------

def test_free_gets_exactly_one_preview_signal(monkeypatch):
    client = _client(_FIVE_SIGNALS, _free_ent(), monkeypatch)
    resp = client.get("/api/v2/investments/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["signals"]) == 1
    assert body["locked"]["investFeed"] is True
    assert body["locked"]["unlockedToday"] == 1
    assert body["locked"]["previewCount"] == 4


def test_free_preview_signal_is_the_same_on_repeated_calls(monkeypatch):
    client = _client(_FIVE_SIGNALS, _free_ent(), monkeypatch)
    first = client.get("/api/v2/investments/signals").json()
    second = client.get("/api/v2/investments/signals").json()
    assert [s["card_id"] for s in first["signals"]] == [s["card_id"] for s in second["signals"]]


# ---- Scenario 4: locked previews carry no premium-only fields ------------

def test_free_locked_signal_previews_have_only_the_allowed_fields(monkeypatch):
    client = _client(_FIVE_SIGNALS, _free_ent(), monkeypatch)
    resp = client.get("/api/v2/investments/signals")
    body = resp.json()
    previews = body["locked"]["previews"]
    assert len(previews) == 4
    for preview in previews:
        assert set(preview.keys()) == {
            "locked", "budgetCategory", "riskCategory", "profitBand", "ageMinutes", "message",
        }
    serialized = str(previews)
    for marker in ("max_buy", "target_price", "Player 1", "Player 2", "Player 3"):
        assert marker not in serialized
