# app/services/sbc_demand_classifier.py
"""
SBC relevance model revision (production-readiness review). Splits what
sbc_card_matching.py's original sync_sbc_triggers() conflated into one
thing - "a card matches an SBC requirement line" - into the two genuinely
different signals the review's own live shadow data proved need
separating:

  A. RESOURCE-DEMAND signals - a requirement with no named identity
     dimension (nation/league/club), e.g. "Min. 1 TOTW Player", "83
     Rated Gold Rares", "Team Rating: Min. 89". These describe demand for
     a whole reusable market segment, not one card - matching it against
     futgg_players resolves to hundreds or thousands of interchangeable
     cards (confirmed live: a single "Min. 1 TOTW Player" line promoted
     every TOTW card in the catalogue). Represented as aggregated
     sbc_resource_demand rows (migration 049), never as individual
     priority_triggers.

  B. CONSTRAINED IDENTITY-DEMAND triggers - a requirement with a named
     nation/league/club (optionally combined with rarity/rating), where
     the eligible pool is small enough that the demand meaningfully
     concentrates on specific cards. These still create individual
     priority_triggers rows, exactly as before, but now gated on pool
     size/concentration instead of unconditionally.

Pure classification/decay/gating functions live here, with no I/O - the
DB reads (pool-size counts, active-challenge aggregation) and writes
(priority_triggers, sbc_resource_demand) live in sbc_card_matching.py,
mirroring the existing sbc_requirement_parser.py/sbc_card_matching.py
split.

WHAT THIS ROUND DOES NOT IMPLEMENT (deliberately, see the task's own
scope restrictions): actual market-reinforcement computation (price/
sales/spread signals confirming or weakening a resource-demand
hypothesis). sbc_resource_demand.market_status exists and defaults to
UNCONFIRMED for every row; MARKET_CONFIRMED/DECAYING/EXPIRED are reserved
values this module's decay output is structured to support classifying
into later (compute_demand_weight's signature is DELIBERATELY built to
accept a `reinforcement` multiplier hook now, defaulted to a no-op,
rather than needing a breaking-change signature later), but nothing here
computes them yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.priority_config import PriorityEngineConfig
from app.services.sbc_requirement_parser import ParsedRequirement

# ---------------------------------------------------------------------
# Resource-type classification
# ---------------------------------------------------------------------

# Rarity string (sbc_requirement_parser.py's vocabulary) -> resource_type
# for the rarities with their own first-class bucket. Anything parsed as
# a rarity but NOT in this map (gold_common/silver_common/bronze_common/
# plain gold/silver/bronze/fut birthday/...) falls back to PROMO_TYPE
# with resource_value=the rarity string itself - a real, source-derived
# value, not an invented one.
_RARITY_RESOURCE_TYPE = {
    "icon": "ICON",
    "hero": "HERO",
    "totw": "TOTW",
    "tots": "TOTS",
    "gold_rare": "GOLD_RARE",
    "silver_rare": "SILVER_RARE",
    "bronze_rare": "BRONZE_RARE",
}


@dataclass(frozen=True)
class ResourceSignal:
    resource_type: str
    resource_value: str


def is_identity_requirement(req: ParsedRequirement) -> bool:
    """True when this requirement names a specific pool (nation/league/
    club) rather than a broad, reusable resource. This is the one
    boolean the whole classification split hinges on."""
    return bool(req.nation or req.league or req.club)


def classify_resource_signal(req: ParsedRequirement) -> Optional[ResourceSignal]:
    """None for identity requirements (never resource-classified) and for
    requirements with no concrete resource dimension at all (e.g. a bare
    unconstrained player-count line, which sbc_requirement_parser.py
    already refuses to mark parsed=True for). Ambiguous/unparsed rows are
    still classified here if they have a shape - the caller (sbc_card_
    matching.py) is responsible for storing them with
    ambiguity_status != 'CONFIRMED' rather than skipping classification
    entirely, per the task's "store, never guess" rule.

    A line combining rarity AND a bare rating (e.g. "83 Rated Gold
    Rares") is recorded as ONE compound resource_value under the rarity's
    own resource_type, not as two independent signals - that line asks
    for one thing (an 83+ gold rare), and splitting it into a separate
    "any 83-rated card" signal plus a separate "any gold rare" signal
    would fabricate a demand relationship the source text never made."""
    if is_identity_requirement(req):
        return None

    if req.team_rating_min is not None:
        return ResourceSignal("MIN_TEAM_RATING", str(req.team_rating_min))

    if req.rarity:
        resource_type = _RARITY_RESOURCE_TYPE.get(req.rarity, "PROMO_TYPE")
        value = req.rarity
        if req.rating_min is not None:
            value = f"{req.rarity}_{req.rating_min}plus" if req.rating_max is None else f"{req.rarity}_{req.rating_min}-{req.rating_max}"
        return ResourceSignal(resource_type, value)

    if req.rating_min is not None:
        if req.rating_max is not None and req.rating_max != req.rating_min:
            return ResourceSignal("MIN_RATING", f"{req.rating_min}-{req.rating_max}")
        return ResourceSignal("EXACT_RATING" if req.rating_max == req.rating_min else "MIN_RATING", str(req.rating_min))

    return None


# ---------------------------------------------------------------------
# Time decay
# ---------------------------------------------------------------------

def compute_demand_weight(
    *,
    event_started_at: Optional[datetime],
    event_expires_at: Optional[datetime],
    as_of: datetime,
    config: PriorityEngineConfig,
    reinforcement_multiplier: float = 1.0,
) -> float:
    """Deterministic age-band decay in [0, 1]. Configurable via
    config.demand_decay_bands_hours (sorted ascending hour thresholds ->
    weight) - see that field's own comment for why the tail bands are a
    reasoned extrapolation rather than independently confirmed.

    reinforcement_multiplier is a forward-compatibility hook for step 6
    (observed market behaviour confirming/weakening the signal) - always
    1.0 today (no-op), never computed by this round. Clamped to [0, 2]
    so a future caller can't accidentally push a weight wildly out of a
    sane range while this is still unimplemented.

    event_started_at=None is treated as "unknown age" - the most
    conservative assumption (minimal weight), not "brand new".
    """
    permanent = (
        event_expires_at is None
        or event_expires_at - as_of > timedelta(days=config.demand_permanent_event_expiry_threshold_days)
    )

    if event_started_at is None:
        base = config.demand_decay_minimal_weight
    else:
        age_hours = max(0.0, (as_of - event_started_at).total_seconds() / 3600.0)
        base = config.demand_decay_minimal_weight
        for threshold_hours in sorted(config.demand_decay_bands_hours):
            if age_hours <= threshold_hours:
                base = config.demand_decay_bands_hours[threshold_hours]
                break

    if permanent:
        # A permanent/tutorial SBC never exceeds the minimal band,
        # regardless of how recently it was first seen - see the config
        # field's own comment. Repeatable-but-non-permanent SBCs are NOT
        # capped here; they decay on the normal age bands above.
        base = min(base, config.demand_decay_minimal_weight)

    multiplier = max(0.0, min(2.0, reinforcement_multiplier))
    return round(max(0.0, min(1.0, base * multiplier)), 4)


# ---------------------------------------------------------------------
# Identity-trigger pool gating
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class IdentityGateResult:
    accepted: bool
    reason: str
    pool_size: int
    concentration_ratio: Optional[float]


def evaluate_identity_pool(
    *,
    required_count: int,
    pool_size: int,
    is_new_trigger: bool,
    event_started_at: Optional[datetime],
    as_of: datetime,
    config: PriorityEngineConfig,
) -> IdentityGateResult:
    """Whether a named nation/league/club (optionally + rarity/rating)
    requirement's eligible pool is small/concentrated enough to justify
    individual-card priority_triggers rows.

    Rejects (with an explicit, loggable reason) the exact failure modes
    the task calls out: "1 Premier League player from 1,000+ cards", "1
    Spanish player from a huge pool" - broad identity requirements that
    would otherwise re-create the same over-triggering bug this whole
    revision exists to fix, just gated on identity instead of rarity."""
    if pool_size <= 0:
        return IdentityGateResult(False, "empty_pool", pool_size, None)

    if pool_size <= config.identity_trigger_small_pool_override:
        return IdentityGateResult(True, "small_pool_override", pool_size, round(required_count / pool_size, 4))

    if pool_size > config.identity_trigger_max_pool_size:
        return IdentityGateResult(
            False, "pool_too_large", pool_size, round(required_count / pool_size, 4)
        )

    concentration_ratio = round(required_count / pool_size, 4)
    if concentration_ratio < config.identity_trigger_min_concentration_ratio:
        return IdentityGateResult(False, "concentration_too_low", pool_size, concentration_ratio)

    if is_new_trigger and event_started_at is not None:
        age_hours = (as_of - event_started_at).total_seconds() / 3600.0
        if age_hours > config.identity_trigger_max_event_age_hours_for_new:
            return IdentityGateResult(False, "event_too_old_for_new_trigger", pool_size, concentration_ratio)

    return IdentityGateResult(True, "accepted", pool_size, concentration_ratio)


def is_permanent_event(event_expires_at: Optional[datetime], as_of: datetime, config: PriorityEngineConfig) -> bool:
    """Standalone helper mirroring compute_demand_weight's own permanent
    check, exposed separately for diagnostics/tests that want the
    boolean without also computing a full weight."""
    return (
        event_expires_at is None
        or event_expires_at - as_of > timedelta(days=config.demand_permanent_event_expiry_threshold_days)
    )


__all__ = [
    "ResourceSignal",
    "IdentityGateResult",
    "is_identity_requirement",
    "classify_resource_signal",
    "compute_demand_weight",
    "evaluate_identity_pool",
    "is_permanent_event",
]
