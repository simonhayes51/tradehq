# tests/test_priority_engine.py
#
# Pure-function tests for app/services/priority_engine.py's
# compute_priority() - Phase 1 of the adaptive scrape-priority engine.
# No DB needed, same bar test_dashboard_futgg_adapter.py already sets.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
        bin_captured_at=AS_OF - timedelta(minutes=10),
    )
    defaults.update(overrides)
    return defaults


def test_liquid_fresh_gold_rare_lands_in_a_mid_tier_not_dormant():
    result = compute_priority(_row(), as_of=AS_OF)
    assert result.tier in ("NORMAL", "HIGH", "CRITICAL")
    assert 0.0 <= result.priority_score <= 1.0


def test_zero_sales_and_never_captured_card_is_not_scored_as_critical():
    row = _row(sales_count=0, sales_dispersion_ratio=None, bin_captured_at=None)
    result = compute_priority(row, as_of=AS_OF)
    # Never-captured pulls freshness to 1.0 (discovery), but zero
    # liquidity/volatility/interest/opportunity/event should still keep
    # the blended score well short of CRITICAL for an otherwise-inert card.
    assert result.liquidity_component == 0.0
    assert result.freshness_component == 1.0
    assert result.tier != "CRITICAL"


def test_icon_card_type_component_beats_bronze():
    icon = compute_priority(_row(rarity="icon"), as_of=AS_OF)
    bronze = compute_priority(_row(rarity="bronze"), as_of=AS_OF)
    assert icon.card_type_component > bronze.card_type_component
    assert icon.priority_score > bronze.priority_score


def test_unrecognised_rarity_falls_back_to_default_card_type_score():
    config = PriorityEngineConfig()
    result = compute_priority(_row(rarity="some_future_rarity_we_never_heard_of"), as_of=AS_OF)
    assert result.card_type_component == config.default_card_type_score


def test_active_sbc_match_forces_event_component_and_floors_the_tier():
    unmatched = compute_priority(_row(sales_count=0, sales_dispersion_ratio=None), as_of=AS_OF)
    matched = compute_priority(
        _row(sales_count=0, sales_dispersion_ratio=None), forced_by_event_id=42, as_of=AS_OF,
    )
    assert unmatched.event_component == 0.0
    assert matched.event_component == 1.0
    assert matched.forced_by_event_id == 42
    # Even a completely inert card (no sales, no interest) must be
    # floor-clamped to at least the configured event_match_floor_tier -
    # this is the literal "SBC posts, promote immediately" requirement,
    # not just a nudge to the blended score.
    config = PriorityEngineConfig()
    from app.services.priority_config import TIERS
    assert TIERS.index(matched.tier) <= TIERS.index(config.event_match_floor_tier)
    assert matched.tier != unmatched.tier


def test_high_watcher_count_increases_user_interest_component():
    low_interest = compute_priority(_row(), watcher_count=0, as_of=AS_OF)
    high_interest = compute_priority(_row(), watcher_count=50, as_of=AS_OF)
    assert high_interest.user_interest_component > low_interest.user_interest_component
    assert high_interest.priority_score > low_interest.priority_score


def test_high_dispersion_increases_volatility_component():
    calm = compute_priority(_row(sales_dispersion_ratio=0.02), as_of=AS_OF)
    volatile = compute_priority(_row(sales_dispersion_ratio=0.9), as_of=AS_OF)
    assert volatile.volatility_component > calm.volatility_component


def test_stale_price_increases_freshness_component():
    fresh = compute_priority(_row(bin_captured_at=AS_OF - timedelta(minutes=5)), as_of=AS_OF)
    stale = compute_priority(_row(bin_captured_at=AS_OF - timedelta(hours=20)), as_of=AS_OF)
    assert stale.freshness_component > fresh.freshness_component


def test_historical_opportunity_rate_feeds_opportunity_component_directly():
    none_rate = compute_priority(_row(), historical_opportunity_rate=None, as_of=AS_OF)
    high_rate = compute_priority(_row(), historical_opportunity_rate=0.9, as_of=AS_OF)
    assert none_rate.opportunity_component == 0.0
    assert high_rate.opportunity_component == 0.9


def test_result_card_id_matches_snapshot_row():
    result = compute_priority(_row(source_card_id=999), as_of=AS_OF)
    assert result.card_id == 999
