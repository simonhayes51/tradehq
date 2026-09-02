# tests/test_trade_economics.py
#
# WHY THIS EXISTS
# ---------------
# app/services/trade_economics.py is the canonical closed-trade profit/
# ROI formula, consolidated out of three call sites that used to
# duplicate it inline (app/routers/v2/trades.py's close_trade, and
# app/routers/trades.py's create_trade + bulk_insert_trades) plus a
# fourth that independently, and wrongly, recomputed ROI on the
# frontend (TradeShareCardArt.jsx divided profit - already scaled by
# quantity - by unit buy price alone, overstating ROI by a factor of
# quantity for every qty>1 trade). These tests pin the formula's
# behaviour with quantity=1 and quantity>1, and regression-test the
# exact worked example used to spec this module.
from __future__ import annotations

from app.services.trade_economics import close_trade_economics, realised_roi_percent


class TestCloseTradeEconomicsQuantityOne:
    def test_matches_hand_computed_tax_and_profit(self):
        # buy 1,000,000 sell 1,500,000: 5% tax on the unit sell price is
        # exactly 75,000 (no rounding), so this case can be hand-checked.
        economics = close_trade_economics(1_000_000, 1_500_000, 1)
        assert economics.ea_tax == 75_000
        assert economics.profit == 425_000  # (1,500,000 - 75,000) - 1,000,000


class TestCloseTradeEconomicsQuantityAboveOne:
    def test_tax_and_profit_scale_with_quantity(self):
        # Same per-unit prices as above, quantity=4: both tax and profit
        # scale linearly with quantity - this is exactly the dimension
        # the pre-fix share card ROI calc dropped.
        economics = close_trade_economics(1_000_000, 1_500_000, 4)
        assert economics.ea_tax == 75_000 * 4
        assert economics.profit == 425_000 * 4

    def test_the_haaland_worked_example_from_the_audit_spec(self):
        # qty 4, buy 997,000 each, sell 1,450,000 each -> total_cost
        # 3,988,000, gross 5,800,000, tax 290,000, net 5,510,000,
        # profit 1,522,000, ROI 38.16%. This example was chosen
        # (deliberately, by the spec) so that 1,450,000 * 0.95 lands on
        # a whole coin - the per-unit-floor formula this module
        # preserves and the tax-on-aggregate-proceeds formula the spec
        # describes agree exactly here, with no rounding divergence.
        economics = close_trade_economics(997_000, 1_450_000, 4)
        assert economics.profit == 1_522_000
        assert economics.ea_tax == 290_000

        roi = realised_roi_percent(economics.profit, 997_000, 4)
        assert round(roi, 2) == 38.16


class TestRealisedRoiPercent:
    def test_roi_is_against_total_cost_not_unit_buy_price(self):
        # This is the exact bug: profit is already quantity-scaled, so
        # dividing by unit buy price alone (not buy*quantity) overstates
        # ROI by a factor of quantity.
        profit = 425_000 * 4
        assert realised_roi_percent(profit, 1_000_000, 4) == 42.5
        # The bug's number, for contrast - what the old frontend calc
        # would have shown: 4x too high.
        buggy = (profit / 1_000_000) * 100
        assert buggy == 170.0
        assert buggy != realised_roi_percent(profit, 1_000_000, 4)

    def test_quantity_one_is_unaffected(self):
        assert realised_roi_percent(425_000, 1_000_000, 1) == 42.5

    def test_zero_cost_returns_none_not_zero_or_infinity(self):
        assert realised_roi_percent(1000, 0, 1) is None

    def test_a_loss_produces_a_negative_roi(self):
        assert realised_roi_percent(-50_000, 1_000_000, 1) == -5.0
