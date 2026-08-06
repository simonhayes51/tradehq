# tests/test_sbc_demand_classifier.py
#
# Pure-function tests for app/services/sbc_demand_classifier.py - the
# SBC relevance model revision's core split between resource-demand
# signals (broad, reusable market segments - never individual card
# triggers) and constrained identity-demand triggers (narrow, genuinely
# concentrated pools - still individual triggers, now gated). No DB
# needed.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.priority_config import PriorityEngineConfig
from app.services.sbc_demand_classifier import (
    classify_resource_signal,
    compute_demand_weight,
    evaluate_identity_pool,
    is_identity_requirement,
    is_permanent_event,
)
from app.services.sbc_requirement_parser import ParsedRequirement

CONFIG = PriorityEngineConfig()
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def _req(**overrides) -> ParsedRequirement:
    defaults = dict(parsed=True, ambiguous=False, min_count=1)
    defaults.update(overrides)
    return ParsedRequirement(**defaults)


# =========================================================================
# Resource vs identity classification
# =========================================================================

def test_bare_team_rating_requirement_is_resource_not_identity():
    # Spec test 1: an 88-rated squad creates rating-resource demand, not
    # one trigger per 88-rated card.
    req = _req(team_rating_min=88)
    assert is_identity_requirement(req) is False
    signal = classify_resource_signal(req)
    assert signal is not None
    assert signal.resource_type == "MIN_TEAM_RATING"
    assert signal.resource_value == "88"


def test_totw_requirement_is_resource_not_identity():
    req = _req(rarity="totw")
    assert is_identity_requirement(req) is False
    signal = classify_resource_signal(req)
    assert signal.resource_type == "TOTW"


def test_tots_icon_hero_map_to_their_own_resource_types():
    assert classify_resource_signal(_req(rarity="tots")).resource_type == "TOTS"
    assert classify_resource_signal(_req(rarity="icon")).resource_type == "ICON"
    assert classify_resource_signal(_req(rarity="hero")).resource_type == "HERO"


def test_gold_silver_bronze_rare_map_to_dedicated_types():
    assert classify_resource_signal(_req(rarity="gold_rare")).resource_type == "GOLD_RARE"
    assert classify_resource_signal(_req(rarity="silver_rare")).resource_type == "SILVER_RARE"
    assert classify_resource_signal(_req(rarity="bronze_rare")).resource_type == "BRONZE_RARE"


def test_unrecognised_rarity_falls_back_to_promo_type_not_invented():
    # "fut birthday" etc. - a real, source-derived rarity value, but not
    # one of the dedicated buckets - PROMO_TYPE with the raw value, never
    # a fabricated mapping.
    signal = classify_resource_signal(_req(rarity="fut birthday"))
    assert signal.resource_type == "PROMO_TYPE"
    assert signal.resource_value == "fut birthday"


def test_bare_rating_only_requirement_is_min_rating_resource():
    signal = classify_resource_signal(_req(rating_min=85))
    assert signal.resource_type == "MIN_RATING"
    assert signal.resource_value == "85"


def test_exact_rating_range_where_min_equals_max_is_exact_rating():
    signal = classify_resource_signal(_req(rating_min=85, rating_max=85))
    assert signal.resource_type == "EXACT_RATING"


def test_rarity_plus_rating_is_one_compound_signal_not_two():
    # "83 Rated Gold Rares" - one requirement, one signal, not a
    # fabricated independent "any 83-rated card" demand.
    signal = classify_resource_signal(_req(rarity="gold_rare", rating_min=83))
    assert signal.resource_type == "GOLD_RARE"
    assert "83" in signal.resource_value


def test_named_nation_requirement_is_identity_not_resource():
    # Spec test 9: a large named-league pool must not create individual
    # triggers via the RESOURCE path either - confirmed here it's never
    # even classified as a resource signal at all once identity.
    req = _req(nation="Spain")
    assert is_identity_requirement(req) is True
    assert classify_resource_signal(req) is None


def test_named_club_combined_with_rarity_is_still_identity():
    req = _req(club="Real Madrid", rarity="silver")
    assert is_identity_requirement(req) is True
    assert classify_resource_signal(req) is None


