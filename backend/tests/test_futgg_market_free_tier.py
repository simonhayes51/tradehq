# tests/test_futgg_market_free_tier.py
#
# HTTP-level tests for the Free/Premium (£3/mo) gating added to
# app/routers/v2/futgg_market.py's /opportunities and /trade-finder.
# Deliberately a SEPARATE test module from tests/test_futgg_market_router.py:
# that file's own _client() monkeypatches compute_entitlements to a fixed
# Premium user for its (unrelated, pre-existing) tests, so reusing it here
# would make it impossible to also exercise the Free path. This file wires
# its own compute_entitlements/get_core_pool fakes per test instead, same
# "no real DB" bar the rest of this repo's test suite sets, via FastAPI's
# dependency_overrides for the provider and direct module monkeypatching
# for the two call-sites (compute_entitlements, get_core_pool) that
# futgg_market.py calls directly rather than through Depends().
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.v2.futgg_market as futgg_market_module
from app.routers.v2.futgg_market import get_provider, router as futgg_router
from app.services.market_data_provider import PlayerFilters

AS_OF = datetime.now(timezone.utc)


def _row(**overrides) -> Dict[str, Any]:
    defaults = dict(
        source_card_id=1, source_player_id=100, source_slug="messi",
        source_url="http://x/1", game_year=26, name="Lionel Messi",
        rating=91, primary_position="RW", alternate_positions=["ST"],
        rarity="gold_rare", squad=None, price_tier="gold_rare",
        club="Inter Miami", league="MLS", nation="Argentina",
        club_source_id=1, league_source_id=1, nation_source_id=1,
        player_image_url=None, card_design_image_url=None, club_image_url=None,
        league_image_url=None, nation_image_url=None,
        is_active=True, is_tradeable=True, last_price_status="success",
        metadata_warnings=[], price_updated_at=AS_OF, next_price_due_at=None,
        current_bin=40000, price_range_low=39000, price_range_high=41000,
        bin_source_age_text="4 minutes ago", bin_captured_at=AS_OF - timedelta(minutes=4),
        sales_count=40, sales_median=55000, sales_trimmed_mean=55000,
        sales_low=48000, sales_high=60000, sales_stddev=800,
        latest_sale_price=55500, latest_sale_at=AS_OF - timedelta(minutes=2),
        sales_window_earliest_at=AS_OF - timedelta(hours=6),
        sales_window_latest_at=AS_OF - timedelta(minutes=2),
        sales_window_span_minutes=358.0, sales_dispersion_ratio=0.015,
        snapshot_computed_at=AS_OF,
    )
    defaults.update(overrides)
    return defaults


def _buy_row(card_id: int, current_bin: int, sales_median: int) -> Dict[str, Any]:
    # Reliable "buy"/"strong_buy" signal: current_bin comfortably below
    # sales_median/trimmed_mean, decent sales_count for liquidity - the
    # same shape test_futgg_market_router.py's own test_opportunities_
    # only_returns_buy_signals uses.
    return _row(
        source_card_id=card_id, current_bin=current_bin,
        price_range_low=current_bin - 1000, price_range_high=current_bin + 1000,
        sales_median=sales_median, sales_trimmed_mean=sales_median,
        latest_sale_price=sales_median,
    )


