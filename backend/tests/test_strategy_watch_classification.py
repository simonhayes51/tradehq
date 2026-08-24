# tests/test_strategy_watch_classification.py
#
# Pure unit tests for app/services/strategy_definitions.py's
# classify_watch_candidate() - the hotfix for Strategy Finder's main
# grid being flooded with signal='watch' cards (Gareth Bale at current
# 391,000 vs buy below 240,000 - 63% above entry - shown in the primary
# results grid, badged WATCH, next to full trade-signal numbers).
#
# These are pure/synthetic (no HTTP, no DB) so every boundary can be
# constructed exactly, rather than reverse-engineered through the real
# scoring pipeline's price formulas.
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.strategy_definitions import (
    WATCH_TOLERANCE_PCT,
    classify_watch_candidate,
)


@dataclass
class FakeCI:
    recommended_buy_max: Optional[float]
    recommended_sell_target: Optional[float]


def _row(current_bin):
    return {"current_bin": current_bin}


class TestToleranceBoundary:
    def test_materially_above_entry_is_excluded(self):
        # The reported incident, to scale: buy below 240,000, current
        # 391,000 - roughly 63% above entry. Must never reach Watch.
        ci = FakeCI(recommended_buy_max=240_000, recommended_sell_target=280_000)
        result = classify_watch_candidate(_row(391_000), ci)
        assert result is None

    def test_slightly_above_entry_and_profitable_is_included(self):
        # 5% above entry, well within the 10% tolerance, and the sell
        # target still leaves real after-tax margin from the CURRENT
        # price.
        ci = FakeCI(recommended_buy_max=100_000, recommended_sell_target=130_000)
        result = classify_watch_candidate(_row(105_000), ci)
        assert result is not None
        assert result["above_entry_pct"] == pytest_approx(5.0)
        assert result["real_time_expected_profit_after_tax"] > 0

    def test_exactly_at_the_tolerance_boundary_is_included(self):
        ci = FakeCI(recommended_buy_max=100_000, recommended_sell_target=200_000)
        at_boundary = 110_000  # exactly WATCH_TOLERANCE_PCT (10%) above 100,000
        result = classify_watch_candidate(_row(at_boundary), ci)
        assert result is not None

    def test_just_past_the_tolerance_boundary_is_excluded(self):
        ci = FakeCI(recommended_buy_max=100_000, recommended_sell_target=200_000)
        just_past = 100_000 * (1 + WATCH_TOLERANCE_PCT) + 1
        result = classify_watch_candidate(_row(just_past), ci)
        assert result is None

    def test_at_or_below_entry_is_not_a_watch_candidate(self):
        # 0% or negative "above entry" means it's executable right now -
        # that belongs in the main BUY NOW grid, not Watch.
        ci = FakeCI(recommended_buy_max=100_000, recommended_sell_target=130_000)
        assert classify_watch_candidate(_row(100_000), ci) is None
        assert classify_watch_candidate(_row(95_000), ci) is None


class TestTargetAndBreakEven:
    def test_current_price_at_or_above_target_is_excluded(self):
        ci = FakeCI(recommended_buy_max=100_000, recommended_sell_target=105_000)
        # 105,000 is within 10% of buy_max but AT the target - must be excluded.
        result = classify_watch_candidate(_row(105_000), ci)
        assert result is None

    def test_current_price_at_or_above_after_tax_break_even_is_excluded(self):
        # target * 0.95 is the after-tax break-even. A current price at
        # or above that point leaves no real margin even before EA tax
        # is subtracted from the sale, and must be excluded.
        target = 100_000
        break_even = target * 0.95
        ci = FakeCI(recommended_buy_max=95_000, recommended_sell_target=target)
        result = classify_watch_candidate(_row(break_even), ci)
        assert result is None

    def test_real_time_profit_uses_the_current_price_not_the_trigger_price(self):
        # The engine's own prospective watch number assumes the price
        # falls to buy_max; classify_watch_candidate must NOT reuse that
        # number - it recomputes profit from where the price actually is
        # right now, per the hotfix's exact formula.
        ci = FakeCI(recommended_buy_max=100_000, recommended_sell_target=140_000)
        current = 108_000  # 8% above entry, within tolerance
        result = classify_watch_candidate(_row(current), ci)
        assert result is not None
        expected_profit = 140_000 * 0.95 - current
        assert abs(result["real_time_expected_profit_after_tax"] - expected_profit) < 1


class TestMissingData:
    def test_missing_fields_are_handled_without_raising(self):
        ci = FakeCI(recommended_buy_max=None, recommended_sell_target=130_000)
        assert classify_watch_candidate(_row(105_000), ci) is None
        ci2 = FakeCI(recommended_buy_max=100_000, recommended_sell_target=None)
        assert classify_watch_candidate(_row(105_000), ci2) is None
        ci3 = FakeCI(recommended_buy_max=100_000, recommended_sell_target=130_000)
        assert classify_watch_candidate(_row(None), ci3) is None


def pytest_approx(value, rel=1e-3):
    import pytest
    return pytest.approx(value, rel=rel)