def test_bare_unconstrained_requirement_has_no_signal_at_all():
    req = _req()  # no nation/league/club/rarity/rating/team_rating
    assert classify_resource_signal(req) is None


def test_rating_and_totw_from_one_challenge_are_two_separate_signals():
    # Spec test 13: two requirement lines within the same challenge
    # (rating + TOTW) must retain their own distinct segment identities,
    # not merge into one signal.
    rating_signal = classify_resource_signal(_req(team_rating_min=87))
    totw_signal = classify_resource_signal(_req(rarity="totw"))
    assert rating_signal.resource_type != totw_signal.resource_type
    assert (rating_signal.resource_type, rating_signal.resource_value) != (totw_signal.resource_type, totw_signal.resource_value)


# =========================================================================
# Time decay
# =========================================================================

def test_fresh_requirement_has_full_strength():
    w = compute_demand_weight(
        event_started_at=NOW - timedelta(hours=1), event_expires_at=NOW + timedelta(days=3),
        as_of=NOW, config=CONFIG,
    )
    assert w == 1.0


def test_newly_released_totw_requirement_has_stronger_weight_than_ten_day_old_one():
    # Spec test 3.
    fresh = compute_demand_weight(
        event_started_at=NOW - timedelta(hours=2), event_expires_at=NOW + timedelta(days=10),
        as_of=NOW, config=CONFIG,
    )
    old = compute_demand_weight(
        event_started_at=NOW - timedelta(days=10), event_expires_at=NOW + timedelta(days=3),
        as_of=NOW, config=CONFIG,
    )
    assert fresh > old


def test_old_demand_decays_even_while_event_remains_active():
    # Spec test 4: the SBC hasn't expired (event_expires_at still in the
    # future), but the requirement itself is old - weight must still be
    # low, "active" != "still strong".
    w = compute_demand_weight(
        event_started_at=NOW - timedelta(days=10), event_expires_at=NOW + timedelta(days=30),
        as_of=NOW, config=CONFIG,
    )
    assert w <= CONFIG.demand_decay_bands_hours[168.0]


def test_permanent_tutorial_sbc_has_no_urgent_demand_regardless_of_age():
    # Spec test 5: expires far in the future (or never) - always minimal,
    # even for a requirement added moments ago.
    w = compute_demand_weight(
        event_started_at=NOW - timedelta(minutes=5), event_expires_at=NOW + timedelta(days=1000),
        as_of=NOW, config=CONFIG,
    )
    assert w == CONFIG.demand_decay_minimal_weight

    w_no_expiry = compute_demand_weight(
        event_started_at=NOW - timedelta(minutes=5), event_expires_at=None,
        as_of=NOW, config=CONFIG,
    )
    assert w_no_expiry == CONFIG.demand_decay_minimal_weight


def test_is_permanent_event_matches_compute_demand_weight_own_check():
    assert is_permanent_event(NOW + timedelta(days=1000), NOW, CONFIG) is True
    assert is_permanent_event(NOW + timedelta(days=3), NOW, CONFIG) is False
    assert is_permanent_event(None, NOW, CONFIG) is True


def test_repeatable_upgrade_does_not_remain_at_full_strength_forever():
    # Spec test 6: repeatable alone must not exempt a requirement from
    # normal age-based decay - only event_expires_at being far out (the
    # permanent check) does that. A repeatable SBC that started 10 days
    # ago and expires in 3 days (not permanent) still decays normally.
    fresh_repeatable = compute_demand_weight(
        event_started_at=NOW - timedelta(hours=1), event_expires_at=NOW + timedelta(days=3),
        as_of=NOW, config=CONFIG,
    )
    old_repeatable = compute_demand_weight(
        event_started_at=NOW - timedelta(days=10), event_expires_at=NOW + timedelta(days=3),
        as_of=NOW, config=CONFIG,
    )
    assert old_repeatable < fresh_repeatable


def test_reinforcement_multiplier_can_preserve_an_otherwise_decaying_signal():
    # Spec test 7 (structural hook only - no market computation shipped
    # this round, see sbc_demand_classifier.py's own module docstring).
    decaying = compute_demand_weight(
        event_started_at=NOW - timedelta(days=4), event_expires_at=NOW + timedelta(days=10),
        as_of=NOW, config=CONFIG, reinforcement_multiplier=1.0,
    )
    reinforced = compute_demand_weight(
        event_started_at=NOW - timedelta(days=4), event_expires_at=NOW + timedelta(days=10),
        as_of=NOW, config=CONFIG, reinforcement_multiplier=1.8,
    )
    assert reinforced > decaying


