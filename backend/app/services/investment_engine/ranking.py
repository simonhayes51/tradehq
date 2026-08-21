# app/services/investment_engine/ranking.py
"""
Individual-card RANKING (not aggressive filtering) once a cohort's
regime has opened (state INVESTMENT_WINDOW or REBOUND).

FC26 research (Phase 8B/8C's card-vs-cohort tests) found no individual
card-level filter that reliably beat buying the qualifying cohort
broadly - several tested confirmation rules made results worse, not
better. Per that finding, and per the explicit product requirement ("Do
not rely on a single card-level buy/no-buy historical rule... use
ranking rather than aggressive filtering"), this module scores and
orders candidates rather than hard-excluding them (aside from the one
genuinely hard requirement: a card must be tradeable and must clear the
Trade-vs-Investment liquidity bar - see investability()).

Deployability is weighted heavily and deliberately: a card nobody can
buy more than one copy of belongs to the Trade Engine's snipe surface,
not here, however good its own chart looks.

Pure module: no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

LIQUIDITY_LOW = "low"
LIQUIDITY_MEDIUM = "medium"
LIQUIDITY_HIGH = "high"

INVESTABILITY_LIMITED = "limited"
INVESTABILITY_INVESTABLE = "investable"


@dataclass(frozen=True)
class CardCandidate:
    card_id: int
    current_price: float
    drawdown_pct: float
    is_decelerating: bool
    relative_strength_pct: Optional[float]   # card's own recent return minus cohort's, percentage points
    sales_count_recent: Optional[int]
    price_age_minutes: Optional[int]
    is_tradeable: Optional[bool]


@dataclass(frozen=True)
class RankedCandidate:
    card_id: int
    rank_score: float
    liquidity: str
    investability: str


def liquidity_tier(sales_count_recent: Optional[int]) -> str:
    if sales_count_recent is None:
        return LIQUIDITY_LOW
    if sales_count_recent >= 15:
        return LIQUIDITY_HIGH
    if sales_count_recent >= 5:
        return LIQUIDITY_MEDIUM
    return LIQUIDITY_LOW


def investability(sales_count_recent: Optional[int], min_sales_for_investable: int) -> str:
    """The explicit Trade-vs-Investment split: enough recent completed
    sales to believe multiple copies could actually be bought, not just
    one attractive listing. A single anomalous listing with no
    supporting sales history is a Trade Engine snipe, not an Investment
    Engine candidate."""
    if sales_count_recent is not None and sales_count_recent >= min_sales_for_investable:
        return INVESTABILITY_INVESTABLE
    return INVESTABILITY_LIMITED


def rank_candidates(
    candidates: Sequence[CardCandidate],
    *,
    min_sales_for_investable: int,
    max_price_age_minutes: int,
    require_investable: bool = True,
) -> List[RankedCandidate]:
    """Hard exclusions (the only ones): untradeable, or price too stale
    to trust. `require_investable=True` (the production default) also
    excludes 'limited' liquidity - those belong to the Trade Engine.
    Everything that survives is RANKED, not further filtered."""
    out: List[RankedCandidate] = []
    for c in candidates:
        if c.is_tradeable is False:
            continue
        if c.price_age_minutes is not None and c.price_age_minutes > max_price_age_minutes:
            continue
        inv = investability(c.sales_count_recent, min_sales_for_investable)
        if require_investable and inv != INVESTABILITY_INVESTABLE:
            continue

        liquidity = liquidity_tier(c.sales_count_recent)
        liquidity_component = {LIQUIDITY_HIGH: 1.0, LIQUIDITY_MEDIUM: 0.6, LIQUIDITY_LOW: 0.2}[liquidity]
        deceleration_component = 1.0 if c.is_decelerating else 0.3
        strength_component = max(0.0, min(1.0, 0.5 + (c.relative_strength_pct or 0.0) / 20.0))

        score = 0.45 * liquidity_component + 0.30 * deceleration_component + 0.25 * strength_component
        out.append(RankedCandidate(
            card_id=c.card_id, rank_score=round(score, 4), liquidity=liquidity, investability=inv,
        ))

    out.sort(key=lambda r: -r.rank_score)
    return out
