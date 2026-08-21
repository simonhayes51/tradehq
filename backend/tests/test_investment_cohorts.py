# tests/test_investment_cohorts.py
#
# Pure tests for app/services/investment_engine/cohorts.py - no DB, no I/O.
from __future__ import annotations

from app.services.investment_engine import cohorts as c
from app.services.investment_engine.config import InvestmentEngineConfig


CONFIG = InvestmentEngineConfig()


def _cards(prices, position="ST"):
    return [
        c.CohortInput(card_id=i, reference_price=p, primary_position=position)
        for i, p in enumerate(prices)
    ]


class TestPositionGroups:
    def test_strikers_and_keepers_are_never_compared(self):
        strikers = _cards([1000] * 50, position="ST")
        # offset ids so they don't collide with the strikers above
        keepers = [c.CohortInput(card_id=i + 1000, reference_price=p, primary_position="GK")
                   for i, p in enumerate([50000] * 50)]
        assignments = c.classify_cohorts(strikers + keepers, CONFIG)
        groups_seen = {a.position_group for a in assignments}
        assert groups_seen == {"STRIKER", "GK"}
        # Every striker and every keeper independently spans the full
        # percentile range 0..1 - a flat-priced GK pool is never judged
        # against the (much cheaper) striker pool's price scale.
        striker_pctiles = sorted(a.price_percentile for a in assignments if a.position_group == "STRIKER")
        assert striker_pctiles[0] == 0.0
        assert striker_pctiles[-1] == 1.0

    def test_unknown_position_falls_back_to_other(self):
        assert c.position_group("XX") == "OTHER"
        assert c.position_group(None) == "OTHER"


class TestClassification:
    def test_top_decile_is_premium_meta(self):
        prices = list(range(1, 101))  # 1..100, evenly spaced
        assignments = c.classify_cohorts(_cards(prices), CONFIG)
        top = [a for a in assignments if a.reference_price == 100][0]
        assert top.cohort_key == c.COHORT_PREMIUM_META

    def test_bottom_of_range_is_likely_fodder(self):
        prices = list(range(1, 101))
        assignments = c.classify_cohorts(_cards(prices), CONFIG)
        bottom = [a for a in assignments if a.reference_price == 1][0]
        assert bottom.cohort_key == c.COHORT_LIKELY_FODDER

    def test_all_five_cohorts_appear_over_a_wide_enough_population(self):
        prices = list(range(1, 201))
        assignments = c.classify_cohorts(_cards(prices), CONFIG)
        assert {a.cohort_key for a in assignments} == set(c.ALL_COHORTS)

    def test_small_group_below_min_size_is_skipped(self):
        prices = [100, 200, 300]
        assignments = c.classify_cohorts(_cards(prices), CONFIG, min_group_size=10)
        assert assignments == []

    def test_reference_price_and_card_id_are_preserved(self):
        assignments = c.classify_cohorts(_cards([500]), CONFIG)
        assert assignments[0].reference_price == 500
        assert assignments[0].card_id == 0

    def test_no_lookahead_classification_is_deterministic_given_same_inputs(self):
        # Calling twice with the same (unchanged) input list must produce
        # identical output - nothing here should depend on wall-clock
        # time or any external state.
        prices = list(range(1, 51))
        a1 = c.classify_cohorts(_cards(prices), CONFIG)
        a2 = c.classify_cohorts(_cards(prices), CONFIG)
        assert [(a.card_id, a.cohort_key) for a in a1] == [(a.card_id, a.cohort_key) for a in a2]
