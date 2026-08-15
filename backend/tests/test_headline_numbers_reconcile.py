"""
The three headline numbers must describe ONE trade.

WHY THIS EXISTS
---------------
Seen live on a Granit Xhaka, all three on screen together:

    BUY BELOW       85,000   "Maximum entry"
    EXPECTED PROFIT +25,650  "After EA tax"
    SELL TARGET     97,000   "List around this price"

Those numbers do not reconcile. 97,000 less EA's 5% is 92,150; buying at
the advertised 85,000 returns 7,150, not 25,650. The 25,650 was computed
against the 66,500 live ask - a price that appeared NOWHERE on the card.
The profit was overstated 3.6x at the entry the user was being told to
pay.

The engine was not wrong. Its own comment is explicit: "Expected outcome
is always computed against the price a user would actually pay" - the
live ask on a buy, the trigger price on a watch. The defect was
presentational: the UI paired that profit with a DIFFERENT price and
hid the one it depended on.

expected_profit_basis makes the assumed entry an explicit part of the
result, so the two can never be shown apart. These tests pin the
arithmetic identity that failed:

    profit == net_profit(sell_target, expected_profit_basis)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.routers.v2.dashboard import _futgg_to_recommendation
from app.services import trading_math as tm
from app.services.futgg_intelligence import evaluate_card

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _sales(n=30, price=98_000):
    return [
        {"sold_price": price + (i * 211) % 3000,
         "approximate_sold_at": NOW - timedelta(minutes=i + 1)}
        for i in range(n)
    ]


def _snapshot(current_bin: int, **overrides):
    snap = {
        "source_card_id": 1, "name": "Test", "rating": 95,
        "price_tier": "special", "is_tradeable": True,
        "current_bin": current_bin,
        "sales_count": 30, "sales_median": 98_000,
        "sales_trimmed_mean": 98_000, "sales_low": 97_000, "sales_high": 100_000,
        "sales_dispersion_ratio": 0.02, "sales_interdecile_ratio": 0.03,
        "sales_window_span_minutes": 60.0,
        "latest_sale_at": NOW - timedelta(minutes=1),
        "bin_checked_at": NOW - timedelta(minutes=3),
        "bin_captured_at": NOW - timedelta(minutes=3),
    }
    snap.update(overrides)
    return snap


class TestArithmeticIdentity:
    @pytest.mark.parametrize("current_bin", [45_000, 66_500, 80_000, 120_000])
    def test_profit_always_reconciles_with_its_stated_basis(self, current_bin):
        """The identity that must hold at every price, buy or watch."""
        ci = evaluate_card(_snapshot(current_bin), as_of=NOW, sales=_sales())
        if ci.expected_profit_after_tax is None:
            pytest.skip("card not valued at this price")
        assert ci.expected_profit_basis is not None
        assert ci.expected_profit_after_tax == tm.net_profit(
            ci.recommended_sell_target, ci.expected_profit_basis
        )

    def test_on_a_buy_the_basis_is_the_live_ask_not_the_ceiling(self):
        """The Xhaka shape: a genuinely executable card where the ask sits
        well below the advised ceiling. The two must stay distinguishable,
        because a profit quoted at one and displayed beside the other is
        the bug."""
        ci = evaluate_card(_snapshot(66_500), as_of=NOW, sales=_sales())
        assert ci.signal in ("buy", "strong_buy")
        assert ci.expected_profit_basis == Decimal("66500")
        assert ci.recommended_buy_max > ci.expected_profit_basis
        # The number that was on screen, and what it really returns.
        at_ceiling = tm.net_profit(ci.recommended_sell_target, ci.recommended_buy_max)
        assert at_ceiling < ci.expected_profit_after_tax

    def test_on_a_watch_the_basis_is_the_trigger_price(self):
        """When the ask is above the ceiling there is nothing to buy at,
        so the profit is prospective and must be quoted against the price
        the user is being told to wait for."""
        ci = evaluate_card(_snapshot(120_000), as_of=NOW, sales=_sales())
        if ci.signal == "insufficient_data":
            pytest.skip("blocked before a verdict at this price")
        assert ci.current_executable_buy is None
        assert ci.expected_profit_basis == ci.recommended_buy_max


class TestTheAdapterForwardsIt:
    def test_the_basis_reaches_the_client(self):
        """Without this the UI cannot pair them correctly, which is
        exactly how the mismatch shipped."""
        ci = evaluate_card(_snapshot(66_500), as_of=NOW, sales=_sales())
        d = _futgg_to_recommendation({"source_card_id": 1, "current_bin": 66_500}, ci)
        assert d["expectedProfitBasis"] == 66_500
        assert d["recommendedBuyMax"] == int(ci.recommended_buy_max)
        # And the two are genuinely different, so a UI that shows the
        # wrong one is visibly wrong rather than coincidentally right.
        assert d["expectedProfitBasis"] != d["recommendedBuyMax"]

    def test_an_unvalued_card_carries_no_basis(self):
        d = _futgg_to_recommendation(
            {"source_card_id": 1, "current_bin": 66_500},
            evaluate_card(_snapshot(66_500, sales_count=0), as_of=NOW, sales=[]),
        )
        assert d["expectedProfitBasis"] is None
