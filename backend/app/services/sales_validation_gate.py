# app/services/sales_validation_gate.py
"""
Who gets to spend an expensive FUT.GG sales request, and why.

THE PROBLEM THIS SOLVES
-----------------------
futgg_sales_sync.py currently refreshes the whole catalogue on a
rating-band timer: 85+ hourly, 80-84 four-hourly, 75-79 twelve-hourly,
everything else every 48h. Nothing in that schedule asks whether a card
has done anything worth looking at. A card nobody trades, nobody watches,
and whose asking price has not moved in a week is read on exactly the
same cadence as a card in the middle of a live opportunity.

Each of those reads is a headed-Chromium page navigation. That is what
the hosting bill is.

This module replaces "your timer elapsed" with "you earned it", and makes
every expensive request carry a machine-readable reason, so the question
"why did we spend money on this card?" always has an answer on record.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not decide that a card is a buy. A wake-up means "this card
deserves transaction validation", never "recommend this". Those are
separate questions and conflating them would let a cost-control mechanism
quietly become a recommendation engine.

It also does not touch the transport. scrape_queue/http_client stay
unused, and futgg_sales_sync.py's own throttle and circuit breaker - whose
constants came out of a real incident, 144 HTTP 429s in one run - are not
modified. This gate decides WHICH cards are offered to that worker. How
they are fetched is unchanged.

EVIDENCE TYPES
--------------
BIN is an asking price. A market level inferred from repeated asks is not
the same fact as a level confirmed by completed sales, and the difference
must survive into whatever reads this later - so it is an explicit enum
rather than a comment. Nothing here may be substituted for the
sales-derived anchor in futgg_intelligence.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.services.bin_market_signal import (
    INSUFFICIENT_BIN_HISTORY, MOVING_DOWN, MOVING_UP, POSSIBLE_SPIKE,
    POSSIBLE_UNDERCUT, VOLATILE, BinMarketSignal,
)

# =============================================================================
# Evidence types - see module docstring
# =============================================================================
EVIDENCE_BIN_DERIVED = "BIN_DERIVED"
EVIDENCE_SALES_CONFIRMED = "SALES_CONFIRMED"
EVIDENCE_HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
EVIDENCE_TYPES = (EVIDENCE_BIN_DERIVED, EVIDENCE_SALES_CONFIRMED, EVIDENCE_HIGH_CONFIDENCE)


# =============================================================================
# Reason codes
# =============================================================================
BIN_UNDERCUT_VALIDATION = "BIN_UNDERCUT_VALIDATION"
BIN_SPIKE_VALIDATION = "BIN_SPIKE_VALIDATION"
BIN_MOVEMENT_VALIDATION = "BIN_MOVEMENT_VALIDATION"
BIN_VOLATILITY_VALIDATION = "BIN_VOLATILITY_VALIDATION"
TRADE_CANDIDATE = "TRADE_CANDIDATE"
USER_POSITION = "USER_POSITION"
WATCHLIST_INTEREST = "WATCHLIST_INTEREST"
SBC_RESOURCE_DEMAND = "SBC_RESOURCE_DEMAND"
SBC_CONSTRAINED_DEMAND = "SBC_CONSTRAINED_DEMAND"
NEW_RELEASE = "NEW_RELEASE"
PERIODIC_DISCOVERY = "PERIODIC_DISCOVERY"
NEVER_READ_BACKFILL = "NEVER_READ_BACKFILL"
MANUAL = "MANUAL"

#: Deterministic budget priority, most important first (spec item 10).
#: Discovery sits last by construction so it can never consume capacity a
#: genuine opportunity needed - the explicit requirement.
REASON_PRIORITY: List[str] = [
    TRADE_CANDIDATE,
    USER_POSITION,
    MANUAL,
    BIN_UNDERCUT_VALIDATION,
    BIN_SPIKE_VALIDATION,
    SBC_CONSTRAINED_DEMAND,
    SBC_RESOURCE_DEMAND,
    NEW_RELEASE,
    BIN_MOVEMENT_VALIDATION,
    BIN_VOLATILITY_VALIDATION,
    WATCHLIST_INTEREST,
    NEVER_READ_BACKFILL,
    PERIODIC_DISCOVERY,
]
_REASON_RANK = {reason: rank for rank, reason in enumerate(REASON_PRIORITY)}

SKIP_NOT_ELIGIBLE = "NOT_ELIGIBLE"
SKIP_EVIDENCE_FRESH = "EVIDENCE_STILL_FRESH"
SKIP_COOLDOWN = "VALIDATION_COOLDOWN"
SKIP_PER_CARD_LIMIT = "PER_CARD_DAILY_LIMIT"
SKIP_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


# =============================================================================
# Validation outcomes (spec item 13)
# =============================================================================
SALES_CONFIRMED_UNDERCUT = "SALES_CONFIRMED_UNDERCUT"
SALES_CONFIRMED_SPIKE = "SALES_CONFIRMED_SPIKE"
BIN_BASELINE_LAGGING = "BIN_BASELINE_LAGGING"
INSUFFICIENT_TRANSACTION_EVIDENCE = "INSUFFICIENT_TRANSACTION_EVIDENCE"
SALES_AGREE_WITH_BIN = "SALES_AGREE_WITH_BIN"


@dataclass(frozen=True)
class SalesValidationConfig:
    """Every threshold that governs spending. All env-overridable.

    Defaults are set from the audited production schedule (see
    scripts/bin_first_shadow_report.py's baseline calculation), not
    guessed in isolation.
    """

    version: str = "sales-gate-1.0"

    # ---- Maximum acceptable age of existing sales evidence, per class --
    # A card only earns a request when what we already hold is older than
    # its class allows. This is the single biggest lever: most of the
    # catalogue lands in `cold`/`dormant` and therefore almost never
    # qualifies.
    active_max_evidence_age_minutes: int = 45
    warm_max_evidence_age_minutes: int = 360        # 6h
    cold_max_evidence_age_minutes: int = 4320       # 3d
    dormant_max_evidence_age_minutes: int = 43200   # 30d

    # ---- Anti-cost-explosion safeguards (spec item 9) -------------------
    #: After ANY validation, a card cannot be validated again for this
    #: long regardless of what its BIN does. Without this a card
    #: oscillating around a threshold re-triggers every bulk cycle.
    validation_cooldown_minutes: int = 90
    #: Hard per-card ceiling, so one pathological card cannot consume the
    #: global budget on its own.
    max_validations_per_card_per_day: int = 6
    #: Anomaly-triggered work specifically is capped per cycle, separately
    #: from the global budget, because a market-wide event (a promo drop)
    #: moves thousands of cards at once and would otherwise convert
    #: directly into thousands of requests.
    max_anomaly_validations_per_cycle: int = 40

    #: A BIN anomaly must survive this many consecutive bulk observations
    #: before it can spend anything. One malformed observation - which the
    #: bulk feed demonstrably produces - must never be sufficient.
    min_anomaly_persistence_observations: int = 2

    #: Thin/illiquid markets need a bigger move to qualify: FC26 is full
    #: of cards whose ask wanders because listings expire, not because
    #: anything traded.
    thin_market_anomaly_multiplier: float = 1.5

    # ---- Global budget (spec item 10) ----------------------------------
    max_requests_per_hour: int = 400
    max_requests_per_day: int = 6000

    # ---- Discovery / control sample (spec item 11) ---------------------
    #: Deliberately tiny. Its job is to measure what BIN-first misses, not
    #: to provide coverage.
    discovery_sample_per_day: int = 120
    #: Share of the daily budget discovery may never exceed, belt and
    #: braces alongside its own count.
    discovery_max_budget_share: float = 0.05

    # ---- New release ----------------------------------------------------
    new_release_age_hours: int = 72

    def max_evidence_age_minutes(self, card_class: str) -> int:
        return {
            "active": self.active_max_evidence_age_minutes,
            "warm": self.warm_max_evidence_age_minutes,
            "cold": self.cold_max_evidence_age_minutes,
            "dormant": self.dormant_max_evidence_age_minutes,
        }.get(card_class, self.dormant_max_evidence_age_minutes)

    def as_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def _env_int(name: str, default: int) -> int:
    import os
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    import os
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    import os
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_config() -> SalesValidationConfig:
    return SalesValidationConfig(
        active_max_evidence_age_minutes=_env_int("SALES_GATE_ACTIVE_MAX_AGE_MIN", 45),
        warm_max_evidence_age_minutes=_env_int("SALES_GATE_WARM_MAX_AGE_MIN", 360),
        cold_max_evidence_age_minutes=_env_int("SALES_GATE_COLD_MAX_AGE_MIN", 4320),
        dormant_max_evidence_age_minutes=_env_int("SALES_GATE_DORMANT_MAX_AGE_MIN", 43200),
        validation_cooldown_minutes=_env_int("SALES_GATE_COOLDOWN_MIN", 90),
        max_validations_per_card_per_day=_env_int("SALES_GATE_MAX_PER_CARD_PER_DAY", 6),
        max_anomaly_validations_per_cycle=_env_int("SALES_GATE_MAX_ANOMALY_PER_CYCLE", 40),
        min_anomaly_persistence_observations=_env_int("SALES_GATE_MIN_ANOMALY_PERSISTENCE", 2),
        thin_market_anomaly_multiplier=_env_float("SALES_GATE_THIN_MARKET_MULTIPLIER", 1.5),
        max_requests_per_hour=_env_int("SALES_SYNC_MAX_REQUESTS_PER_HOUR", 400),
        max_requests_per_day=_env_int("SALES_SYNC_MAX_REQUESTS_PER_DAY", 6000),
        discovery_sample_per_day=_env_int("SALES_GATE_DISCOVERY_PER_DAY", 120),
        discovery_max_budget_share=_env_float("SALES_GATE_DISCOVERY_BUDGET_SHARE", 0.05),
        new_release_age_hours=_env_int("SALES_GATE_NEW_RELEASE_AGE_HOURS", 72),
    )


SALES_VALIDATION_CONFIG = load_config()


# =============================================================================
# Kill switches (spec item 15). All default OFF.
# =============================================================================
def bin_first_engine_enabled() -> bool:
    """Phase A. Compute signals and decisions; change no behaviour."""
    return _env_bool("BIN_FIRST_MARKET_ENGINE_ENABLED", False)


def eligibility_gate_enabled() -> bool:
    """Phase B/C. Actually suppress requests. Requires the engine flag too."""
    return _env_bool("SALES_SYNC_ELIGIBILITY_GATE_ENABLED", False)


def request_budget_enabled() -> bool:
    return _env_bool("SALES_SYNC_REQUEST_BUDGET_ENABLED", False)


# =============================================================================
# Inputs
# =============================================================================
@dataclass
class CardContext:
    """Everything the gate needs about one card that is NOT BIN-derived.

    Assembled by the caller from tables it already owns. Every external
    demand field is optional and defaults to "no signal", so the gate
    works correctly when the SBC layer is absent or mid-revision - the
    explicit requirement that BIN-first must not depend on the SBC work
    being right (spec item 16).
    """

    card_id: int
    rating: Optional[int] = None
    is_active: bool = True
    is_tradeable: Optional[bool] = True

    #: Newest completed sale we already hold. None = never read.
    latest_sale_at: Optional[datetime] = None
    sales_count_14d: int = 0
    never_read: bool = False

    first_seen_at: Optional[datetime] = None

    # ---- External demand, all optional --------------------------------
    is_trade_candidate: bool = False
    has_user_position: bool = False
    watcher_count: int = 0
    sbc_resource_demand_weight: float = 0.0
    has_constrained_sbc_trigger: bool = False
    manual_request: bool = False

    # ---- Anti-explosion bookkeeping -----------------------------------
    last_validated_at: Optional[datetime] = None
    validations_today: int = 0
    #: Consecutive bulk observations for which the anomaly has held.
    anomaly_persistence_observations: int = 0


@dataclass
class EligibilityDecision:
    card_id: int
    eligible: bool
    reason: Optional[str] = None
    skip_reason: Optional[str] = None
    urgency: int = 0
    card_class: str = "dormant"
    max_evidence_age_minutes: int = 0
    evidence_age_minutes: Optional[int] = None
    market_state: str = INSUFFICIENT_BIN_HISTORY
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "card_id": self.card_id, "eligible": self.eligible,
            "reason": self.reason, "skip_reason": self.skip_reason,
            "urgency": self.urgency, "card_class": self.card_class,
            "max_evidence_age_minutes": self.max_evidence_age_minutes,
            "evidence_age_minutes": self.evidence_age_minutes,
            "market_state": self.market_state, "notes": list(self.notes),
        }


def _evidence_age_minutes(latest_sale_at: Optional[datetime], as_of: datetime) -> Optional[int]:
    if latest_sale_at is None:
        return None
    if latest_sale_at.tzinfo is None:
        latest_sale_at = latest_sale_at.replace(tzinfo=timezone.utc)
    return max(0, int((as_of - latest_sale_at).total_seconds() // 60))


def classify_card(
    ctx: CardContext, signal: BinMarketSignal, config: SalesValidationConfig,
) -> str:
    """active / warm / cold / dormant.

    This is what makes the default state cheap (spec item 7): the vast
    majority of a 10k catalogue has no user interest, no SBC relevance and
    a flat asking price, so it lands in `dormant` and simply stops being
    scraped on a timer.
    """
    if ctx.is_trade_candidate or ctx.has_user_position or ctx.manual_request:
        return "active"
    if signal.market_state in (POSSIBLE_UNDERCUT, POSSIBLE_SPIKE):
        return "active"
    if ctx.has_constrained_sbc_trigger or ctx.watcher_count > 0:
        return "warm"
    if signal.market_state in (MOVING_UP, MOVING_DOWN, VOLATILE):
        return "warm"
    if ctx.sbc_resource_demand_weight > 0:
        return "warm"
    if (ctx.rating or 0) >= 85 or ctx.sales_count_14d > 0:
        return "cold"
    return "dormant"


def _anomaly_qualifies(
    ctx: CardContext, signal: BinMarketSignal, config: SalesValidationConfig,
) -> bool:
    """Guards against the failure mode that would make BIN-first cost MORE
    than what it replaced: every small BIN wobble buying a page load."""
    if ctx.anomaly_persistence_observations < config.min_anomaly_persistence_observations:
        return False
    if signal.thin_market:
        # A thin market has to clear a materially higher bar - its ask
        # moves for reasons that have nothing to do with value.
        required = (signal.anomaly_score or 0.0) / config.thin_market_anomaly_multiplier
        return required >= 4.0
    return True


def evaluate_eligibility(
    ctx: CardContext,
    signal: BinMarketSignal,
    *,
    as_of: Optional[datetime] = None,
    config: SalesValidationConfig = SALES_VALIDATION_CONFIG,
) -> EligibilityDecision:
    """Pure. Does this card deserve an expensive sales request right now?"""
    as_of = as_of or datetime.now(timezone.utc)
    card_class = classify_card(ctx, signal, config)
    max_age = config.max_evidence_age_minutes(card_class)
    age = _evidence_age_minutes(ctx.latest_sale_at, as_of)

    decision = EligibilityDecision(
        card_id=ctx.card_id, eligible=False, card_class=card_class,
        max_evidence_age_minutes=max_age, evidence_age_minutes=age,
        market_state=signal.market_state,
    )

    if not ctx.is_active or ctx.is_tradeable is False:
        decision.skip_reason = SKIP_NOT_ELIGIBLE
        decision.notes.append("inactive_or_untradeable")
        return decision

    # Never-read cards keep an explicit wake-up path. Removing it would
    # permanently strand every card the backfill has not yet reached at
    # zero sales, which is the one invariant futgg_sales_sync.select_due()
    # was specifically written to protect.
    if ctx.never_read:
        decision.eligible = True
        decision.reason = NEVER_READ_BACKFILL
        decision.urgency = _REASON_RANK[NEVER_READ_BACKFILL]
        return decision

    # ---- Which reasons apply -------------------------------------------
    reasons: List[str] = []
    if ctx.manual_request:
        reasons.append(MANUAL)
    if ctx.is_trade_candidate:
        reasons.append(TRADE_CANDIDATE)
    if ctx.has_user_position:
        reasons.append(USER_POSITION)
    if ctx.has_constrained_sbc_trigger:
        reasons.append(SBC_CONSTRAINED_DEMAND)
    if ctx.sbc_resource_demand_weight > 0:
        reasons.append(SBC_RESOURCE_DEMAND)
    if ctx.watcher_count > 0:
        reasons.append(WATCHLIST_INTEREST)
    if ctx.first_seen_at is not None:
        first_seen = ctx.first_seen_at
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        if (as_of - first_seen) <= timedelta(hours=config.new_release_age_hours):
            reasons.append(NEW_RELEASE)

    if _anomaly_qualifies(ctx, signal, config):
        if signal.market_state == POSSIBLE_UNDERCUT:
            reasons.append(BIN_UNDERCUT_VALIDATION)
        elif signal.market_state == POSSIBLE_SPIKE:
            reasons.append(BIN_SPIKE_VALIDATION)
        elif signal.market_state in (MOVING_UP, MOVING_DOWN):
            reasons.append(BIN_MOVEMENT_VALIDATION)
        elif signal.market_state == VOLATILE:
            reasons.append(BIN_VOLATILITY_VALIDATION)

    if not reasons:
        decision.skip_reason = SKIP_NOT_ELIGIBLE
        decision.notes.append(f"no_qualifying_reason_class_{card_class}")
        return decision

    reasons.sort(key=lambda r: _REASON_RANK.get(r, len(REASON_PRIORITY)))
    best = reasons[0]
    decision.notes.extend(reasons)

    # ---- Safeguards, applied AFTER a reason exists ----------------------
    # Ordering matters: a card must have earned a request before we
    # report why it was denied one, otherwise every dormant card produces
    # a misleading "cooldown" record.
    if ctx.last_validated_at is not None:
        last = ctx.last_validated_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (as_of - last) < timedelta(minutes=config.validation_cooldown_minutes):
            decision.skip_reason = SKIP_COOLDOWN
            return decision

    if ctx.validations_today >= config.max_validations_per_card_per_day:
        decision.skip_reason = SKIP_PER_CARD_LIMIT
        return decision

    # Existing evidence still inside this class's tolerance: nothing to buy.
    if age is not None and age <= max_age:
        decision.skip_reason = SKIP_EVIDENCE_FRESH
        return decision

    decision.eligible = True
    decision.reason = best
    decision.urgency = _REASON_RANK.get(best, len(REASON_PRIORITY))
    return decision


# =============================================================================
# Budget
# =============================================================================
@dataclass
class BudgetState:
    requests_this_hour: int = 0
    requests_today: int = 0
    discovery_today: int = 0
    anomaly_this_cycle: int = 0


def apply_budget(
    decisions: Sequence[EligibilityDecision],
    state: BudgetState,
    *,
    config: SalesValidationConfig = SALES_VALIDATION_CONFIG,
) -> List[EligibilityDecision]:
    """Deterministic admission under the global budget.

    Sorted strictly by reason priority (spec item 10), so when demand
    exceeds capacity the requests that survive are always the ones with
    the most trading value - and discovery, sitting last, can never crowd
    out a genuine opportunity.
    """
    eligible = [d for d in decisions if d.eligible]
    eligible.sort(key=lambda d: (d.urgency, d.card_id))

    hour_left = max(0, config.max_requests_per_hour - state.requests_this_hour)
    day_left = max(0, config.max_requests_per_day - state.requests_today)
    discovery_cap = min(
        config.discovery_sample_per_day,
        int(config.max_requests_per_day * config.discovery_max_budget_share),
    )
    discovery_left = max(0, discovery_cap - state.discovery_today)
    anomaly_left = max(0, config.max_anomaly_validations_per_cycle - state.anomaly_this_cycle)

    anomaly_reasons = {
        BIN_UNDERCUT_VALIDATION, BIN_SPIKE_VALIDATION,
        BIN_MOVEMENT_VALIDATION, BIN_VOLATILITY_VALIDATION,
    }

    admitted: List[EligibilityDecision] = []
    for decision in eligible:
        if hour_left <= 0 or day_left <= 0:
            decision.eligible = False
            decision.skip_reason = SKIP_BUDGET_EXHAUSTED
            continue
        if decision.reason == PERIODIC_DISCOVERY:
            if discovery_left <= 0:
                decision.eligible = False
                decision.skip_reason = SKIP_BUDGET_EXHAUSTED
                continue
            discovery_left -= 1
        if decision.reason in anomaly_reasons:
            if anomaly_left <= 0:
                decision.eligible = False
                decision.skip_reason = SKIP_BUDGET_EXHAUSTED
                continue
            anomaly_left -= 1
        hour_left -= 1
        day_left -= 1
        admitted.append(decision)

    return admitted


# =============================================================================
# Validation outcome (spec item 13)
# =============================================================================
@dataclass
class ValidationOutcome:
    card_id: int
    reason: str
    outcome: str
    bin_baseline: Optional[float] = None
    current_bin: Optional[int] = None
    sales_median: Optional[float] = None
    sales_count: int = 0
    evidence_type: str = EVIDENCE_BIN_DERIVED
    notes: List[str] = field(default_factory=list)


#: A validation agrees with the BIN hypothesis when completed sales sit
#: near the baseline the hypothesis was built on, rather than near the
#: anomalous current ask.
_AGREEMENT_BAND = 0.06


def validate_against_sales(
    signal: BinMarketSignal,
    *,
    reason: str,
    sales_prices: Sequence[float],
    min_sales: int = 5,
) -> ValidationOutcome:
    """Did the transaction evidence support what BIN history suggested?

    This is the measurement that tells us whether BIN-first is actually
    finding real opportunities or just generating expensive noise - the
    number the whole architecture has to be judged on.
    """
    import statistics as _stats

    outcome = ValidationOutcome(
        card_id=signal.card_id, reason=reason, outcome=INSUFFICIENT_TRANSACTION_EVIDENCE,
        bin_baseline=signal.baseline, current_bin=signal.current_bin,
        sales_count=len(sales_prices),
    )
    usable = [float(p) for p in sales_prices if p and float(p) > 0]
    if len(usable) < min_sales:
        outcome.notes.append("too_few_sales")
        return outcome

    sales_median = float(_stats.median(usable))
    outcome.sales_median = sales_median
    baseline = signal.baseline
    current = float(signal.current_bin or 0)
    if not baseline or baseline <= 0 or current <= 0:
        outcome.notes.append("no_bin_baseline")
        return outcome

    near_baseline = abs(sales_median - baseline) / baseline <= _AGREEMENT_BAND
    near_current = abs(sales_median - current) / max(current, 1.0) <= _AGREEMENT_BAND

    if reason in (BIN_UNDERCUT_VALIDATION, BIN_SPIKE_VALIDATION):
        if near_baseline and not near_current:
            # Sales are still clustering at the old level, so the odd
            # current ask really is an outlier - a genuine opportunity.
            outcome.outcome = (
                SALES_CONFIRMED_UNDERCUT if reason == BIN_UNDERCUT_VALIDATION
                else SALES_CONFIRMED_SPIKE
            )
            outcome.evidence_type = EVIDENCE_SALES_CONFIRMED
            return outcome
        if near_current:
            # Sales have followed the ask: the baseline was stale, and the
            # "anomaly" was just this module noticing late.
            outcome.outcome = BIN_BASELINE_LAGGING
            outcome.evidence_type = EVIDENCE_SALES_CONFIRMED
            return outcome
        outcome.outcome = INSUFFICIENT_TRANSACTION_EVIDENCE
        outcome.notes.append("sales_near_neither_level")
        return outcome

    outcome.outcome = SALES_AGREE_WITH_BIN if near_baseline else BIN_BASELINE_LAGGING
    outcome.evidence_type = EVIDENCE_SALES_CONFIRMED
    return outcome
