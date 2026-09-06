# tests/test_futgg_candidate_segments_coverage.py
#
# Production case (2026-09-06): card_id 50562269 (Gianluigi Donnarumma,
# sales_count=50, current_bin 22.8% below sales_median) evaluated to
# signal="strong_buy" on its own direct /players/{card_id} lookup, but
# never appeared in /api/v2/opportunities - proven, via the scan log's
# buy_signal_card_ids, to be the exact card the scanner found as a live
# buy signal minutes earlier. Root cause: it qualified for both "liquid"
# (sales_count >= 25) and "recently_moved" (>= 8% BIN-vs-median deviation)
# by their WHERE clauses, but ranked outside both segments' LIMIT cutoffs -
# "liquid" by raw sales_count DESC (a 50-sale card loses to higher-volume
# ones), "recently_moved" by raw deviation DESC with no liquidity floor (a
# one-sale outlier can have a much larger, meaningless deviation ratio and
# rank ahead of a real, well-sampled move). These tests cover the two
# changes made in response: a sales_count floor on "recently_moved" so
# noise can't outrank it, and confirm the segment carries this card's
# WHERE clause correctly.
from __future__ import annotations

from app.services.futgg_candidate_segments import MOVED_MIN_SALES, SEGMENTS_BY_NAME

DONNARUMMA_ROW = {
    "current_bin": 11000,
    "sales_median": 14250.0,
    "sales_count": 50,
}


def _matches_where(where_sql: str, row: dict) -> bool:
    # The WHERE clauses here are simple enough (numeric comparisons, no
    # subqueries) to evaluate directly against a row dict by substituting
    # column names - avoids standing up a real Postgres connection just to
    # check a predicate shape.
    import re

    expr = where_sql
    for key, value in row.items():
        expr = re.sub(rf"\b{key}\b", repr(value), expr)
    expr = (
        expr.replace("IS NOT NULL", "!= None")
        .replace("abs(", "abs(")
        .replace(" AND ", " and ")
        .replace(" OR ", " or ")
    )
    return bool(eval(expr))  # noqa: S307 - static module-level SQL, test-only


class TestRecentlyMovedHasALiquidityFloor:
    def test_moved_min_sales_constant_is_at_least_the_engines_own_signal_floor(self):
        # Filtering below what evaluate_card() itself requires for a
        # completed-sales signal (futgg_config.py's min_sales_for_signal)
        # would exclude rows this segment could still usefully rank -
        # the floor must not be stricter than that.
        assert MOVED_MIN_SALES <= 5

    def test_single_sale_noise_no_longer_qualifies_for_recently_moved(self):
        noisy_one_sale_row = {
            "current_bin": 100,
            "sales_median": 1000.0,
            "sales_count": 1,
        }
        segment = SEGMENTS_BY_NAME["recently_moved"]
        assert not _matches_where(segment.where, noisy_one_sale_row)

    def test_well_sampled_real_move_still_qualifies_for_recently_moved(self):
        segment = SEGMENTS_BY_NAME["recently_moved"]
        assert _matches_where(segment.where, DONNARUMMA_ROW)


class TestSegmentLimitsWereRaised:
    # Not a magic-number lock-in - just a guard against silently
    # reverting the fix back to the limits that missed this card.
    def test_liquid_and_recently_moved_limits_are_larger_than_the_old_default(self):
        assert SEGMENTS_BY_NAME["liquid"].limit > 200
        assert SEGMENTS_BY_NAME["recently_moved"].limit > 200

    def test_max_total_candidates_covers_the_new_segment_limit_sum(self):
        from app.services.futgg_candidate_segments import MAX_TOTAL_CANDIDATES, SEGMENTS

        assert MAX_TOTAL_CANDIDATES >= sum(s.limit for s in SEGMENTS)
