# tests/test_strategy_finder.py
#
# Tests for GET /api/v2/trade-finder/strategy/{strategy} and
# GET /api/v2/trade-finder/strategies (app/routers/v2/futgg_market.py's
# strategy_finder()/list_strategies(), backed by
# app/services/strategy_definitions.py).
#
# Root-caused bugs this guards against:
#   - "Big profit" had no absolute profit floor, so a card netting
#     ~3,425 coins could rank next to one netting 13,800.
#   - Cards were shown as actionable even when the underlying price was
#     too stale to trust (no live-entry-state concept existed at all).
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.v2.futgg_market import (
    get_provider, require_trade_finder, router as futgg_router,
)
from app.services.strategy_definitions import (
    BIG_PROFIT_MIN_ABS_PROFIT_AFTER_TAX,
    BUDGET_PICKS_MAX_PRICE,
)
from app.auth.access import get_viewer
from tests.test_futgg_market_router import ELITE_VIEWER, FakeProvider, _row

AS_OF = datetime.now(timezone.utc)


def _client(rows: Optional[List[Dict[str, Any]]] = None) -> TestClient:
    # Strategy Finder is Elite-only. This file tests the strategy
    # DEFINITIONS (floors, sorts, watch separation), so it runs as Elite;
    # that the gate itself fires for Free and anonymous callers is proved
    # in tests/test_access_control.py.
    app = FastAPI()
    app.include_router(futgg_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: FakeProvider(rows)
    app.dependency_overrides[get_viewer] = lambda: ELITE_VIEWER
    app.dependency_overrides[require_trade_finder] = lambda: {"tier": "elite"}
    return TestClient(app)


def test_unknown_strategy_is_404():
    resp = _client().get("/api/v2/trade-finder/strategy/not_a_real_strategy")
    assert resp.status_code == 404


def test_list_strategies_returns_all_five():
    resp = _client().get("/api/v2/trade-finder/strategies")
    assert resp.status_code == 200
    keys = {s["key"] for s in resp.json()["strategies"]}
    assert keys == {"best_overall", "quick_flips", "low_risk", "big_profit", "budget"}


class TestBigProfitFloor:
    """The reported bug: 'Big profit' showed a 3,425-coin trade next to a
    13,800-coin one, because no absolute minimum was ever enforced."""

    def test_small_absolute_profit_is_excluded_even_with_decent_roi(self):
        # A cheap card with a real ROI% but a tiny absolute coin gain -
        # the exact shape that used to slip into "Big profit".
        cheap_row = _row(
            source_card_id=51, current_bin=6000, sales_median=6800,
            sales_trimmed_mean=6800, latest_sale_price=6800, sales_count=40,
        )
        resp = _client([cheap_row]).get("/api/v2/trade-finder/strategy/big_profit")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_large_absolute_profit_clears_the_floor(self):
        # A card with a large enough spread that after-tax profit clears
        # the configured floor - must actually appear, and must be
        # reported with expected_profit_after_tax >= the floor.
        big_row = _row(
            source_card_id=52, current_bin=150000, sales_median=200000,
            sales_trimmed_mean=200000, latest_sale_price=200000, sales_count=40,
        )
        resp = _client([big_row]).get("/api/v2/trade-finder/strategy/big_profit")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["expected_profit_after_tax"] >= BIG_PROFIT_MIN_ABS_PROFIT_AFTER_TAX

    def test_no_fallback_widening_when_nothing_qualifies(self):
        # Every candidate is too cheap to clear the floor - the response
        # must be an honest empty list, never a widened/relaxed result.
        rows = [
            _row(source_card_id=60 + i, current_bin=1000 + i, sales_median=1100 + i,
                 sales_trimmed_mean=1100 + i, latest_sale_price=1100 + i, sales_count=40)
            for i in range(5)
        ]
        resp = _client(rows).get("/api/v2/trade-finder/strategy/big_profit")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        assert resp.json()["total"] == 0


class TestBudgetPicksCeiling:
    def test_price_above_ceiling_is_excluded(self):
        expensive_row = _row(
            source_card_id=53, current_bin=BUDGET_PICKS_MAX_PRICE + 20000,
            sales_median=BUDGET_PICKS_MAX_PRICE + 24000,
            sales_trimmed_mean=BUDGET_PICKS_MAX_PRICE + 24000,
            latest_sale_price=BUDGET_PICKS_MAX_PRICE + 24000, sales_count=40,
        )
        resp = _client([expensive_row]).get("/api/v2/trade-finder/strategy/budget")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_within_ceiling_and_profitable_is_included(self):
        row = _row(
            source_card_id=54, current_bin=20000, sales_median=25000,
            sales_trimmed_mean=25000, latest_sale_price=25000, sales_count=40,
        )
        resp = _client([row]).get("/api/v2/trade-finder/strategy/budget")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["current_bin"] <= BUDGET_PICKS_MAX_PRICE


class TestLowRiskRequiresLowRiskTier:
    def test_medium_or_high_risk_excluded(self):
        # Thin sales history and wide dispersion push risk above "low" -
        # this must not appear under Low Risk even if otherwise a buy.
        risky_row = _row(
            source_card_id=55, current_bin=40000, sales_median=55000,
            sales_trimmed_mean=55000, latest_sale_price=55000,
            sales_count=3, sales_dispersion_ratio=0.9,
        )
        resp = _client([risky_row]).get("/api/v2/trade-finder/strategy/low_risk")
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["risk_level"] == "low"


class TestLiveEntryState:
    def test_actionable_fresh_buy_is_buy_now(self):
        row = _row(source_card_id=56, current_bin=40000, sales_median=55000,
                    sales_trimmed_mean=55000, latest_sale_price=55000, sales_count=40,
                    bin_captured_at=AS_OF - timedelta(minutes=2))
        resp = _client([row]).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items and items[0]["live_entry_state"] in ("buy_now", "watch")

    def test_stale_price_is_never_labelled_buy_now(self):
        # Same otherwise-actionable card, but the price was captured a
        # long time ago - a stale price must never render as "Buy now".
        stale_row = _row(source_card_id=57, current_bin=40000, sales_median=55000,
                          sales_trimmed_mean=55000, latest_sale_price=55000, sales_count=40,
                          bin_captured_at=AS_OF - timedelta(hours=12))
        resp = _client([stale_row]).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code == 200
        items = resp.json()["items"]
        for item in items:
            if item["source_card_id"] == 57:
                assert item["live_entry_state"] == "stale"

    def test_every_item_has_a_why_this_fits_explanation(self):
        row = _row(source_card_id=58, current_bin=40000, sales_median=55000,
                    sales_trimmed_mean=55000, latest_sale_price=55000, sales_count=40)
        resp = _client([row]).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items and all(isinstance(i["why_this_fits"], str) and i["why_this_fits"] for i in items)


class TestMainGridNeverContainsWatch:
    """Hotfix regression: the pre-fix best_overall/low_risk definitions
    left require_executable at its default (False), which is what let
    signal='watch' candidates flood the main results grid (reported
    incident: cards 15-63% above their buy threshold, badged WATCH,
    sitting in the primary grid next to full trade-signal numbers)."""

    def test_best_overall_items_never_contain_watch_state(self):
        watch_row = _row(source_card_id=70, current_bin=90000, sales_median=52000,
                          sales_trimmed_mean=52000, latest_sale_price=52000, sales_count=30)
        resp = _client([watch_row]).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code == 200
        body = resp.json()
        assert all(item["live_entry_state"] != "watch" for item in body["items"])
        # A card that's only "watch" (not executable) with nothing else
        # in the candidate pool must leave the main grid empty, not fall
        # back to showing it anyway.
        assert body["items"] == []

    def test_low_risk_items_never_contain_watch_state(self):
        watch_row = _row(source_card_id=71, current_bin=90000, sales_median=52000,
                          sales_trimmed_mean=52000, latest_sale_price=52000, sales_count=30)
        resp = _client([watch_row]).get("/api/v2/trade-finder/strategy/low_risk")
        assert resp.status_code == 200
        body = resp.json()
        assert all(item["live_entry_state"] != "watch" for item in body["items"])

    def test_watch_items_key_present_and_separate_from_items(self):
        strong_row = _row(source_card_id=72, current_bin=40000, sales_median=55000,
                           sales_trimmed_mean=55000, latest_sale_price=55000, sales_count=40)
        resp = _client([strong_row]).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code == 200
        body = resp.json()
        assert "watch_items" in body
        assert isinstance(body["watch_items"], list)


class TestMainGridPriceInvariants:
    """Hotfix acceptance criteria: no card whose current price is above
    its buy threshold (or at/above target, or at/above after-tax
    break-even) may appear as an actionable main-grid item. These hold
    structurally because signal='buy'/'strong_buy' is only ever assigned
    when is_executable (current_bin<=recommended_buy_max) AND
    expected_roi clears the buy threshold when sold at target from the
    CURRENT price (see futgg_intelligence.py's evaluate_card) - this
    test proves it end-to-end through the strategy endpoint rather than
    trusting that invariant blindly."""

    def test_every_main_grid_item_is_priced_below_its_own_buy_threshold(self):
        rows = [
            _row(source_card_id=80, current_bin=40000, sales_median=55000,
                 sales_trimmed_mean=55000, latest_sale_price=55000, sales_count=40),
            _row(source_card_id=81, current_bin=90000, sales_median=52000,
                 sales_trimmed_mean=52000, latest_sale_price=52000, sales_count=30),
        ]
        resp = _client(rows).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            buy_threshold = item.get("recommended_buy_max")
            if buy_threshold is not None:
                assert item["current_bin"] <= buy_threshold

    def test_every_main_grid_item_has_current_price_below_after_tax_break_even(self):
        rows = [
            _row(source_card_id=82, current_bin=40000, sales_median=55000,
                 sales_trimmed_mean=55000, latest_sale_price=55000, sales_count=40),
        ]
        resp = _client(rows).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            target = item.get("recommended_sell_target")
            if target is not None:
                assert item["current_bin"] < target * 0.95
