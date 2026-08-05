# app/services/priority_engine.py
"""
Pure scoring function for the adaptive scrape-priority engine - dict/
scalar inputs in, a PriorityResult dataclass out, no I/O. Mirrors
futgg_intelligence.py's own "evaluate_card() is pure and fully
unit-testable" convention deliberately: the whole point of Phase 1 is to
validate this function's output against real production data before
anything downstream (scheduling, Trade Finder gating) is allowed to
depend on it, and that's only possible to do quickly if it has no
database/network dependency of its own.

The I/O - reading futgg_market_snapshot, watchlist_popularity, past
outcomes, and active SBC matches, then writing card_priority - lives in
priority_recompute.py, exactly the same split futgg_intelligence.py/
market_data_provider.py already use.

SCORE VS TIER
-------------
priority_score is a continuous [0,1] blend of seven independently
inspectable components (see PriorityEngineConfig). tier is a DERIVED
label from fixed score cutoffs - useful for observability, the refresh-
interval lookup, and the 48h-inactivity demotion state machine, but not
itself a separate source of truth. The one exception: an active SBC/
event match floor-clamps the tier to at least
config.event_match_floor_tier regardless of the blended score, because a
weighted average can only ever nudge a card upward - the spec calls for
an SBC match to promote a card *immediately*, not "probably soon once
enough other signals agree".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services import trading_math as tm
from app.services.futgg_intelligence import _compute_liquidity_score
from app.services.priority_config import PRIORITY_ENGINE_CONFIG, TIERS, PriorityEngineConfig


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _saturating_score(value: float, reference: float) -> float:
    """log-scaled [0,1] score that never quite reaches 1.0 for a finite
    input - same shape trading_math.liquidity_score already uses for its
    hourly-rate component, reused here for any "more is better, but with
    heavily diminishing returns" signal (user interest, in this module)."""
    if value <= 0 or reference <= 0:
        return 0.0
    return _clamp(math.log1p(value) / math.log1p(reference))


@dataclass(frozen=True)
class PriorityResult:
    card_id: int
    priority_score: float
    tier: str
    liquidity_component: float
    freshness_component: float
    card_type_component: float
    opportunity_component: float
    user_interest_component: float
    volatility_component: float
    event_component: float
    forced_by_event_id: Optional[int]


def _tier_for_score(score: float, config: PriorityEngineConfig) -> str:
    if score >= config.tier_critical_min:
        return "CRITICAL"
    if score >= config.tier_high_min:
        return "HIGH"
    if score >= config.tier_normal_min:
        return "NORMAL"
    if score >= config.tier_low_min:
        return "LOW"
    return "DORMANT"


def _apply_event_floor(tier: str, forced: bool, config: PriorityEngineConfig) -> str:
    if not forced:
        return tier
    floor = config.event_match_floor_tier
    if floor not in TIERS:
        return tier
    # TIERS is ordered CRITICAL..DORMANT (highest priority first), so a
    # lower index means higher priority - clamp to whichever of
    # (computed tier, floor) has the lower index.
    return TIERS[min(TIERS.index(tier), TIERS.index(floor))]


def _card_type_component(rarity: Optional[str], config: PriorityEngineConfig) -> float:
    if not rarity:
        return config.default_card_type_score
    lowered = rarity.strip().lower()
    if lowered in config.card_type_scores:
        return config.card_type_scores[lowered]
    for key, score in config.card_type_scores.items():
        if key in lowered:
            return score
    return config.default_card_type_score


def _freshness_component(
    price_age_minutes: Optional[int], reference_minutes: float
) -> float:
    """Self-correcting staleness signal: the longer since a card's price
    was last captured, the higher this climbs, so a card that's fallen
    behind its own schedule naturally regains priority rather than
    staying stuck wherever its other components put it. A card that has
    NEVER been captured (None) is treated as maximally stale - the
    correct behaviour for discovery, not a missing-data edge case to
    special-case away."""
    if price_age_minutes is None:
        return 1.0
    if reference_minutes <= 0:
        return 1.0
    return _clamp(price_age_minutes / reference_minutes)


