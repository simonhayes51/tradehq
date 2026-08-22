# tests/test_market_event_study.py
#
# Pure tests for scripts/market_event_study.py's evaluate_cell() - no DB.
# The whole point of this classifier is that "not enough data" and
# "genuinely flat" must never render as the same "+0.0%".
from __future__ import annotations

from datetime import date

from scripts.market_event_study import CellStatus, MIN_PAIRED_CARDS, evaluate_cell

T_MINUS_1 = date(2026, 3, 9)
T0 = date(2026, 3, 10)


def _series(pairs):
    """pairs: {card_id: {date: price}}"""
    return pairs


class TestValidResults:
    def test_enough_paired_cards_with_real_movement_is_valid_move(self):
        series = _series({
            1: {T_MINUS_1: 1000, T0: 1200},
            2: {T_MINUS_1: 2000, T0: 2400},
            3: {T_MINUS_1: 500, T0: 600},
        })
        result = evaluate_cell(series, [1, 2, 3], T_MINUS_1, T0)
        assert result.status is CellStatus.VALID_MOVE
        assert result.median_pct == 20.0
        assert result.n_paired == 3

    def test_enough_paired_cards_with_no_movement_is_valid_zero_not_missing(self):
        series = _series({
            1: {T_MINUS_1: 1000, T0: 1000},
            2: {T_MINUS_1: 2000, T0: 2000},
            3: {T_MINUS_1: 500, T0: 500},
        })
        result = evaluate_cell(series, [1, 2, 3], T_MINUS_1, T0)
        assert result.status is CellStatus.VALID_ZERO
        assert result.median_pct == 0.0
        # A real flat price renders as a number, never as a missing-data marker.
        assert result.render().strip() == "+0.0%"

    def test_min_paired_cards_threshold_is_the_documented_constant(self):
        assert MIN_PAIRED_CARDS == 3


class TestMissingIsNeverRenderedAsZero:
    def test_no_card_has_a_baseline_price_is_no_baseline(self):
        series = _series({
            1: {T0: 1000},
            2: {T0: 2000},
        })
        result = evaluate_cell(series, [1, 2], T_MINUS_1, T0)
        assert result.status is CellStatus.NO_BASELINE
        assert result.median_pct is None
        assert "no_T-1" in result.render()

    def test_baseline_exists_but_no_card_has_the_target_day_is_no_target(self):
        series = _series({
            1: {T_MINUS_1: 1000},
            2: {T_MINUS_1: 2000},
        })
        result = evaluate_cell(series, [1, 2], T_MINUS_1, T0)
        assert result.status is CellStatus.NO_TARGET
        assert result.median_pct is None
        assert "no_day" in result.render()

    def test_some_pairs_exist_but_below_threshold_is_insufficient_not_zero(self):
        series = _series({
            1: {T_MINUS_1: 1000, T0: 1100},   # paired
            2: {T_MINUS_1: 2000},              # baseline only
            3: {T0: 500},                      # target only
        })
        result = evaluate_cell(series, [1, 2, 3], T_MINUS_1, T0, min_paired=3)
        assert result.status is CellStatus.INSUFFICIENT_COVERAGE
        assert result.median_pct is None
        assert result.n_paired == 1
        assert "thin" in result.render()

    def test_card_with_no_series_at_all_does_not_crash_and_is_excluded(self):
        series = _series({1: {T_MINUS_1: 1000, T0: 1100}})
        # card 99 has no price history rows anywhere
        result = evaluate_cell(series, [1, 2, 3, 99], T_MINUS_1, T0, min_paired=3)
        assert result.status is CellStatus.INSUFFICIENT_COVERAGE
        assert result.n_eligible == 4
        assert result.n_paired == 1

    def test_empty_cohort_is_no_baseline_not_a_crash(self):
        result = evaluate_cell({}, [], T_MINUS_1, T0)
        assert result.status is CellStatus.NO_BASELINE
        assert result.n_eligible == 0


class TestZeroEpsilon:
    def test_tiny_float_noise_around_zero_is_still_valid_zero(self):
        series = _series({
            1: {T_MINUS_1: 100000, T0: 100000},
            2: {T_MINUS_1: 100000, T0: 100001},   # +0.001%
            3: {T_MINUS_1: 100000, T0: 99999},    # -0.001%
        })
        result = evaluate_cell(series, [1, 2, 3], T_MINUS_1, T0)
        assert result.status is CellStatus.VALID_ZERO

    def test_a_real_small_move_outside_epsilon_is_valid_move(self):
        series = _series({
            1: {T_MINUS_1: 1000, T0: 1002},   # +0.2%, outside the 0.05% epsilon
            2: {T_MINUS_1: 1000, T0: 1002},
            3: {T_MINUS_1: 1000, T0: 1002},
        })
        result = evaluate_cell(series, [1, 2, 3], T_MINUS_1, T0)
        assert result.status is CellStatus.VALID_MOVE


class TestZeroAndFalsyPricesNeverPoisonAPair:
    def test_a_zero_baseline_price_is_excluded_not_divided_by(self):
        series = _series({
            1: {T_MINUS_1: 0, T0: 1000},
            2: {T_MINUS_1: 1000, T0: 1100},
            3: {T_MINUS_1: 2000, T0: 2200},
            4: {T_MINUS_1: 500, T0: 550},
        })
        # card 1's zero baseline must not raise a ZeroDivisionError and
        # must not be counted as a paired observation.
        result = evaluate_cell(series, [1, 2, 3, 4], T_MINUS_1, T0)
        assert result.status is CellStatus.VALID_MOVE
        assert result.n_paired == 3
