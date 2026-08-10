# tests/test_bin_market_signal.py
#
# Pure tests for app/services/bin_market_signal.py - no DB, no I/O.
#
# The load-bearing property is robustness. This module's output decides
# whether real money is spent on a sales request, and its baseline is what
# an anomaly detector then compares against - so a baseline that one absurd
# observation can move produces a detector that fires on its own noise.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.bin_market_signal import (
    BIN_MARKET_CONFIG, INSUFFICIENT_BIN_HISTORY, MOVING_DOWN, MOVING_UP,
    POSSIBLE_SPIKE, POSSIBLE_UNDERCUT, STABLE, VOLATILE, BinMarketConfig,
    compute_bin_market_signal,
)

AS_OF = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _series(prices, *, step_minutes=5, as_of=AS_OF):
    """Newest price LAST, spaced `step_minutes` apart ending at as_of."""
    n = len(prices)
    return [
        (p, as_of - timedelta(minutes=step_minutes * (n - 1 - i)))
        for i, p in enumerate(prices)
    ]


def _flat(price, n=60, **kw):
    return _series([price] * n, **kw)


class TestRobustness:
    def test_one_absurd_observation_does_not_move_the_market_level(self):
        """The exact series named in the spec. A mean of these six values
        is ~68,000 - a number no participant in this market would
        recognise. The reported level must be 42,000."""
        signal = compute_bin_market_signal(
            1, _series([42000, 42000, 41750, 42250, 200000, 42000]), as_of=AS_OF,
        )
        assert signal.median_24h == 42000
        assert signal.baseline == 42000
        # And it must be identified as anomalous rather than absorbed.
        assert signal.anomaly_score is not None

    def test_a_single_spike_is_flagged_not_absorbed(self):
        # Long flat history, then one absurd current ask.
        signal = compute_bin_market_signal(
            2, _series([42000] * 40 + [200000]), as_of=AS_OF,
        )
        assert signal.baseline == 42000
        assert signal.market_state == POSSIBLE_SPIKE
        assert signal.anomaly_score > BIN_MARKET_CONFIG.anomaly_sigma

    def test_a_flat_series_does_not_produce_infinite_anomaly_scores(self):
        """MAD collapses to 0 on a perfectly flat series. Without a
        dispersion floor every trivial deviation becomes infinitely
        anomalous and every card triggers an expensive validation."""
        signal = compute_bin_market_signal(3, _flat(50000), as_of=AS_OF)
        assert signal.anomaly_score == 0.0
        assert signal.market_state == STABLE

    def test_a_tiny_deviation_from_a_flat_series_stays_stable(self):
        # 50 coins on a 50,000 card: real, but not worth a page load.
        signal = compute_bin_market_signal(
            4, _series([50000] * 40 + [50050]), as_of=AS_OF,
        )
        assert signal.market_state == STABLE


class TestMoveThresholds:
    def test_a_large_percentage_move_on_a_cheap_card_does_not_qualify(self):
        """20% of a 1,000-coin card is 200 coins. Firing on that is how a
        cost-reduction feature becomes a cost-increase feature."""
        signal = compute_bin_market_signal(
            5, _series([1000] * 40 + [800]), as_of=AS_OF,
        )
        assert signal.market_state == STABLE

    def test_a_large_absolute_move_on_an_expensive_card_needs_the_pct_too(self):
        # 800 coins on a 400,000 card clears min_move_coins but is 0.2%.
        signal = compute_bin_market_signal(
            6, _series([400000] * 40 + [399200]), as_of=AS_OF,
        )
        assert signal.market_state == STABLE

    def test_a_move_clearing_both_thresholds_is_detected(self):
        signal = compute_bin_market_signal(
            7, _series([50000] * 40 + [42000]), as_of=AS_OF,
        )
        assert signal.market_state == POSSIBLE_UNDERCUT


class TestPersistenceDistinguishesUndercutFromReRating:
    def test_a_lone_cheap_print_against_a_held_level_is_an_undercut(self):
        signal = compute_bin_market_signal(
            8, _series([50000] * 40 + [42000]), as_of=AS_OF,
        )
        assert signal.market_state == POSSIBLE_UNDERCUT
        assert signal.price_persistence < BIN_MARKET_CONFIG.max_persistence_for_undercut

    def test_a_cheap_level_that_has_held_for_an_hour_is_a_move_not_an_undercut(self):
        """If the whole recent window IS the low price, the market moved.
        Buying sales evidence to 'validate an undercut' against a level
        that no longer exists is exactly the wasted request this is for.

        37 observations at the old level then 13 at the new one - at the
        5-minute bulk cadence that is the last hour, entirely re-priced."""
        signal = compute_bin_market_signal(
            9, _series([50000] * 37 + [42000] * 13), as_of=AS_OF,
        )
        assert signal.market_state == MOVING_DOWN
        assert signal.price_persistence >= BIN_MARKET_CONFIG.min_persistence_for_established_move

    def test_an_established_rise_is_moving_up(self):
        signal = compute_bin_market_signal(
            10, _series([40000] * 37 + [52000] * 13), as_of=AS_OF,
        )
        assert signal.market_state == MOVING_UP

    def test_a_move_old_enough_to_be_the_market_is_simply_stable(self):
        """Once the new level dominates the whole 24h window it IS the
        market - there is no anomaly left to pay to investigate, and
        reporting one would buy a sales request to confirm the status
        quo."""
        signal = compute_bin_market_signal(
            22, _series([50000] * 10 + [42000] * 40), as_of=AS_OF,
        )
        assert signal.market_state == STABLE