class FakeProvider:
    """Minimal fake - only the surface /opportunities and /trade-finder
    actually touch (both use the segmented-candidate scan, never
    search_players/count_players)."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = rows if rows is not None else [_row()]

    async def get_sales_by_ids(self, card_ids: List[int], limit_per_card: int = 50):
        return {
            cid: [{
                "sold_price": r["latest_sale_price"], "ea_tax": int(r["latest_sale_price"] * 0.05),
                "net_price": int(r["latest_sale_price"] * 0.95),
                "approximate_sold_at": r["latest_sale_at"],
                "source_age_text": "2 minutes ago", "source_age_seconds": 120,
            }]
            for r, cid in ((row, row["source_card_id"]) for row in self.rows)
            if cid in card_ids
        }

    async def select_candidates_by_segment(self):
        return {"all": list(self.rows)}


def _free_ent(user_id: str = "free-user-1", **limit_overrides) -> Dict[str, Any]:
    limits = {
        "watchlist_max": 3, "daily_opportunity_limit": 2, "daily_finder_limit": 3,
        "active_alert_limit": 1, "open_trade_limit": 3, "portfolio_history_days": 7,
        "ai_chat_daily_limit": 3,
    }
    limits.update(limit_overrides)
    return {
        "user_id": user_id, "plan": None, "tier": "free", "premium_until": None,
        "roles": [], "is_premium": False, "is_admin": False,
        "features": ["opportunity_feed", "trade_finder"],
        "limits": limits,
        "capabilities": {"is_premium": False},
    }


def _premium_ent(user_id: str = "premium-user-1") -> Dict[str, Any]:
    limits = {
        "watchlist_max": 25, "daily_opportunity_limit": None, "daily_finder_limit": None,
        "active_alert_limit": None, "open_trade_limit": None, "portfolio_history_days": None,
        "ai_chat_daily_limit": None,
    }
    return {
        "user_id": user_id, "plan": "pro", "tier": "pro", "premium_until": None,
        "roles": [], "is_premium": True, "is_admin": False,
        "features": ["opportunity_feed", "trade_finder"],
        "limits": limits,
        "capabilities": {"is_premium": True},
    }


class FakeUsagePool:
    def __init__(self):
        self.rows: Dict[tuple, int] = {}

    async def fetchrow(self, query: str, *args):
        user_id, usage_key, day = args
        key = (str(user_id), usage_key, day)
        self.rows[key] = self.rows.get(key, 0) + 1
        return {"count": self.rows[key]}


def _client(rows: List[Dict[str, Any]], ent: Dict[str, Any], usage_pool: Optional[FakeUsagePool] = None, monkeypatch=None):
    app = FastAPI()
    app.include_router(futgg_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: FakeProvider(rows)

    async def fake_compute_entitlements(_request):
        return dict(ent)

    monkeypatch.setattr(futgg_market_module, "compute_entitlements", fake_compute_entitlements)
    if usage_pool is not None:
        async def fake_get_core_pool():
            return usage_pool
        monkeypatch.setattr(futgg_market_module, "get_core_pool", fake_get_core_pool)
    return TestClient(app)


_MANY_BUY_ROWS = [
    _buy_row(101, 10_000, 16_000),   # low budget
    _buy_row(102, 30_000, 45_000),   # mid budget
    _buy_row(103, 150_000, 220_000),  # high budget
    _buy_row(104, 600_000, 900_000),  # premium budget
    _buy_row(105, 20_000, 35_000),   # mid budget
]


# ---- Scenario 1: Free gets no more than the daily opportunity limit -----

def test_free_opportunities_capped_at_daily_limit(monkeypatch):
    client = _client(_MANY_BUY_ROWS, _free_ent(), monkeypatch=monkeypatch)
    resp = client.get("/api/v2/opportunities")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2  # FREE_DAILY_OPPORTUNITY_LIMIT default
    assert body["locked"]["dailyLimit"] == 2
    assert body["locked"]["unlockedToday"] == 2
    # total still reflects every eligible opportunity, not just the
    # unlocked ones - Free must be able to see more exists.
    assert body["total"] == 5
    assert body["locked"]["previewCount"] == 3


# ---- Scenario 2: refreshing does not reveal a different Free set --------

def test_free_opportunities_are_the_same_set_on_repeated_calls(monkeypatch):
    client = _client(_MANY_BUY_ROWS, _free_ent(), monkeypatch=monkeypatch)
    first = client.get("/api/v2/opportunities").json()
    second = client.get("/api/v2/opportunities").json()
    third = client.get("/api/v2/opportunities?page=2").json()  # even with a different page param

    def card_ids(body):
        return sorted(item["source_card_id"] for item in body["items"])

    assert card_ids(first) == card_ids(second) == card_ids(third)


# ---- Scenario 3: Premium gets the full eligible feed, no cap/previews ---

def test_premium_opportunities_gets_everything_with_no_locked_block(monkeypatch):
    client = _client(_MANY_BUY_ROWS, _premium_ent(), monkeypatch=monkeypatch)
    resp = client.get("/api/v2/opportunities")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 5
    assert "locked" not in body
    assert body["total"] == 5


# ---- Scenario 4: locked previews never carry premium-only fields --------

def test_free_locked_previews_never_carry_priced_or_identity_fields(monkeypatch):
    client = _client(_MANY_BUY_ROWS, _free_ent(), monkeypatch=monkeypatch)
    resp = client.get("/api/v2/opportunities")
    body = resp.json()
    previews = body["locked"]["previews"]
    assert len(previews) == 3
    for preview in previews:
        assert set(preview.keys()) == {
            "locked", "budgetCategory", "riskCategory", "profitBand", "ageMinutes", "message",
        }
    # And the unlocked items DO carry the real, actionable fields Free's
    # allowance is meant to include - proving this isn't just an empty feed.
    for item in body["items"]:
        assert "name" in item
        assert "current_executable_buy" in item or "recommended_buy_max" in item


# ---- Scenario 5: Trade Finder daily search allowance -----------------

def test_free_trade_finder_within_allowance_returns_locked_results(monkeypatch):
    usage_pool = FakeUsagePool()
    client = _client(_MANY_BUY_ROWS, _free_ent(), usage_pool=usage_pool, monkeypatch=monkeypatch)
    resp = client.get("/api/v2/trade-finder")
    assert resp.status_code == 200
    body = resp.json()
    assert body["locked"] is True
    assert body["searchesRemainingToday"] == 2  # 3 - this 1st search
    assert body["dailySearchLimit"] == 3
    # No actionable prices in-allowance either - Finder's allowance
    # counts *searches*, it never unlocks real prices for Free.
    for item in body["items"]:
        assert "current_executable_buy" not in item
        assert "recommended_buy_max" not in item
        assert set(item.keys()) <= {
            "locked", "budgetCategory", "riskCategory", "profitBand", "ageMinutes", "message",
        }


def test_free_trade_finder_4th_call_same_day_is_rejected(monkeypatch):
    usage_pool = FakeUsagePool()
    client = _client(_MANY_BUY_ROWS, _free_ent(), usage_pool=usage_pool, monkeypatch=monkeypatch)
    for _ in range(3):
        resp = client.get("/api/v2/trade-finder")
        assert resp.status_code == 200

    fourth = client.get("/api/v2/trade-finder")
    assert fourth.status_code == 402
    detail = fourth.json()["detail"]
    assert detail["error"] == "daily_limit_reached"
    assert detail["feature"] == "trade_finder"
    assert detail["remaining"] == 0
    assert detail["limit"] == 3


def test_free_trade_finder_requires_login(monkeypatch):
    anon_ent = _free_ent(user_id=None)
    anon_ent["user_id"] = None
    usage_pool = FakeUsagePool()
    client = _client(_MANY_BUY_ROWS, anon_ent, usage_pool=usage_pool, monkeypatch=monkeypatch)
    resp = client.get("/api/v2/trade-finder")
    assert resp.status_code == 401


def test_premium_trade_finder_has_no_daily_cap_and_full_prices(monkeypatch):
    client = _client(_MANY_BUY_ROWS, _premium_ent(), monkeypatch=monkeypatch)
    for _ in range(5):  # more than Free's allowance would ever permit
        resp = client.get("/api/v2/trade-finder")
        assert resp.status_code == 200
        body = resp.json()
        assert "locked" not in body
        assert len(body["items"]) == 5
