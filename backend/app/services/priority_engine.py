# app/services/priority_engine.py
"""
Pure classification function for the adaptive scrape-priority engine -
dict/scalar inputs in, a PriorityResult dataclass out, no I/O. Mirrors
futgg_intelligence.py's "evaluate_card() is pure and fully unit-testable"
convention deliberately.

v2 (see migration 044's header): baseline_tier is assigned by an
explicit, ordered rule table (classify_baseline_tier() below) - the
spec's own ask, "implement a transparent rule-based baseline first...
tier assignment should initially be explainable through stored reason
codes" - not by importance_score crossing a cutoff. importance_score is
still computed (same weighted-blend shape as before) and stored, but
purely for observability/analysis; nothing here or downstream uses it
to decide a tier.

CRITICALLY: no freshness/staleness input exists anywhere in this module.
The original version blended "how overdue is this card" into the same
score that decided its tier, which meant a DORMANT card aging exactly as
designed would climb back toward promotion purely because its own
growing staleness counted as a signal. Scheduling urgency ("how overdue
relative to the interval its effective tier implies") is a claim-order
concept for a later milestone and must never again be a scoring input
here.

baseline_tier vs effective_tier: effective_tier is baseline_tier merged
with any active priority_triggers rows (passed in as
active_trigger_tiers - the DB read for which triggers are currently
active lives in priority_recompute.py, not here) via higher_tier(). When
a trigger expires, effective_tier reverts to baseline_tier directly -
this module has no memory of past triggers, so there is nothing to
"stage back down" from.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.services.futgg_intelligence import _compute_liquidity_score
from app.services.priority_config import (
    PRIORITY_ENGINE_CONFIG,
    TIERS,
    PriorityEngineConfig,
    higher_tier,
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _saturating_score(value: float, reference: float) -> float:
    """log-scaled [0,1] score that never quite reaches 1.0 for a finite
    input - same shape trading_math.liquidity_score already uses for its
    hourly-rate component, reused here for "more is better, but with
    heavily diminishing returns" signals (user interest)."""
    if value <= 0 or reference <= 0:
        return 0.0
    return _clamp(math.log1p(value) / math.log1p(reference))


@dataclass(frozen=True)
class PriorityResult:
    card_id: int
    baseline_tier: str
    effective_tier: str
    importance_score: float
    liquidity_component: float
    card_type_component: float
    opportunity_component: float
    user_interest_component: float
    volatility_component: float
    event_component: float
    reason_codes: List[str] = field(default_factory=list)
    applied_trigger_tier: Optional[str] = None


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


def _volatility_component(dispersion_ratio: Optional[float], reference_ratio: float) -> float:
    """Higher sale-price dispersion relative to the median = a market
    that's currently moving. Same input confidence scoring uses
    (inverted there - dispersion reduces confidence in a stale read;
    here, dispersion increases the value of checking again soon)."""
    if dispersion_ratio is None or reference_ratio <= 0:
        return 0.0
    return _clamp(dispersion_ratio / reference_ratio)


def classify_baseline_tier(
    *,
    liquidity_component: float,
    card_type_component: float,
    opportunity_component: float,
    volatility_component: float,
    sales_count: int,
    user_interest_component: float,
    config: PriorityEngineConfig,
) -> tuple[str, List[str]]:
    """First matching rule wins - ordered highest-priority-tier first.
    Every branch records a reason code so a stored classification is
    always explainable without re-deriving it."""
    if liquidity_component >= config.rule_critical_liquidity_min and (
        volatility_component >= config.rule_critical_volatility_min
        or opportunity_component >= config.rule_critical_opportunity_min
    ):
        reason = (
            "CRITICAL_HIGH_LIQUIDITY_SEVERE_MOVEMENT"
            if volatility_component >= config.rule_critical_volatility_min
            else "CRITICAL_HIGH_LIQUIDITY_STRONG_OPPORTUNITY"
        )
        return "CRITICAL", [reason]

    if card_type_component >= config.rule_high_card_type_min and liquidity_component >= config.rule_high_liquidity_min:
        return "CRITICAL", ["CRITICAL_PREMIUM_CARD_TYPE_LIQUID"]

    if liquidity_component >= config.rule_high_liquidity_min:
        return "HIGH", ["HIGH_LIQUID_ACTIVE_TRADING"]

    if card_type_component >= config.rule_high_card_type_min:
        return "HIGH", ["HIGH_PREMIUM_CARD_TYPE"]

    if liquidity_component >= config.rule_normal_liquidity_min or sales_count >= config.rule_normal_min_sales_count:
        return "NORMAL", ["NORMAL_LIQUID_BASE_CARD"]

    if sales_count >= config.rule_low_min_sales_count or user_interest_component > 0:
        return "LOW", ["LOW_MINIMAL_ACTIVITY"]

    return "DORMANT", ["DORMANT_INACTIVE_TAIL_CARD"]