def _volatility_component(
    dispersion_ratio: Optional[float], reference_ratio: float
) -> float:
    """Higher sale-price dispersion relative to the median = a market
    that's currently moving, which is exactly what should pull a card's
    refresh cadence tighter - the inverse relationship confidence scoring
    uses dispersion for (there, high dispersion REDUCES confidence in a
    stale read; here, high dispersion INCREASES the value of a fresh
    one)."""
    if dispersion_ratio is None or reference_ratio <= 0:
        return 0.0
    return _clamp(dispersion_ratio / reference_ratio)


def compute_priority(
    snapshot_row: Dict[str, Any],
    *,
    watcher_count: int = 0,
    ownership_count: int = 0,
    recent_trade_count: int = 0,
    historical_opportunity_rate: Optional[float] = None,
    forced_by_event_id: Optional[int] = None,
    as_of: Optional[datetime] = None,
    freshness_reference_minutes: float = 720.0,
    volatility_reference_ratio: float = 0.5,
    user_interest_reference: float = 25.0,
    config: PriorityEngineConfig = PRIORITY_ENGINE_CONFIG,
) -> PriorityResult:
    """snapshot_row is a futgg_market_snapshot row (or anything with the
    same keys: source_card_id, rarity, sales_count, sales_window_span_
    minutes, sales_dispersion_ratio, bin_captured_at). watcher_count/
    ownership_count/recent_trade_count are the user-interest inputs
    priority_recompute.py gathers from watchlist_popularity/trades.
    historical_opportunity_rate is [0,1] or None (Phase 1: always None,
    the trailing-window outcome join lands in Phase 2 - see
    priority_recompute.py)."""
    as_of = as_of or datetime.now(timezone.utc)
    card_id = int(snapshot_row["source_card_id"])

    sales_count = int(snapshot_row.get("sales_count") or 0)
    span_minutes = snapshot_row.get("sales_window_span_minutes")
    liquidity = _compute_liquidity_score(sales_count, span_minutes)
    liquidity_component = liquidity if liquidity is not None else 0.0

    bin_captured_at = snapshot_row.get("bin_captured_at")
    price_age_minutes: Optional[int] = None
    if bin_captured_at is not None:
        if bin_captured_at.tzinfo is None:
            bin_captured_at = bin_captured_at.replace(tzinfo=timezone.utc)
        price_age_minutes = max(0, int((as_of - bin_captured_at).total_seconds() // 60))
    freshness_component = _freshness_component(price_age_minutes, freshness_reference_minutes)

    card_type_component = _card_type_component(snapshot_row.get("rarity"), config)

    opportunity_component = _clamp(historical_opportunity_rate) if historical_opportunity_rate is not None else 0.0

    user_interest_component = _saturating_score(
        watcher_count + ownership_count + (recent_trade_count * 2), user_interest_reference
    )

    volatility_component = _volatility_component(
        snapshot_row.get("sales_dispersion_ratio"), volatility_reference_ratio
    )

    forced = forced_by_event_id is not None
    event_component = 1.0 if forced else 0.0

    priority_score = _clamp(
        config.weight_liquidity * liquidity_component
        + config.weight_freshness * freshness_component
        + config.weight_card_type * card_type_component
        + config.weight_opportunity * opportunity_component
        + config.weight_user_interest * user_interest_component
        + config.weight_volatility * volatility_component
        + config.weight_event * event_component
    )

    tier = _apply_event_floor(_tier_for_score(priority_score, config), forced, config)

    return PriorityResult(
        card_id=card_id,
        priority_score=priority_score,
        tier=tier,
        liquidity_component=liquidity_component,
        freshness_component=freshness_component,
        card_type_component=card_type_component,
        opportunity_component=opportunity_component,
        user_interest_component=user_interest_component,
        volatility_component=volatility_component,
        event_component=event_component,
        forced_by_event_id=forced_by_event_id,
    )
