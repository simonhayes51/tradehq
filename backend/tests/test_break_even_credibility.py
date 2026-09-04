"""The Mbappé break-even case, pinned on the backend side.

A real Free portfolio row showed Mbappé bought at 2,304,000 with a
target of 2,425,500 and a "projected profit" of 225 coins. The
arithmetic was right; the presentation was not. The frontend now
classifies a target like that as marginal rather than as a return (see
frontend/src/v2/lib/tradeMath.js and its tests) - these tests pin the
numbers that classification depends on, so a change to the canonical
backend maths can never silently move the frontend's warning threshold
without a failing test on this side too.

Also covers the close-trade contract: what is persisted is what the
route reports back.
"""
from decimal import Decimal

import pytest

from app.services.trade_economics import close_trade_economics
from app.services.trading_math import (
    break_even_sale_price,
    net_profit,
    net_roi,
    net_sale_proceeds,
    round_to_ea_increment,
)

MBAPPE_BUY = 2_304_000
MBAPPE_TARGET = 2_425_500


class TestMbappeBreakEven:
    def test_the_recorded_target_really_does_net_225_coins(self):
        # int(target * 0.95) - buy, which is what a close would persist.
        assert close_trade_economics(MBAPPE_BUY, MBAPPE_TARGET, 1).profit == 225

    def test_225_coins_on_a_2_3m_stake_is_break_even_not_a_return(self):
        roi = net_roi(MBAPPE_TARGET, MBAPPE_BUY)
        # Under a tenth of one percent. The frontend's "marginal" floor is
        # 2% of stake; this is two orders of magnitude below it.
        assert roi is not None
        assert abs(roi) < Decimal("0.0002")

    def test_the_true_listable_break_even_is_above_the_recorded_target(self):
        # 2,304,000 / 0.95 = 2,425,263.16, rounded up into the 5,000-coin
        # increment band. The recorded target (2,425,500) is not even a
        # listable price - it came from a hard-coded 250-coin increment
        # that is only correct below 50,000 coins.
        assert break_even_sale_price(MBAPPE_BUY) == Decimal("2430000")
        assert break_even_sale_price(MBAPPE_BUY) > MBAPPE_TARGET


class TestBreakEvenBoundaries:
    @pytest.mark.parametrize("buy", [250, 900, 5_000, 47_500, 99_000, 640_000, 2_304_000])
    def test_break_even_never_rounds_down_into_a_loss(self, buy):
        assert net_profit(break_even_sale_price(buy), buy) >= 0

    @pytest.mark.parametrize("buy", [900, 5_000, 47_500, 99_000, 640_000])
    def test_one_increment_below_break_even_is_a_real_loss(self, buy):
        be = break_even_sale_price(buy)
        below = round_to_ea_increment(be - 1, direction="down")
        assert net_profit(below, buy) < 0

    def test_a_three_percent_pre_tax_gain_is_a_loss_after_tax(self):
        # The class of bug the tax rules exist to make impossible: a gap
        # smaller than 5.26% cannot clear EA's cut.
        assert net_profit(103_000, 100_000) < 0

    def test_proceeds_are_exactly_ninety_five_percent(self):
        assert net_sale_proceeds(100_000) == Decimal("95000")


class TestPersistedCloseMatchesReportedClose:
    """POST /api/v2/trades/{id}/close returned a bare `profit` name that
    was never assigned - a NameError raised AFTER the UPDATE had already
    committed, so the trade closed correctly and the caller got a 500.
    The route now reports the economics it just wrote."""

    def test_route_reports_the_same_numbers_it_persists(self):
        economics = close_trade_economics(10_000, 13_000, 2)
        payload = {
            "ok": True,
            "trade_id": 1,
            "status": "closed",
            "profit": economics.profit,
            "ea_tax": economics.ea_tax,
        }
        assert payload["profit"] == economics.profit == 4_700
        assert payload["ea_tax"] == economics.ea_tax == 1_300

    def test_the_close_route_no_longer_references_an_unassigned_name(self):
        import inspect

        from app.routers.v2 import trades

        source = inspect.getsource(trades.close_trade)
        # The bug, precisely: `"profit": profit` with no local `profit`.
        assert '"profit": profit' not in source
        assert '"profit": economics.profit' in source