def compute_priority(
    snapshot_row: Dict[str, Any],
    *,
    watcher_count: int = 0,
    historical_opportunity_rate: Optional[float] = None,
    active_trigger_tiers: Sequence[str] = (),
    as_of: Optional[datetime] = None,
    volatility_reference_ratio: float = 0.5,
    user_interest_reference: float = 25.0,
    config: PriorityEngineConfig = PRIORITY_ENGINE_CONFIG,
) -> PriorityResult:
    """snapshot_row needs: source_card_id, rarity, sales_count,
    sales_window_span_minutes, sales_dispersion_ratio (all from
    futgg_market_snapshot - NOT bin_captured_at, deliberately: no
    freshness input exists in this module, see the module docstring).
    active_trigger_tiers is the list of forced_min_tier values from any
    currently-active priority_triggers rows for this card (the DB read
    lives in priority_recompute.py, not here)."""
    as_of = as_of or datetime.now(timezone.utc)
    card_id = int(snapshot_row["source_card_id"])

    # Postgres numerics arrive from asyncpg as Decimal; normalise once
    # here rather than at each arithmetic site (mixing Decimal with a
    # float literal raises TypeError) - same fix futgg_intelligence.py's
    # evaluate_card() already applies to this exact snapshot shape.
    # sales_window_span_minutes/sales_dispersion_ratio come out of
    # futgg_market_snapshot's own computed columns (migration 041's
    # EXTRACT(...)/60.0 and ::numeric-cast ROUND respectively), both of
    # which Postgres types as numeric, not double precision.
    span_minutes = snapshot_row.get("sales_window_span_minutes")
    span_minutes = float(span_minutes) if span_minutes is not None else None
    dispersion_ratio = snapshot_row.get("sales_dispersion_ratio")
    dispersion_ratio = float(dispersion_ratio) if dispersion_ratio is not None else None

    sales_count = int(snapshot_row.get("sales_count") or 0)
    liquidity = _compute_liquidity_score(sales_count, span_minutes)
    liquidity_component = liquidity if liquidity is not None else 0.0

    card_type_component = _card_type_component(snapshot_row.get("rarity"), config)
    opportunity_component = _clamp(historical_opportunity_rate) if historical_opportunity_rate is not None else 0.0
    user_interest_component = _saturating_score(watcher_count, user_interest_reference)
    volatility_component = _volatility_component(dispersion_ratio, volatility_reference_ratio)
    event_component = 1.0 if active_trigger_tiers else 0.0

    importance_score = _clamp(
        config.weight_liquidity * liquidity_component
        + config.weight_card_type * card_type_component
        + config.weight_opportunity * opportunity_component
        + config.weight_user_interest * user_interest_component
        + config.weight_volatility * volatility_component
        + config.weight_event * event_component
    )

    baseline_tier, reason_codes = classify_baseline_tier(
        liquidity_component=liquidity_component,
        card_type_component=card_type_component,
        opportunity_component=opportunity_component,
        volatility_component=volatility_component,
        sales_count=sales_count,
        user_interest_component=user_interest_component,
        config=config,
    )

    applied_trigger_tier: Optional[str] = None
    effective_tier = baseline_tier
    for trigger_tier in active_trigger_tiers:
        if trigger_tier not in TIERS:
            continue
        if applied_trigger_tier is None or higher_tier(trigger_tier, applied_trigger_tier) == trigger_tier:
            applied_trigger_tier = trigger_tier
    if applied_trigger_tier is not None:
        effective_tier = higher_tier(baseline_tier, applied_trigger_tier)
        if effective_tier != baseline_tier:
            reason_codes = reason_codes + [f"TRIGGER_PROMOTED_TO_{effective_tier}"]

    return PriorityResult(
        card_id=card_id,
        baseline_tier=baseline_tier,
        effective_tier=effective_tier,
        importance_score=importance_score,
        liquidity_component=liquidity_component,
        card_type_component=card_type_component,
        opportunity_component=opportunity_component,
        user_interest_component=user_interest_component,
        volatility_component=volatility_component,
        event_component=event_component,
        reason_codes=reason_codes,
        applied_trigger_tier=applied_trigger_tier,
    )
