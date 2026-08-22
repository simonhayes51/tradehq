# app/services/investment_engine/cohorts.py
"""
Position-relative launch-price-percentile cohort classification.

Reproduces the FC26 launch-market research method exactly (see
research/fc26_launch_market Phase 8's build_cohorts): a card's cohort is
decided ENTIRELY by its own reference price's rank within its own
position group's price distribution, at a single point in time. A
striker is never compared to a goalkeeper. No future price, no later
performance, and no cross-position comparison ever enters this decision -
see classify_cohorts()'s own no-lookahead note.

Pure module: no I/O. The live runner (runner.py) and the FC26 backtest
harness (backtest.py) both call classify_cohorts() with the same
function, so "does cohort assignment behave the way the research
validated" is directly unit-testable without a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

COHORT_PREMIUM_META = "premium_meta"
COHORT_MID_TIER_META = "mid_tier_meta"
COHORT_DESIRABLE_STARTER = "desirable_starter"
COHORT_ORDINARY = "ordinary"
COHORT_LIKELY_FODDER = "likely_fodder"

ALL_COHORTS = (
    COHORT_PREMIUM_META, COHORT_MID_TIER_META, COHORT_DESIRABLE_STARTER,
    COHORT_ORDINARY, COHORT_LIKELY_FODDER,
)

CLASSIFIER_VERSION = "cohort-1.0"

# Mirrors the position grouping used throughout the FC26 research
# (research/fc26_launch_market/auto_sync/research_fc26_phase8_investment_
# analysis.py's POSITION_GROUPS) so cohort composition is directly
# comparable to the validated findings.
POSITION_GROUPS: Dict[str, str] = {
    "GK": "GK",
    "CB": "CB", "SW": "CB",
    "LB": "FULLBACK", "RB": "FULLBACK", "LWB": "FULLBACK", "RWB": "FULLBACK",
    "CDM": "CENTRAL_MIDFIELD", "CM": "CENTRAL_MIDFIELD", "CAM": "CENTRAL_MIDFIELD",
    "LM": "WIDE_ATTACKER", "RM": "WIDE_ATTACKER", "LW": "WIDE_ATTACKER", "RW": "WIDE_ATTACKER",
    "CF": "STRIKER", "ST": "STRIKER",
}


def position_group(primary_position: str | None) -> str:
    return POSITION_GROUPS.get((primary_position or "").strip().upper(), "OTHER")


@dataclass(frozen=True)
class CohortInput:
    card_id: int
    reference_price: float
    primary_position: str | None
    reference_captured_at: object | None = None  # datetime | None, passed through only


@dataclass(frozen=True)
class CohortAssignment:
    card_id: int
    cohort_key: str
    position_group: str
    price_percentile: float
    reference_price: float
    reference_captured_at: object | None = None


def classify_cohorts(
    cards: Sequence[CohortInput], config, *, min_group_size: int = 1,
) -> List[CohortAssignment]:
    """NO LOOKAHEAD: every `reference_price` must already be the final,
    settled value the caller intends to classify on (e.g. a card's
    earliest live price, or - in the FC26 backtest - its first observed
    historical price). This function itself has no notion of time; it
    only ranks the prices it's handed.

    Position groups smaller than `min_group_size` are skipped entirely
    (returned as no assignment) rather than force-ranked against too few
    peers to produce a meaningful percentile - callers should treat a
    missing assignment as "insufficient data", not "ordinary".
    """
    by_group: Dict[str, List[CohortInput]] = {}
    for c in cards:
        by_group.setdefault(position_group(c.primary_position), []).append(c)

    out: List[CohortAssignment] = []
    for group, items in by_group.items():
        if len(items) < min_group_size:
            continue
        ordered = sorted(items, key=lambda c: c.reference_price)
        n = len(ordered)
        for rank, c in enumerate(ordered):
            pctile = rank / max(n - 1, 1)
            if pctile >= config.premium_meta_top_percentile:
                key = COHORT_PREMIUM_META
            elif pctile >= config.mid_tier_meta_percentile:
                key = COHORT_MID_TIER_META
            elif pctile >= config.desirable_starter_percentile:
                key = COHORT_DESIRABLE_STARTER
            elif pctile >= config.ordinary_percentile:
                key = COHORT_ORDINARY
            else:
                key = COHORT_LIKELY_FODDER
            out.append(CohortAssignment(
                card_id=c.card_id, cohort_key=key, position_group=group,
                price_percentile=pctile, reference_price=c.reference_price,
                reference_captured_at=c.reference_captured_at,
            ))
    return out
