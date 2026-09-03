# tests/test_priority_engine.py
#
# Pure-function tests for app/services/priority_engine.py's
# compute_priority() and classify_baseline_tier() - v2, rule-based
# classification with baseline_tier/effective_tier split (see migration
# 044's header for why the original weighted-score-cutoff version, which
# folded freshness into the score, was replaced). No DB needed, same bar
# test_dashboard_futgg_adapter.py already sets.
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

from app.services.priority_config import PriorityEngineConfig
from app.services.priority_engine import compute_priority

AS_OF = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _row(**overrides) -> Dict[str, Any]:
    defaults = dict(
        source_card_id=84131117,
        rarity="gold_rare",
        sales_count=20,
        sales_window_span_minutes=600.0,
        sales_dispersion_ratio=0.1,
    )
    defaults.update(overrides)
    return defaults


def test_no_freshness_input_exists_anywhere_in_the_snapshot_row_contract():
    # bin_captured_at (or any timestamp) must never be read by this
    # module - passing one in should have zero effect, since it isn't
    # even looked at. This is the literal regression test for migration
    # 042's bug (staleness silently re-promoting a deliberately DORMANT
    # card).
    row_without_timestamp = _row()
    row_with_timestamp = _row(bin_captured_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    a = compute_priority(row_without_timestamp, as_of=AS_OF)
    b = compute_priority(row_with_timestamp, as_of=AS_OF)
    assert a.baseline_tier == b.baseline_tier
    assert a.importance_score == b.importance_score


def test_liquid_gold_rare_lands_in_a_mid_or_high_tier_not_dormant():
    result = compute_priority(_row(), as_of=AS_OF)
    assert result.baseline_tier in ("NORMAL", "HIGH", "CRITICAL")
    assert result.effective_tier == result.baseline_tier
    assert 0.0 <= result.importance_score <= 1.0
    assert result.reason_codes


def test_zero_sales_and_no_interest_bronze_card_is_dormant():
    row = _row(rarity="bronze", sales_count=0, sales_dispersion_ratio=None)
    result = compute_priority(row, watcher_count=0, as_of=AS_OF)
    assert result.baseline_tier == "DORMANT"
    assert result.effective_tier == "DORMANT"
    assert "DORMANT_INACTIVE_TAIL_CARD" in result.reason_codes


def test_a_dormant_card_does_not_climb_tiers_just_because_time_passes():
    # The exact bug this revision fixes: nothing in compute_priority
    # depends on "now" relative to any per-card timestamp at all, so
    # calling it at two different `as_of` values with identical input
    # data must produce an identical result.
    row = _row(rarity="bronze", sales_count=0, sales_dispersion_ratio=None)
    earlier = compute_priority(row, as_of=datetime(2026, 8, 1, tzinfo=timezone.utc))
    later = compute_priority(row, as_of=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert earlier.baseline_tier == later.baseline_tier == "DORMANT"
    assert earlier.importance_score == later.importance_score


def test_icon_card_type_gets_at_least_high_even_with_low_liquidity():
    result = compute_priority(_row(rarity="icon", sales_count=0, sales_dispersion_ratio=None), as_of=AS_OF)
    assert result.baseline_tier in ("HIGH", "CRITICAL")
    assert "PREMIUM_CARD_TYPE" in "".join(result.reason_codes) or "CRITICAL" in result.baseline_tier


def test_special_promo_card_type_also_gets_at_least_high_with_low_liquidity():
    # A low-print-run promo ("special", card_type_scores=0.75) is
    # low-print-run by nature and rarely clears rule_high_liquidity_min
    # on trading volume alone - before this, only icon/hero/tots/totw/
    # fut birthday (>=0.85) cleared the card-type floor, so "special"
    # fell to NORMAL/LOW/DORMANT and its sales data went stale often
    # enough to zero evaluation confidence outright, structurally
    # excluding it from the Opportunities feed regardless of the
    # underlying economics. rule_high_card_type_min now sits exactly at
    # 0.75 so this class clears it on card type alone, same as icon does.
    result = compute_priority(_row(rarity="special", sales_count=0, sales_dispersion_ratio=None), as_of=AS_OF)
    assert result.baseline_tier in ("HIGH", "CRITICAL")
    assert "PREMIUM_CARD_TYPE" in "".join(result.reason_codes) or "CRITICAL" in result.baseline_tier


def test_gold_rare_does_not_get_the_card_type_floor_promotion():
    # The fix must not overshoot into ordinary gold/silver commons -
    # gold_rare scores 0.5, well below the 0.75 floor, so an illiquid one
    # should still fall through to liquidity-based classification (NORMAL
    # or below here), not get swept up by the same rule that now catches
    # "special".
    result = compute_priority(_row(rarity="gold_rare", sales_count=0, sales_dispersion_ratio=None), as_of=AS_OF)
    assert result.baseline_tier not in ("HIGH", "CRITICAL")
    assert "PREMIUM_CARD_TYPE" not in "".join(result.reason_codes)


def test_high_liquidity_and_severe_volatility_reaches_critical():
    result = compute_priority(
        _row(sales_count=200, sales_window_span_minutes=60.0, sales_dispersion_ratio=0.9),
        as_of=AS_OF,
    )
    assert result.baseline_tier == "CRITICAL"
    assert any("CRITICAL" in code for code in result.reason_codes)


def test_active_sbc_trigger_forces_effective_tier_up_without_changing_baseline():
    row = _row(rarity="bronze", sales_count=0, sales_dispersion_ratio=None)
    unmatched = compute_priority(row, as_of=AS_OF)
    matched = compute_priority(row, active_trigger_tiers=["HIGH"], as_of=AS_OF)

    assert unmatched.baseline_tier == "DORMANT"
    assert matched.baseline_tier == "DORMANT"  # baseline is unaffected by triggers
    assert matched.effective_tier == "HIGH"
    assert matched.applied_trigger_tier == "HIGH"
    assert any(code.startswith("TRIGGER_PROMOTED_TO_") for code in matched.reason_codes)


def test_multiple_active_triggers_use_the_strongest():
    row = _row(rarity="bronze", sales_count=0, sales_dispersion_ratio=None)
    result = compute_priority(row, active_trigger_tiers=["LOW", "CRITICAL", "HIGH"], as_of=AS_OF)
    assert result.effective_tier == "CRITICAL"
    assert result.applied_trigger_tier == "CRITICAL"


def test_trigger_weaker_than_baseline_does_not_downgrade_effective_tier():
    result = compute_priority(_row(), active_trigger_tiers=["DORMANT"], as_of=AS_OF)
    assert result.effective_tier == result.baseline_tier
    assert result.effective_tier != "DORMANT"


def test_unrecognised_rarity_falls_back_to_default_card_type_score():
    config = PriorityEngineConfig()
    result = compute_priority(_row(rarity="some_future_rarity_we_never_heard_of"), as_of=AS_OF)
    assert result.card_type_component == config.default_card_type_score


def test_high_watcher_count_increases_user_interest_component():
    low = compute_priority(_row(), watcher_count=0, as_of=AS_OF)
    high = compute_priority(_row(), watcher_count=50, as_of=AS_OF)
    assert high.user_interest_component > low.user_interest_component


def test_historical_opportunity_rate_feeds_opportunity_component_directly():
    none_rate = compute_priority(_row(), historical_opportunity_rate=None, as_of=AS_OF)
    high_rate = compute_priority(_row(), historical_opportunity_rate=0.9, as_of=AS_OF)
    assert none_rate.opportunity_component == 0.0
    assert high_rate.opportunity_component == 0.9


def test_result_card_id_matches_snapshot_row():
    result = compute_priority(_row(source_card_id=999), as_of=AS_OF)
    assert result.card_id == 999


def test_decimal_typed_snapshot_columns_do_not_raise():
    # Regression test: futgg_market_snapshot's sales_window_span_minutes
    # (EXTRACT(...)/60.0) and sales_dispersion_ratio (an explicit
    # ::numeric-cast ROUND) are both typed numeric by Postgres - not
    # double precision - because mixing a float8 EXTRACT() result with a
    # plain numeric literal like 60.0 promotes the whole expression to
    # numeric. asyncpg returns numeric columns as decimal.Decimal, and
    # Python raises TypeError mixing Decimal with float without an
    # explicit cast. This broke run_classification_pass() in production
    # for every card whose snapshot row came from the real materialized
    # view (61% of one real catalogue's cards failed classification in a
    # single pass) - the local dev/test fixtures had used plain floats,
    # which never exposed it. See futgg_intelligence.py's evaluate_card(),
    # which already normalises this exact shape for the same reason.
    row = _row(
        sales_window_span_minutes=Decimal("600.0"),
        sales_dispersion_ratio=Decimal("0.1500"),
    )
    result = compute_priority(row, as_of=AS_OF)
    assert isinstance(result.liquidity_component, float)
    assert isinstance(result.volatility_component, float)

    # Decimal inputs must produce the identical result as float inputs -
    # not just "no crash".
    float_row = _row(sales_window_span_minutes=600.0, sales_dispersion_ratio=0.15)
    float_result = compute_priority(float_row, as_of=AS_OF)
    assert result.liquidity_component == float_result.liquidity_component
    assert result.volatility_component == float_result.volatility_component
    assert result.baseline_tier == float_result.baseline_tier
