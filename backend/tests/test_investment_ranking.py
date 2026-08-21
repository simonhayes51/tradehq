# tests/test_investment_ranking.py
#
# Pure tests for app/services/investment_engine/ranking.py - no DB, no I/O.
from __future__ import annotations

from app.services.investment_engine.ranking import (
    CardCandidate, INVESTABILITY_INVESTABLE, INVESTABILITY_LIMITED,
    LIQUIDITY_HIGH, LIQUIDITY_LOW, investability, liquidity_tier, rank_candidates,
)


def _candidate(card_id, sales_count=10, decel=True, strength=0.0, tradeable=True, price_age=10):
    return CardCandidate(
        card_id=card_id, current_price=10000.0, drawdown_pct=45.0,
        is_decelerating=decel, relative_strength_pct=strength,
        sales_count_recent=sales_count, price_age_minutes=price_age, is_tradeable=tradeable,
    )


class TestLiquidityAndInvestability:
    def test_low_sales_count_is_low_liquidity(self):
        assert liquidity_tier(1) == LIQUIDITY_LOW
        assert liquidity_tier(None) == LIQUIDITY_LOW

    def test_high_sales_count_is_high_liquidity(self):
        assert liquidity_tier(20) == LIQUIDITY_HIGH

    def test_investability_requires_the_configured_minimum(self):
        assert investability(5, min_sales_for_investable=5) == INVESTABILITY_INVESTABLE
        assert investability(4, min_sales_for_investable=5) == INVESTABILITY_LIMITED
        assert investability(None, min_sales_for_investable=5) == INVESTABILITY_LIMITED


class TestRankCandidates:
    def test_a_single_attractive_listing_with_no_sales_history_is_excluded(self):
        """The explicit Trade-vs-Investment split: one great-looking
        listing with no supporting sales is a Trade Engine snipe, not an
        Investment Engine candidate."""
        candidates = [_candidate(1, sales_count=0)]
        ranked = rank_candidates(candidates, min_sales_for_investable=5, max_price_age_minutes=180)
        assert ranked == []

    def test_untradeable_cards_are_always_excluded(self):
        candidates = [_candidate(1, tradeable=False)]
        ranked = rank_candidates(candidates, min_sales_for_investable=5, max_price_age_minutes=180)
        assert ranked == []

    def test_stale_price_is_excluded(self):
        candidates = [_candidate(1, price_age=999)]
        ranked = rank_candidates(candidates, min_sales_for_investable=5, max_price_age_minutes=180)
        assert ranked == []

    def test_liquid_investable_cards_are_ranked_not_hard_filtered_further(self):
        """Per the FC26 finding that no card-level filter reliably beat
        broad cohort buying: everything past the hard investability/
        tradeability/freshness bar survives, just ordered."""
        candidates = [
            _candidate(1, sales_count=20, decel=True, strength=5.0),
            _candidate(2, sales_count=6, decel=False, strength=-5.0),
        ]
        ranked = rank_candidates(candidates, min_sales_for_investable=5, max_price_age_minutes=180)
        assert {r.card_id for r in ranked} == {1, 2}

    def test_higher_liquidity_and_deceleration_ranks_first(self):
        strong = _candidate(1, sales_count=25, decel=True, strength=10.0)
        weak = _candidate(2, sales_count=5, decel=False, strength=-10.0)
        ranked = rank_candidates([weak, strong], min_sales_for_investable=5, max_price_age_minutes=180)
        assert ranked[0].card_id == 1

    def test_require_investable_false_still_ranks_limited_liquidity_cards(self):
        candidates = [_candidate(1, sales_count=0)]
        ranked = rank_candidates(
            candidates, min_sales_for_investable=5, max_price_age_minutes=180, require_investable=False,
        )
        assert len(ranked) == 1
        assert ranked[0].investability == INVESTABILITY_LIMITED