class TestVolatility:
    def test_a_card_with_no_settled_level_is_volatile_not_undercut(self):
        """'Undercut' is a claim about being below a level. On a card with
        no level there is nothing to be under, and claiming otherwise
        generates endless validations of pure noise."""
        prices = [30000, 60000, 35000, 70000, 32000, 65000] * 7 + [30000]
        signal = compute_bin_market_signal(11, _series(prices), as_of=AS_OF)
        assert signal.market_state == VOLATILE
        assert signal.volatility_24h >= BIN_MARKET_CONFIG.volatile_dispersion


class TestSampleSufficiency:
    def test_too_few_observations_claims_nothing(self):
        signal = compute_bin_market_signal(12, _series([42000, 42000]), as_of=AS_OF)
        assert signal.market_state == INSUFFICIENT_BIN_HISTORY

    def test_no_observations_at_all_is_handled(self):
        signal = compute_bin_market_signal(13, [], as_of=AS_OF)
        assert signal.market_state == INSUFFICIENT_BIN_HISTORY
        assert signal.current_bin is None
        assert "no_bin_observations" in signal.notes

    def test_zero_and_negative_prices_are_discarded_not_averaged_in(self):
        # The bulk feed writes no row for a no-market card, but a 0 has
        # been seen in this codebase before (see futgg_outcome_grader's
        # ZeroDivisionError) - never let one become a price.
        signal = compute_bin_market_signal(
            14, _series([42000] * 20 + [0] + [42000] * 20), as_of=AS_OF,
        )
        assert signal.median_24h == 42000
        assert signal.market_state == STABLE


class TestThinMarket:
    def test_a_week_level_far_from_the_day_level_is_marked_thin(self):
        """FC26 is full of cards whose ask wanders because listings expire,
        not because anything traded. Reported, never silently trusted."""
        old = _series([200000] * 30, as_of=AS_OF - timedelta(days=3))
        recent = _series([40000] * 30, as_of=AS_OF)
        signal = compute_bin_market_signal(15, old + recent, as_of=AS_OF)
        assert signal.thin_market is True
        assert "thin_market_7d_divergence" in signal.notes

    def test_a_consistent_market_is_not_marked_thin(self):
        old = _series([42000] * 30, as_of=AS_OF - timedelta(days=3))
        recent = _series([42000] * 30, as_of=AS_OF)
        signal = compute_bin_market_signal(16, old + recent, as_of=AS_OF)
        assert signal.thin_market is False


class TestWindowsAndCounts:
    def test_windows_are_populated_and_counts_reflect_the_window(self):
        # One observation every 5 minutes for 6 hours = 72 observations.
        signal = compute_bin_market_signal(17, _flat(42000, n=72), as_of=AS_OF)
        assert signal.median_30m == 42000
        assert signal.median_1h == 42000
        assert signal.median_6h == 42000
        # 5-minute cadence, inclusive of both window edges.
        assert signal.observation_count_1h == 13
        assert signal.observation_count_24h == 72

    def test_baseline_prefers_24h_and_falls_back_when_absent(self):
        signal = compute_bin_market_signal(18, _flat(42000, n=20), as_of=AS_OF)
        assert signal.baseline == signal.median_24h

    def test_naive_timestamps_are_treated_as_utc_not_crashed_on(self):
        naive = [(42000, (AS_OF - timedelta(minutes=5 * i)).replace(tzinfo=None))
                 for i in range(20)]
        signal = compute_bin_market_signal(19, naive, as_of=AS_OF)
        assert signal.market_state == STABLE
        assert signal.observation_count_24h == 20

    def test_unsorted_input_produces_the_same_answer(self):
        ordered = _series([50000] * 40 + [42000])
        shuffled = list(reversed(ordered))
        a = compute_bin_market_signal(20, ordered, as_of=AS_OF)
        b = compute_bin_market_signal(20, shuffled, as_of=AS_OF)
        assert a.current_bin == b.current_bin == 42000
        assert a.market_state == b.market_state


class TestConfigurability:
    def test_thresholds_are_configurable(self):
        strict = BinMarketConfig(undercut_pct=0.50, min_move_pct=0.50)
        signal = compute_bin_market_signal(
            21, _series([50000] * 40 + [42000]), as_of=AS_OF, config=strict,
        )
        assert signal.market_state == STABLE
