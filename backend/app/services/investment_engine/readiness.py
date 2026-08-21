# app/services/investment_engine/readiness.py
"""
"Insufficient data is a valid state."

The first hours of a new game cycle contain almost nothing: a partial
card catalogue, few prices, no sales history, no cohort worth the name.
The Investment Engine must treat that as a legitimate, NAMED outcome -
not force a classification, and not silently produce zero signals for a
reason nobody can see afterwards.

This module is the single place that decides "do we have enough to even
try", and it returns a NAMED reason when the answer is no. Those names
are the vocabulary the whole system uses - persisted on
investment_engine_runs, exposed by the admin shadow report, and shown in
the launch-health check - so "why did the engine do nothing this pass"
is always answerable.

Deliberately NOT a confidence score: this is the binary "are the inputs
present at all" gate that runs BEFORE any market judgement. Whether a
present-but-weak market justifies a signal is the state machine's and
the strategies' job, not this module's.

Pure module: no I/O. The caller supplies already-measured coverage
figures; every threshold comes from config.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------
# Suppression reason vocabulary. These strings are persisted and shown
# in operator tooling - treat them as a stable API, not free text.
# ---------------------------------------------------------------------
INSUFFICIENT_CARD_COVERAGE = "INSUFFICIENT_CARD_COVERAGE"
INSUFFICIENT_PRICE_HISTORY = "INSUFFICIENT_PRICE_HISTORY"
INSUFFICIENT_SALES_DATA = "INSUFFICIENT_SALES_DATA"
STALE_MARKET_DATA = "STALE_MARKET_DATA"
COHORT_UNSTABLE = "COHORT_UNSTABLE"
LIVE_CONFIRMATION_UNAVAILABLE = "LIVE_CONFIRMATION_UNAVAILABLE"
COHORT_STILL_CRASHING = "COHORT_STILL_CRASHING"
REGIME_NOT_INVESTABLE = "REGIME_NOT_INVESTABLE"
LAUNCH_WINDOW_CLOSED = "LAUNCH_WINDOW_CLOSED"
PUBLICATION_DISABLED = "PUBLICATION_DISABLED"

ALL_SUPPRESSION_REASONS = (
    INSUFFICIENT_CARD_COVERAGE, INSUFFICIENT_PRICE_HISTORY, INSUFFICIENT_SALES_DATA,
    STALE_MARKET_DATA, COHORT_UNSTABLE, LIVE_CONFIRMATION_UNAVAILABLE,
    COHORT_STILL_CRASHING, REGIME_NOT_INVESTABLE, LAUNCH_WINDOW_CLOSED,
    PUBLICATION_DISABLED,
)

# Plain-language copy for operator/user-facing surfaces. Deliberately
# free of percentile/z-score/threshold-name jargon (see the product copy
# requirement) - the numeric detail belongs in diagnostics, not here.
REASON_COPY: Dict[str, str] = {
    INSUFFICIENT_CARD_COVERAGE: "Still discovering this market's cards.",
    INSUFFICIENT_PRICE_HISTORY: "Not enough price history yet to judge a trend.",
    INSUFFICIENT_SALES_DATA: "Not enough completed sales yet to confirm real demand.",
    STALE_MARKET_DATA: "Market data is not fresh enough to act on right now.",
    COHORT_UNSTABLE: "This market segment is still settling into shape.",
    LIVE_CONFIRMATION_UNAVAILABLE: "Waiting on live market confirmation.",
    COHORT_STILL_CRASHING: "Prices are still falling - not a buying opportunity yet.",
    REGIME_NOT_INVESTABLE: "No investment window open for this market right now.",
    LAUNCH_WINDOW_CLOSED: "The early accumulation window for this market has closed.",
    PUBLICATION_DISABLED: "Investment calls are not being published yet.",
}


@dataclass(frozen=True)
class CoverageInputs:
    """Everything measured about a cohort's data availability this pass.
    Every field is Optional because on a brand-new cycle the honest
    answer to most of them is "we do not know yet" - which must never be
    silently coerced to 0 (see evaluate_readiness's own note)."""
    cards_in_cohort: int
    cards_with_price: int
    cards_with_fresh_price: int
    cards_with_recent_sales: Optional[int]
    market_state_observations: int
    max_price_age_minutes: Optional[float]


@dataclass(frozen=True)
class Readiness:
    ready: bool
    reasons: List[str]
    detail: Dict[str, Any]

    @property
    def primary_reason(self) -> Optional[str]:
        return self.reasons[0] if self.reasons else None


def evaluate_readiness(
    coverage: CoverageInputs, config, *, require_sales: bool = False,
) -> Readiness:
    """Binary gate: are the inputs present at all?

    `require_sales=False` by default because the two shipped strategies
    differ: the premium crash strategy's trigger is a price-history
    regime signal that FC26 could compute without any sales data at all,
    while a strategy that genuinely depends on demand confirmation
    should pass require_sales=True. Callers state their own dependency
    rather than this module assuming one for everybody.

    Note on missing vs zero: `cards_with_recent_sales=None` means "the
    sales pipeline has not reported yet", which is NOT the same as "zero
    cards have sales". The first is unknown and blocks only when sales
    are actually required; the second is a real, measured absence.
    """
    reasons: List[str] = []
    detail: Dict[str, Any] = {
        "cards_in_cohort": coverage.cards_in_cohort,
        "cards_with_price": coverage.cards_with_price,
        "cards_with_fresh_price": coverage.cards_with_fresh_price,
        "cards_with_recent_sales": coverage.cards_with_recent_sales,
        "market_state_observations": coverage.market_state_observations,
        "max_price_age_minutes": coverage.max_price_age_minutes,
    }

    min_cohort = config.min_cohort_size
    if coverage.cards_in_cohort < min_cohort:
        reasons.append(INSUFFICIENT_CARD_COVERAGE)
    elif coverage.cards_with_price < min_cohort:
        # The cohort exists on paper but most of it has no price yet -
        # a real early-launch state (catalogue discovered ahead of the
        # price sweep), distinct from "the cohort is too small".
        reasons.append(INSUFFICIENT_CARD_COVERAGE)

    priced_fraction = (
        coverage.cards_with_fresh_price / coverage.cards_with_price
        if coverage.cards_with_price else 0.0
    )
    detail["fresh_price_fraction"] = round(priced_fraction, 4)
    if coverage.cards_with_price and priced_fraction < config.min_fresh_price_fraction:
        reasons.append(STALE_MARKET_DATA)

    if coverage.market_state_observations < config.min_market_state_observations:
        reasons.append(INSUFFICIENT_PRICE_HISTORY)

    if require_sales:
        if coverage.cards_with_recent_sales is None:
            reasons.append(INSUFFICIENT_SALES_DATA)
        elif coverage.cards_with_recent_sales < config.min_cards_with_sales:
            reasons.append(INSUFFICIENT_SALES_DATA)

    return Readiness(ready=not reasons, reasons=reasons, detail=detail)