def test_decay_is_deterministic_for_identical_inputs():
    # Spec test 19.
    kwargs = dict(
        event_started_at=NOW - timedelta(hours=30), event_expires_at=NOW + timedelta(days=5),
        as_of=NOW, config=CONFIG,
    )
    assert compute_demand_weight(**kwargs) == compute_demand_weight(**kwargs)


def test_decay_bands_are_configurable():
    custom_config = PriorityEngineConfig(demand_decay_bands_hours={12.0: 1.0, 48.0: 0.3})
    w = compute_demand_weight(
        event_started_at=NOW - timedelta(hours=20), event_expires_at=NOW + timedelta(days=5),
        as_of=NOW, config=custom_config,
    )
    assert w == 0.3  # falls into the 48h band under the custom config


def test_unknown_event_start_time_is_treated_as_minimal_not_fresh():
    w = compute_demand_weight(event_started_at=None, event_expires_at=NOW + timedelta(days=5), as_of=NOW, config=CONFIG)
    assert w == CONFIG.demand_decay_minimal_weight


# =========================================================================
# Identity-trigger pool gating
# =========================================================================

def test_large_named_league_pool_is_rejected():
    # Spec test 9 (the actual rejection example): "1 Premier League
    # player from 1,000+ cards".
    result = evaluate_identity_pool(
        required_count=1, pool_size=1200, is_new_trigger=True,
        event_started_at=NOW, as_of=NOW, config=CONFIG,
    )
    assert result.accepted is False
    assert result.reason == "pool_too_large"


def test_small_constrained_league_plus_rarity_pool_is_accepted():
    # Spec test 10 / the task's own worked example: 6 Saudi League silver
    # players from a pool of 40.
    result = evaluate_identity_pool(
        required_count=6, pool_size=40, is_new_trigger=True,
        event_started_at=NOW, as_of=NOW, config=CONFIG,
    )
    assert result.accepted is True
    assert result.pool_size == 40


def test_tiny_pool_is_accepted_via_small_pool_override_even_at_low_ratio():
    result = evaluate_identity_pool(
        required_count=1, pool_size=5, is_new_trigger=True,
        event_started_at=NOW, as_of=NOW, config=CONFIG,
    )
    assert result.accepted is True
    assert result.reason == "small_pool_override"


def test_moderate_pool_below_concentration_ratio_is_rejected():
    result = evaluate_identity_pool(
        required_count=1, pool_size=50, is_new_trigger=True,
        event_started_at=NOW, as_of=NOW, config=CONFIG,
    )
    assert result.accepted is False
    assert result.reason == "concentration_too_low"


def test_empty_pool_is_rejected_cleanly():
    result = evaluate_identity_pool(
        required_count=1, pool_size=0, is_new_trigger=True,
        event_started_at=NOW, as_of=NOW, config=CONFIG,
    )
    assert result.accepted is False
    assert result.reason == "empty_pool"


def test_new_trigger_from_a_stale_event_is_rejected_on_age():
    result = evaluate_identity_pool(
        required_count=6, pool_size=40, is_new_trigger=True,
        event_started_at=NOW - timedelta(hours=CONFIG.identity_trigger_max_event_age_hours_for_new + 10),
        as_of=NOW, config=CONFIG,
    )
    assert result.accepted is False
    assert result.reason == "event_too_old_for_new_trigger"


def test_reconfirmation_of_an_existing_trigger_is_not_age_gated():
    # A pool that's otherwise identical but is_new_trigger=False (already
    # actively triggered) must not be rejected purely for event age -
    # that would fight the grace-period mechanism.
    result = evaluate_identity_pool(
        required_count=6, pool_size=40, is_new_trigger=False,
        event_started_at=NOW - timedelta(hours=CONFIG.identity_trigger_max_event_age_hours_for_new + 10),
        as_of=NOW, config=CONFIG,
    )
    assert result.accepted is True
