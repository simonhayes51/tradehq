# app/services/strategy_definitions.py
"""
Central, server-side definitions for the Strategy Finder's five tabs
(Best overall / Quick flips / Low risk / Big profit / Budget picks).

WHY THIS EXISTS
---------------
Before this module, each tab's filter parameters lived only in the
frontend (frontend/src/v2/pages/HomeDashboard/HomeDashboard.jsx's
STRATEGY_PARAMS), sent to the generic GET /api/v2/trade-finder endpoint
as raw query params. Two concrete bugs came from that:

  1. "Big profit" set no min_profit/meaningful_profit at all, so a card
     clearing the buy signal's 3% ROI floor on a cheap card (e.g. a
     ~120k card netting ~3,425 coins after tax) ranked in the same list,
     sorted only by absolute profit, as a card netting 13,800 - nothing
     ever filtered the low end out.
  2. The frontend was the only source of truth for what each label
     means. A client bug, a future edit to HomeDashboard.jsx, or a
     second consumer of the same tabs (there is already one: the AI
     chat's find_trades_for_budget tool calls trade_finder() directly)
     could silently diverge from what "Big profit"/"Quick flips" are
     supposed to guarantee, with nothing server-side to catch it.

This module is the single place that answers "what does each strategy
label actually promise", consumed by
GET /api/v2/trade-finder/strategy/{strategy} (app/routers/v2/
futgg_market.py). The generic /trade-finder endpoint is untouched -
existing consumers (v2 Trade Finder page, the AI chat tool) keep their
current behaviour exactly as before.

THRESHOLDS
----------
BIG_PROFIT_MIN_ABS_PROFIT_AFTER_TAX (5,000 coins) and
BUDGET_PICKS_MAX_PRICE / BUDGET_PICKS_MIN_PROFIT_AFTER_TAX are product
requirements, not derived from trade outcome data (no access to a
calibration dataset at design time) - same "flagged for calibration
against actual data post-launch, not a substitute for it" caveat as
MEANINGFUL_PROFIT_ABS_FLOOR in futgg_market.py. QUICK_FLIP_MIN_LIQUIDITY
reuses the same liquidity_score scale trade_finder's "liquidity" sort
already ranks by (0-1).

Deliberately NO fallback/threshold-relaxation logic anywhere in this
module or its caller. If a strategy's real filters yield zero rows, the
caller returns an empty list and the frontend shows an honest empty
state - the alternative (quietly loosening a threshold to avoid a blank
tab) is exactly the failure mode this whole module exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.services import trading_math as tm

# ---------------------------------------------------------------------
# Thresholds (see module docstring)
# ---------------------------------------------------------------------

BIG_PROFIT_MIN_ABS_PROFIT_AFTER_TAX = 5_000
QUICK_FLIP_MIN_LIQUIDITY = 0.5
LOW_RISK_MIN_CONFIDENCE = 0.55
BUDGET_PICKS_MAX_PRICE = 50_000
BUDGET_PICKS_MIN_PROFIT_AFTER_TAX = 1_000

# HOTFIX: main Strategy Finder grid must contain only genuinely
# actionable BUY NOW opportunities. Every definition below now sets
# require_executable=True unconditionally - the pre-hotfix version left
# best_overall/low_risk at the default (False), which is what let
# signal="watch" candidates (current price already well above the
# recommended buy threshold - 15%/22%/60%+ above in the reported
# incident) flood the main grid, each one relabelled "WATCH" instead of
# being kept out of the actionable list entirely. A WATCH badge on a
# card sitting in the primary results grid, next to full BUY BELOW/
# TARGET/PROFIT numbers, reads as actionable regardless of the badge.
#
# WATCH_TOLERANCE_PCT: the only place "near entry" is defined, for the
# separate, clearly non-actionable Watch section (see
# app/routers/v2/futgg_market.py's watch_candidates()). A card whose
# current price is more than this fraction above its buy threshold is
# not "near" anything and must not appear in Watch either - not shown as
# WATCH, not hidden-but-technically-included, excluded outright.
WATCH_TOLERANCE_PCT = 0.10

# How stale a card's price is allowed to be before Strategy Finder
# refuses to call it "Buy now"/"Watch" and instead reports "Stale /
# Updating". Reuses the exact same per-tier freshness thresholds
# evaluate_card()'s confidence scoring already treats as "too old to
# trust for this tier" (app/services/futgg_intelligence.py's
# MAX_ACCEPTABLE_PRICE_AGE_MINUTES_BY_TIER) rather than inventing a
# second, disagreeing definition of staleness. That existing threshold
# only ever *soft-penalises* confidence (one component of a blended
# score), so a stale card can still surface with signal="buy" at a
# passing-but-degraded confidence - this module turns the same
# threshold into a hard label on the card itself, which is the missing
# half of the fix for "cards shown after the price already moved".


@dataclass(frozen=True)
class StrategyDefinition:
    key: str
    label: str
    description: str
    # Kwargs forwarded to the internal trade_finder() scoring pipeline.
    # Anything not listed here uses trade_finder's own defaults.
    sort_by: str
    require_executable: bool = False
    risk_tolerance: Optional[str] = None
    min_confidence: Optional[float] = None
    min_liquidity: Optional[float] = None
    min_profit: Optional[float] = None
    min_roi: Optional[float] = None
    meaningful_profit: bool = False
    budget: Optional[int] = None
    # Template for the per-card "why this fits" explanation. {fields}
    # are filled in from the scored CardIntelligence/row at request time
    # by why_this_fits() below - keep in sync with that function.
    why_template: str = ""


STRATEGY_DEFINITIONS: Dict[str, StrategyDefinition] = {
    "best_overall": StrategyDefinition(
        key="best_overall",
        label="Best overall",
        description="The strongest currently actionable balance of confidence, profit, ROI, liquidity and risk.",
        sort_by="best_opportunity",
        require_executable=True,
        # Same hole this module's own docstring documents fixing for "Big
        # profit" - "best_opportunity" sorts on confidence/ROI%/liquidity/
        # risk, none of which is absolute profit, so a cheap, highly
        # liquid card (tight spread, deep sales history -> high
        # confidence and liquidity) could top the tab on a trivial coin
        # move. "Best overall" is the one tab whose whole promise is "the
        # strongest opportunity" - it needs this floor more than any
        # other, not less.
        meaningful_profit=True,
        why_template="Ranks high on confidence ({confidence_pct}%) and expected return ({roi_pct}% ROI) together, not just one metric alone.",
    ),
    "quick_flips": StrategyDefinition(
        key="quick_flips",
        label="Quick flips",
        description="Trades a real recent history of frequent sales says should clear quickly, with a genuine profit floor.",
        sort_by="liquidity",
        require_executable=True,
        meaningful_profit=True,
        min_confidence=0.45,
        min_liquidity=QUICK_FLIP_MIN_LIQUIDITY,
        why_template="Liquidity score {liquidity_pct}% (recent sales throughput) means this card has historically sold fast, not just that the ROI% looks good.",
    ),
    "low_risk": StrategyDefinition(
        key="low_risk",
        label="Low risk",
        description="Only the engine's lowest risk tier, at a meaningful confidence bar. Lower risk, not no risk.",
        sort_by="confidence",
        require_executable=True,
        risk_tolerance="low",
        min_confidence=LOW_RISK_MIN_CONFIDENCE,
        why_template="Risk tier is the engine's lowest (low), and confidence clears {min_confidence_pct}% - still a real trade, not a guarantee.",
    ),
    "big_profit": StrategyDefinition(
        key="big_profit",
        label="Big profit",
        description=f"Ranked by absolute expected after-tax coin profit, with a hard floor of {BIG_PROFIT_MIN_ABS_PROFIT_AFTER_TAX:,} coins - a high percentage return on a cheap card does not qualify on its own.",
        sort_by="profit",
        require_executable=True,
        risk_tolerance="medium",
        min_confidence=0.45,
        min_profit=BIG_PROFIT_MIN_ABS_PROFIT_AFTER_TAX,
        why_template="Expected profit of {profit_coins} coins after tax clears the {min_profit_coins}-coin Big Profit floor - this is the actual coin gain, not a percentage.",
    ),
    "budget": StrategyDefinition(
        key="budget",
        label="Budget picks",
        description=(
            f"Entry price at or under {BUDGET_PICKS_MAX_PRICE:,} coins, "
            f"but still required to clear a real {BUDGET_PICKS_MIN_PROFIT_AFTER_TAX:,}-coin profit floor after tax - cheap alone isn't enough."
        ),
        sort_by="budget",
        require_executable=True,
        meaningful_profit=True,
        min_roi=0.03,
        min_profit=BUDGET_PICKS_MIN_PROFIT_AFTER_TAX,
        budget=BUDGET_PICKS_MAX_PRICE,
        why_template="Entry at {price_coins} coins is within the {budget_coins}-coin budget ceiling, and still nets {profit_coins} coins after tax.",
    ),
}


assert all(d.require_executable for d in STRATEGY_DEFINITIONS.values()), (
    "Every Strategy Finder tab's main grid must be require_executable=True "
    "- see the HOTFIX comment above WATCH_TOLERANCE_PCT. A False here is "
    "exactly the regression that let signal='watch' cards flood the main "
    "grid, so this is asserted at import time rather than left to be "
    "caught by a test someone might not run."
)


def get_strategy(key: str) -> Optional[StrategyDefinition]:
    return STRATEGY_DEFINITIONS.get(key)


def classify_watch_candidate(row: Dict[str, Any], ci: Any) -> Optional[Dict[str, Any]]:
    """Decide whether a signal='watch' candidate belongs in the separate,
    clearly non-actionable Watch section - and if so, compute its numbers
    honestly, from the CURRENT price, not the prospective trigger price.

    Returns None if the candidate must be excluded from Watch entirely
    (materially above entry, or no longer profitable from where the
    price actually is right now). Never returns a dict for a card that
    fails any of the hotfix's hard rules:
      - current price must be within WATCH_TOLERANCE_PCT of the buy
        threshold (not 15%/22%/60%+ above it, as the pre-hotfix version
        allowed by not checking this at all)
      - current price must never be at or above the sell target
      - current price must never be at or above the after-tax break-even
        point (target_price * (1 - EA_TAX), i.e. target_price * 0.95)
      - real-time expected profit (computed from the CURRENT price, not
        the trigger price evaluate_card() uses for a prospective watch
        number) must be positive
    """
    buy_threshold = ci.recommended_buy_max
    target = ci.recommended_sell_target
    current = row.get("current_bin")
    if buy_threshold is None or target is None or current is None:
        return None
    buy_threshold = Decimal(str(buy_threshold))
    target = Decimal(str(target))
    current = Decimal(str(current))
    if buy_threshold <= 0:
        return None

    # "Only slightly above entry" - the ONLY tolerance definition in this
    # codebase for what counts as "near". A card 22%/60% above its buy
    # threshold is not near anything.
    above_entry_pct = (current - buy_threshold) / buy_threshold
    if above_entry_pct <= 0 or above_entry_pct > Decimal(str(WATCH_TOLERANCE_PCT)):
        return None

    if current >= target:
        return None

    after_tax_break_even = target * (Decimal("1") - tm.EA_TAX)  # target_price * 0.95
    if current >= after_tax_break_even:
        return None

    real_time_profit = tm.net_profit(target, current)  # target_price*0.95 - current_price
    if real_time_profit <= 0:
        return None

    real_time_roi = tm.net_roi(target, current)

    return {
        "above_entry_pct": float(above_entry_pct * 100),
        "real_time_expected_profit_after_tax": float(real_time_profit),
        "real_time_expected_roi": float(real_time_roi) if real_time_roi is not None else None,
    }


def why_this_fits(strategy: StrategyDefinition, row: Dict[str, Any], ci: Any) -> str:
    """Fill the strategy's why_template from the scored candidate. Falls
    back to the bare description if a referenced field is missing rather
    than raising - a missing explanation is a cosmetic gap, not a reason
    to fail the whole request."""
    confidence = ci.confidence_score or 0.0
    liquidity = ci.liquidity_score or 0.0
    roi = float(ci.expected_roi) if ci.expected_roi is not None else 0.0
    profit = float(ci.expected_profit_after_tax) if ci.expected_profit_after_tax is not None else 0.0
    price = row.get("current_bin") or 0
    try:
        return strategy.why_template.format(
            confidence_pct=round(confidence * 100),
            liquidity_pct=round(liquidity * 100),
            roi_pct=round(roi * 100, 1),
            min_confidence_pct=round((strategy.min_confidence or 0) * 100),
            profit_coins=f"{round(profit):,}",
            min_profit_coins=f"{round(strategy.min_profit or 0):,}",
            price_coins=f"{round(price):,}",
            budget_coins=f"{strategy.budget or 0:,}",
        )
    except (KeyError, ValueError):
        return strategy.description
