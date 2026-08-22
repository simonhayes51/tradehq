# tests/test_incumbent_market_study.py
#
# Pure tests for app/services/incumbent_market_study.py - no DB.
from __future__ import annotations

from datetime import date

from app.services.investment_engine.config import InvestmentEngineConfig
from app.services.incumbent_market_study import (
    LegStats, aggregate_event_level, assign_asof_launch_cohorts, assign_price_band,
    card_return, entry_exit_legs, flag_independent_events, legs_for_event, leg_stats,
    pace_band, per_label_legs, rating_band, season_regime, weekday_bucket,
)

CONFIG = InvestmentEngineConfig()

T_MINUS_1 = date(2026, 3, 12)   # a Thursday
T0 = date(2026, 3, 13)          # a Friday


class TestCardReturn:
    def test_computes_pct_change(self):
        assert card_return({T_MINUS_1: 1000, T0: 1100}, T_MINUS_1, T0) == 10.0

    def test_missing_baseline_is_none(self):
        assert card_return({T0: 1100}, T_MINUS_1, T0) is None

    def test_missing_target_is_none(self):
        assert card_return({T_MINUS_1: 1000}, T_MINUS_1, T0) is None

    def test_zero_baseline_never_divides_by_zero(self):
        assert card_return({T_MINUS_1: 0, T0: 1000}, T_MINUS_1, T0) is None


class TestLegStats:
    def test_below_min_paired_is_not_reportable(self):
        series = {1: {T_MINUS_1: 1000, T0: 1100}, 2: {T_MINUS_1: 2000, T0: 2200}}
        result = leg_stats("T-1->T0", series, [1, 2], T_MINUS_1, T0)
        assert not result.is_reportable
        assert result.median is None

    def test_enough_paired_reports_full_stats(self):
        series = {
            1: {T_MINUS_1: 1000, T0: 1100}, 2: {T_MINUS_1: 2000, T0: 2200},
            3: {T_MINUS_1: 500, T0: 600},
        }
        result = leg_stats("T-1->T0", series, [1, 2, 3], T_MINUS_1, T0)
        assert result.is_reportable
        assert result.n_paired == 3
        assert result.median == 10.0
        assert result.tax_adjusted_median == 5.0  # 10% - 5% EA tax

    def test_n_eligible_counts_the_whole_group_not_just_paired(self):
        series = {1: {T_MINUS_1: 1000, T0: 1100}}
        result = leg_stats("T-1->T0", series, [1, 2, 3, 4], T_MINUS_1, T0)
        assert result.n_eligible == 4
        assert result.n_paired == 1


class TestSeasonRegimeAndWeekday:
    def test_launch_window(self):
        assert season_regime(date(2025, 9, 17)) == "Launch (Sep-Oct 2025)"

    def test_summer_window(self):
        assert season_regime(date(2026, 7, 24)) == "Summer/FUTTIES (Jun-Aug 2026)"

    def test_out_of_range_is_other(self):
        assert season_regime(date(2024, 1, 1)) == "Other"

    def test_weekday_bucket_reads_real_weekday(self):
        assert weekday_bucket(date(2026, 3, 13)) == "Friday"   # matches T0 above
        assert weekday_bucket(date(2026, 3, 12)) == "Thursday"
        assert weekday_bucket(date(2026, 3, 14)) == "Weekend"


class TestIndependenceFlag:
    def test_events_far_apart_are_both_independent(self):
        events = [
            {"id": 1, "released_at": date(2026, 1, 1)},
            {"id": 2, "released_at": date(2026, 1, 20)},
        ]
        flags = flag_independent_events(events)
        assert flags[1] is True
        assert flags[2] is True

    def test_first_event_is_always_independent(self):
        events = [{"id": 1, "released_at": date(2026, 1, 1)}]
        assert flag_independent_events(events)[1] is True

    def test_weekly_futties_style_gap_is_not_independent(self):
        events = [
            {"id": 1, "released_at": date(2026, 7, 24)},
            {"id": 2, "released_at": date(2026, 7, 31)},   # exactly 7 days later
        ]
        flags = flag_independent_events(events)
        assert flags[1] is True   # first in sequence
        assert flags[2] is False  # window overlaps the previous event's T+7

    def test_gap_of_exactly_the_threshold_is_independent(self):
        events = [
            {"id": 1, "released_at": date(2026, 1, 1)},
            {"id": 2, "released_at": date(2026, 1, 9)},   # 8 days later
        ]
        flags = flag_independent_events(events)
        assert flags[2] is True


class TestNoLookaheadCohorting:
    def test_launch_cohort_uses_only_the_events_own_t_minus_1_price(self):
        # Card 1 is cheap in September (T-1) but would look premium if a
        # later/global price were used by mistake - the assignment must
        # reflect September's price only.
        series = {1: {date(2025, 9, 16): 1000}, 2: {date(2025, 9, 16): 900000}}
        labels = assign_asof_launch_cohorts(
            [1, 2], series, {1: "ST", 2: "ST"}, date(2025, 9, 16), CONFIG,
        )
        assert labels[1] != labels[2]  # position-relative percentile split them

    def test_card_with_no_t_minus_1_price_is_omitted_not_guessed(self):
        series = {1: {date(2025, 9, 16): 1000}}
        labels = assign_asof_launch_cohorts(
            [1, 2], series, {1: "ST", 2: "ST"}, date(2025, 9, 16), CONFIG,
        )
        assert 2 not in labels

    def test_price_band_is_recomputed_per_event_from_that_days_prices(self):
        d1 = date(2025, 9, 16)
        series = {i: {d1: (i + 1) * 1000} for i in range(10)}
        bands = assign_price_band(list(range(10)), series, d1)
        assert bands[0] == "Q1_cheapest"
        assert bands[9] == "Q5_priciest"

    def test_price_band_too_few_priced_cards_returns_empty_not_a_crash(self):
        d1 = date(2025, 9, 16)
        series = {1: {d1: 1000}}
        assert assign_price_band([1], series, d1) == {}


class TestStaticBands:
    def test_rating_band_boundaries(self):
        assert rating_band(75) == "75-77"
        assert rating_band(77) == "75-77"
        assert rating_band(78) == "78-80"
        assert rating_band(83) == "81-83"
        assert rating_band(90) == "87+"
        assert rating_band(None) == "unknown"

    def test_pace_band_boundaries(self):
        assert pace_band(55) == "<60"
        assert pace_band(65) == "60-69"
        assert pace_band(95) == "90+"
        assert pace_band(None) == "unknown"


class TestAggregateEventLevel:
    def test_one_event_one_observation_not_pooled_cards(self):
        # Event A: 3 cards, huge +50% median. Event B: 30 cards, flat 0%.
        # A naive card-pool would be dominated by B's 30 observations;
        # event-level aggregation must treat A and B as ONE vote each.
        legs = (("T-1->T0", -1, 0),)
        big_flat = {i: LegStats("T-1->T0", 30, 30, median=0.0) for i in [0]}
        event_a = {"T-1->T0": LegStats("T-1->T0", 3, 3, median=50.0)}
        event_b = {"T-1->T0": LegStats("T-1->T0", 30, 30, median=0.0)}
        agg = aggregate_event_level([event_a, event_b], legs)
        assert agg["T-1->T0"]["n_events"] == 2
        assert agg["T-1->T0"]["median_of_medians"] == 25.0  # median of [50, 0], not a 30-vs-3 weighted pool

    def test_unreportable_events_are_excluded_from_aggregate(self):
        legs = (("T-1->T0", -1, 0),)
        event_a = {"T-1->T0": LegStats("T-1->T0", 1, 1)}  # below MIN_PAIRED_CARDS
        event_b = {"T-1->T0": LegStats("T-1->T0", 3, 3, median=10.0)}
        agg = aggregate_event_level([event_a, event_b], legs)
        assert agg["T-1->T0"]["n_events"] == 1

    def test_fewer_than_two_reportable_events_yields_no_median(self):
        legs = (("T-1->T0", -1, 0),)
        event_a = {"T-1->T0": LegStats("T-1->T0", 3, 3, median=10.0)}
        agg = aggregate_event_level([event_a], legs)
        assert agg["T-1->T0"]["median_of_medians"] is None


class TestEntryExitMatrix:
    def test_only_forward_looking_pairs_are_generated(self):
        for name, entry, exit_off in entry_exit_legs():
            assert exit_off > entry

    def test_covers_every_entry_offset(self):
        entries = {entry for _, entry, _ in entry_exit_legs()}
        assert entries == {-1, 0, 1, 2}


class TestPerLabelLegs:
    def test_splits_cards_by_label_before_computing_legs(self):
        legs = (("T-1->T0", -1, 0),)
        series = {
            1: {T_MINUS_1: 1000, T0: 1100}, 2: {T_MINUS_1: 2000, T0: 2200},
            3: {T_MINUS_1: 3000, T0: 3000},
        }
        cid_to_label = {1: "premium_meta", 2: "premium_meta", 3: "ordinary"}
        result = per_label_legs(legs, series, cid_to_label, T_MINUS_1)
        assert result["premium_meta"]["T-1->T0"].n_eligible == 2
        assert result["ordinary"]["T-1->T0"].n_eligible == 1
