# app/routers/v2/dashboard.py
#
# Single aggregated Home Dashboard endpoint - mirrors app/routers/v2/
# players.py's player_summary() aggregation pattern (one call instead of
# N separate hook fetches), built against the exact response contract
# the current frontend redesign expects (marketRegime, todaysOpportunities,
# highConfidenceInvestments, cardsToAvoid, biggestMovers,
# recentAiPredictions, watchlistAlerts, latestMarketEvents,
# latestSbcImpact).
#
# Gating is preserved, not dropped: opportunity_feed still gates
# todaysOpportunities/highConfidenceInvestments/cardsToAvoid/
# recentAiPredictions exactly as it already does on the separate feed
# endpoints (app/routers/v2/recommendations.py) - this endpoint adds one
# `locked` block to the response (additive beyond the frontend's
# originally-sketched contract) so the UI can render the same rich
# layout with an upsell for those sections instead of silently returning
# empty arrays that would look like "no signals today."
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from app.auth.entitlements import compute_entitlements
from app.db import get_core_pool, get_player_pool, get_watchlist_db
from app.routers.v2.futgg_market import (
    CANDIDATE_SCAN_LIMIT,
    CANDIDATE_SCAN_ORDER_BY,
    _score_and_filter,
    _sales_for_rows,
)
from app.services.futgg_intelligence import CardIntelligence
from app.services.futgg_trend import DOWNTREND, FALLING_KNIFE, INSUFFICIENT_TREND_DATA
from app.services.market_data_provider import FutggMarketDataProvider, PlayerFilters
from app.services.sbc_demand import compute_sbc_demand

router = APIRouter(tags=["v2-dashboard"])

# CardIntelligence.signal (see futgg_intelligence.py's SIGNALS tuple:
# strong_buy/buy/watch/hold/sell/avoid/insufficient_data) mapped onto the
# same BUY/WAIT/SELL/AVOID/INSUFFICIENT_DATA vocabulary
# recommendation_engine_v2.py's legacy `status` column already used, so
# _to_recommendation-shaped output stays identical regardless of which
# engine produced it.
_SIGNAL_TO_STATUS = {
    "strong_buy": "BUY", "buy": "BUY",
    "watch": "WAIT", "hold": "WAIT",
    "sell": "SELL",
    "avoid": "AVOID",
    "insufficient_data": "INSUFFICIENT_DATA",
}

# Plain, FUT-trader-facing labels rather than stock-market jargon
# (bullish/bearish/illiquid) - this product's audience trades FUT cards,
# not stocks, and isn't assumed to know those terms.
_STATE_LABEL = {"bullish": "Good Time to Buy", "bearish": "Prices Dropping", "illiquid": "Slow Trading", "normal": "Steady Market"}

# Below either of these, _compute_live_market_regime declines to
# characterise the market at all. Every metric it computes degrades
# silently to 0.0 when the underlying evidence is missing, and 0% of
# cards falling is indistinguishable from 0% of cards known - so without
# a floor on real evidence the function reports a calm, healthy market
# precisely when it knows least. Seen live: 1,344 cards scored, none
# valued, reported as a 95/100 market beside a zero buy-signal count.
MIN_VALUED_CARDS_FOR_REGIME = 20
MIN_EVIDENCE_PCT_FOR_REGIME = 5.0
# Sample size at which the count itself stops limiting confidence. Beyond
# this, only the share of cards actually valued moves the number.
REGIME_FULL_SAMPLE_CARDS = 300

# Recommendation Engine V1.2 (recommendation_engine_v2.py) writes a real
# status enum - BUY/WAIT/SELL/AVOID/INSUFFICIENT_DATA - straight onto
# `status`. This is now the source of truth; the legacy lowercase
# `recommendation` column (still written, see _legacy_recommendation())
# is kept only for callers that haven't migrated off the deprecated
# shape yet.
_STATUS_LABEL = {"BUY": "BUY", "WAIT": "WAIT", "SELL": "SELL", "AVOID": "AVOID", "INSUFFICIENT_DATA": "INSUFFICIENT_DATA"}

# Per-strategy nominal holding period, shown once a card actually
# qualifies for that strategy (see strategy_config.py) - never a single
# global "holding period" figure. First match in this order wins when a
# card qualifies for more than one.
_STRATEGY_HOLDING_LABEL = {
    "quick_flip": "~24h", "swing_trade": "~48h", "low_risk": "Flexible",
    "lazy_buyer": "Flexible", "sbc": "Flexible", "long_hold": "~7d",
}

_FACTOR_LABEL = {
    "discount_vs_fair_value": lambda v: f"Trading {float(v):.1f}% below its real 24h median.",
    "liquidity_sales_per_hour": lambda v: f"{float(v):.1f} sales/hour liquidity.",
    "trend_falling": lambda v: "Price is in a confirmed downward trend, not a discount.",
}


def _holding_period_label(qualified_strategies: List[str]) -> str:
    for name in _STRATEGY_HOLDING_LABEL:
        if name in (qualified_strategies or []):
            return _STRATEGY_HOLDING_LABEL[name]
    return "Unavailable"


def _risk_label(score_risk: Optional[float]) -> str:
    if score_risk is None:
        return "Unknown"
    v = float(score_risk)
    if v < 0.33:
        return "Low"
    if v < 0.66:
        return "Medium"
    return "High"


def _reasoning_text(status: Optional[str], qualified_strategies: List[str], failed_gate_reasons: List[str], held_decision_reasons: List[str]) -> str:
    """Short, honest reasoning derived from the real V1.2 decision
    fields - reasoning/market_drivers on the recommendations row itself
    are legacy rule_v1 columns the V1.2 engine never populates (see
    recommendation_engine_v2.py's _persist()), so a blank string there
    would silently look like a bug rather than "no engine wrote this
    column." Never claims more than the reason codes actually say."""
    if status == "BUY":
        names = ", ".join(s.replace("_", " ") for s in qualified_strategies) or "a strategy"
        return f"Qualifies for: {names}."
    if status == "SELL":
        names = "; ".join(r.replace("_", " ").lower() for r in held_decision_reasons)
        return names or "Selling now looks better than continuing to hold."
    if status == "AVOID":
        return "The likely outcome is a net loss after EA's sale tax."
    if status == "INSUFFICIENT_DATA":
        if failed_gate_reasons:
            return "Missing: " + ", ".join(r.replace("_", " ").lower() for r in failed_gate_reasons) + "."
        return "Not enough live market data yet."
    if status == "WAIT":
        return "Doesn't clear any strategy's threshold yet."
    return ""


def _sbc_demand_note(
    status: Optional[str],
    nation: Optional[str],
    league: Optional[str],
    sbc_demand: Optional[Dict[str, Dict[str, int]]],
) -> str:
    """Only ever added to a BUY reasoning string - a card's nation/league
    being in current SBC demand is a real, cite-able driver, never
    surfaced for any other status (AVOID/WAIT etc. already have their own
    honest reasoning and this isn't a reason to override it)."""
    if status != "BUY" or not sbc_demand:
        return ""
    nation_count = sbc_demand.get("nation", {}).get(nation or "", 0)
    league_count = sbc_demand.get("league", {}).get(league or "", 0)
    if nation_count and league_count:
        return f" {nation}/{league} cards are in demand across {nation_count + league_count} currently-live SBCs."
    if nation_count:
        return f" {nation} cards are in demand across {nation_count} currently-live SBC{'s' if nation_count != 1 else ''}."
    if league_count:
        return f" {league} cards are in demand across {league_count} currently-live SBC{'s' if league_count != 1 else ''}."
    return ""


def _drivers_to_strings(drivers: List[Dict[str, Any]]) -> List[str]:
    out = []
    for d in drivers or []:
        factor = d.get("factor", "")
        if factor.startswith("market_event_"):
            out.append(f"Related market event: {d.get('title') or d.get('event_id')}")
        elif factor in _FACTOR_LABEL:
            out.append(_FACTOR_LABEL[factor](d.get("value")))
        else:
            out.append(factor)
    return out


def _regime_summary(state: str, indicators: Dict[str, Any]) -> str:
    falling_pct = indicators.get("falling_pct") or 0
    avg_discount = indicators.get("avg_discount_pct") or 0
    avg_liquidity = indicators.get("avg_liquidity") or 0
    if state == "bullish":
        return f"Tracked cards are selling for {avg_discount:.1f}% less than usual, and only {falling_pct:.0f}% are still dropping in price - good time to pick some up."
    if state == "bearish":
        return f"{falling_pct:.0f}% of tracked cards are still dropping in price right now - a cheap price might drop even more before it's worth buying."
    if state == "illiquid":
        return f"Cards are only selling about {avg_liquidity:.2f} times an hour on average - it'll take longer than usual to buy or sell."
    return "Prices and sales are behaving normally across tracked cards right now."


def _compute_live_market_regime(scored_pairs: List[Any]) -> Dict[str, Any]:
    """Live FUT.GG-native replacement for the market_states-based regime.

    market_states is written exclusively by analytics_engine.compute_market_regime(),
    which only runs when ENABLE_LEGACY_FUTBIN is set (see analytics_engine.py's
    refresher_loop docstring) - unset in this deployment, so that table holds
    at most one stale row from whenever the loop last ran (possibly never) and
    is not updated by anything live. This recomputes the same shape of signal
    (bearish/bullish/illiquid/normal + confidence + summary) directly off the
    FUT.GG-native candidate pool already evaluated for the opportunity feeds
    above, so it reflects the current market on every request instead of being
    frozen forever.
    """
    total = len(scored_pairs)
    if total == 0:
        return {
            "label": "Unknown", "confidence": 0,
            "summary": "Not enough live market data has been scored yet.",
            "dataQuality": "LIMITED",
            "metrics": {"liquidCards": 0, "avgVolatility": 0, "avgValueGap": 0},
        }

    # How many of those cards the engine could actually VALUE, as opposed
    # to merely look at. This is the distinction the first version of this
    # function missed entirely, and it is the whole ballgame: every metric
    # below degrades silently to 0.0 when the evidence is absent rather
    # than reporting that it is absent. No cards falling gives
    # falling_pct 0.0; no buy signals gives avg_discount 0.0. Feed those
    # into the classifier and a market we know nothing about scores
    # identically to a calm, healthy one.
    valued = [ci for _, ci in scored_pairs if ci.signal != "insufficient_data"]
    evidence_pct = 100.0 * len(valued) / total

    if len(valued) < MIN_VALUED_CARDS_FOR_REGIME or evidence_pct < MIN_EVIDENCE_PCT_FOR_REGIME:
        # Observed live: 1,344 cards scored, 0 valued, and this function
        # reported "Steady Market" at 95/100 next to a "Buy signals: 0"
        # tile. Saying nothing is the only honest output here.
        return {
            "label": "Unknown", "confidence": 0,
            "summary": (
                f"Only {len(valued)} of {total} tracked cards have enough fresh price "
                "and sales data to value right now, so there is nothing solid to say "
                "about the market yet."
            ),
            "dataQuality": "LIMITED",
            "metrics": {"liquidCards": total, "avgVolatility": 0, "avgValueGap": 0},
        }

    trend_known = [ci for _, ci in scored_pairs if ci.trend_state != INSUFFICIENT_TREND_DATA]
    falling_pct = (
        100.0 * sum(1 for ci in trend_known if ci.trend_state in (FALLING_KNIFE, DOWNTREND)) / len(trend_known)
        if trend_known else 0.0
    )

    discounts = []
    for row, ci in scored_pairs:
        if ci.signal not in ("strong_buy", "buy") or ci.fair_value is None:
            continue
        current_bin = row.get("current_bin")
        fair_value = float(ci.fair_value)
        if not current_bin or fair_value <= 0:
            continue
        discounts.append(100.0 * (fair_value - float(current_bin)) / fair_value)
    avg_discount = (sum(discounts) / len(discounts)) if discounts else 0.0

    liquidity_per_hour = []
    for row, _ in scored_pairs:
        span = row.get("sales_window_span_minutes")
        if span:
            liquidity_per_hour.append(60.0 * float(row.get("sales_count") or 0) / float(span))
    avg_liquidity = (sum(liquidity_per_hour) / len(liquidity_per_hour)) if liquidity_per_hour else 0.0

    if falling_pct >= 25:
        state = "bearish"
    elif avg_discount >= 5 and falling_pct < 10:
        state = "bullish"
    elif avg_liquidity < 0.5:
        state = "illiquid"
    else:
        state = "normal"

    # Confidence in the VERDICT, not a count of how much we looked at.
    #
    # This was `max(50, min(95, 50 + min(total, 500) / 10))` - purely a
    # function of candidate-pool size, with a 50 floor. It therefore
    # returned 95 whenever the scanner produced 450+ candidates, whether
    # or not a single one of them could be valued, and could never go
    # below 50 no matter how little was known. That is how the dashboard
    # came to read "STRONG MARKET 95/100" beside "BUY SIGNALS 0".
    #
    # Two independent things have to be true before a verdict deserves
    # confidence, so they multiply rather than average - neither can
    # compensate for the other being near zero:
    #   * a big enough sample to generalise from at all, and
    #   * enough of that sample actually valued rather than declined.
    # No floor: not knowing is a real answer. Capped below 100 because a
    # market read is never certain.
    sample_term = min(1.0, total / REGIME_FULL_SAMPLE_CARDS)
    evidence_term = len(valued) / total
    confidence = int(round(min(95.0, 100.0 * sample_term * evidence_term)))
    indicators = {
        "total_cards": total, "avg_liquidity": avg_liquidity,
        "avg_discount_pct": avg_discount, "falling_pct": falling_pct,
        "valued_cards": len(valued), "evidence_pct": evidence_pct,
    }
    return {
        "label": _STATE_LABEL.get(state, state),
        "confidence": confidence,
        "summary": _regime_summary(state, indicators),
        # Reflects the evidence behind the verdict rather than being a
        # constant that said GOOD even when nothing could be valued.
        "dataQuality": "GOOD" if evidence_pct >= 40 else "LIMITED",
        "metrics": {
            "liquidCards": total,
            "avgVolatility": avg_liquidity,
            "avgValueGap": avg_discount,
        },
    }


def _to_recommendation(
    d: Dict[str, Any],
    scores: Optional[Dict[str, float]] = None,
    sbc_demand: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """Reshapes a recommendations_latest row (+ optional card_scores) into
    the frontend's Recommendation contract. current_bin/fair_value_24h/
    sales_24h/sales_7d aren't columns on recommendations/
    recommendations_latest themselves - every query building `d` LEFT
    JOINs fair_value_mv alongside fut_players so these come through as
    plain keys, live-current rather than a stale scored-at-the-time
    snapshot.

    `status` (BUY/WAIT/SELL/AVOID/INSUFFICIENT_DATA) is the real V1.2
    decision; `d` may lack it entirely for synthetic rows that were never
    evaluated (e.g. the "biggest movers" cards below, which are a raw
    price-activity signal, not an AI verdict) - `has_evaluation` guards
    every field that would otherwise fabricate an opinion out of absent
    data."""
    computed_at = d.get("computed_at")
    status = d.get("status")
    has_evaluation = status is not None
    qualified_strategies = d.get("qualified_strategies") or []
    failed_gate_reasons = d.get("failed_gate_reasons") or []
    held_decision_reasons = d.get("held_decision_reasons") or []

    def pct(field: str) -> Optional[float]:
        v = d.get(field)
        return float(v) * 100 if v is not None else None

    return {
        "cardId": d.get("card_id"),
        "player": {
            "name": d.get("name") or f"Card {d.get('card_id')}",
            "rating": d.get("rating"),
            "version": d.get("version"),
            "position": d.get("position"),
            "imageUrl": d.get("image_url"),
            "cardBgImage": d.get("card_bg_image"),
            "cardCutoutImage": d.get("card_cutout_image"),
            "cardCutoutType": d.get("card_cutout_type"),
            "cardName": d.get("card_name"),
            "generatedCardUrl": d.get("generated_card_url"),
            "generatedCardStatus": d.get("generated_card_status"),
            "generatedCardFlagged": d.get("generated_card_flagged"),
            # Same bg+cutout+overlay rendering PlayerCardArt already uses
            # on the Player Search/Compare pages - stats/nation-league-club
            # images are only present here so that component's full (non-
            # compact) overlay has real numbers instead of "-" placeholders.
            "stats": {
                "pace": d.get("pace"),
                "shooting": d.get("shooting"),
                "passing": d.get("passing"),
                "dribbling": d.get("dribbling"),
                "defending": d.get("defending"),
                "physicality": d.get("physicality"),
            },
            "nationImage": d.get("nation_image"),
            "leagueImage": d.get("league_image"),
            "clubImage": d.get("club_image"),
        },
        "status": status,
        "recommendation": _STATUS_LABEL.get(status, "WAIT") if has_evaluation else None,
        "confidence": float(d.get("confidence") or 0),
        # Deprecated alias kept for callers still on the pre-V1.2 shape -
        # always the after-tax likely-case ROI now (see
        # recommendation_engine_v2.py's _persist()), never the old
        # pre-tax discount-as-profit figure. New callers should read
        # netRoi.likely / netRoi.conservative instead.
        "expectedRoi": pct("likely_net_roi"),
        "netRoi": {
            "conservative": pct("conservative_net_roi"),
            "likely": pct("likely_net_roi"),
            "bullish": pct("bullish_net_roi"),
            "potential": pct("potential_net_roi"),
        },
        # Null with an explicit source string until a validated ML model
        # is promoted - never a fabricated/heuristic prediction (see
        # migration 024 + trading_math.py's module docstring).
        "expectedNetRoi": pct("expected_net_roi"),
        "expectedNetRoiSource": d.get("expected_net_roi_source"),
        "entryPrice": d.get("entry_price"),
        "breakEvenPrice": d.get("break_even_sale_price"),
        "holdingPeriod": _holding_period_label(qualified_strategies) if has_evaluation else "Unavailable",
        "risk": _risk_label(d.get("score_risk")),
        "qualifiedStrategies": qualified_strategies,
        "failedGateReasons": failed_gate_reasons,
        "currentBin": d.get("current_bin"),
        "fairValue": d.get("fair_value_24h"),
        "sales24h": d.get("sales_24h"),
        "sales7d": d.get("sales_7d"),
        "dataQuality": "SUSPECT" if d.get("data_quality_suspect") else ("GOOD" if d.get("sales_24h") else "LIMITED"),
        "updatedAt": computed_at.isoformat() if hasattr(computed_at, "isoformat") else computed_at,
        "reasoning": (
            _reasoning_text(status, qualified_strategies, failed_gate_reasons, held_decision_reasons)
            + _sbc_demand_note(status, d.get("nation"), d.get("league"), sbc_demand)
        ) if has_evaluation else "",
        "marketDrivers": _drivers_to_strings(d.get("market_drivers") or []),
        "historicalSimilarEvents": d.get("similar_events") or [],
        "isHeld": bool(d.get("is_held")),
        "heldDecision": d.get("held_decision"),
        "heldDecisionReasons": held_decision_reasons,
        "purchasePrice": d.get("purchase_price"),
        "scores": scores or {},
        "modelVersion": d.get("engine_version"),
    }


def _futgg_to_recommendation(
    row: Dict[str, Any],
    ci: Optional[CardIntelligence],
    sbc_demand: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """Adapts a (futgg_market_snapshot row, CardIntelligence) pair - the
    same inputs app/routers/v2/futgg_market.py's Trade Finder/Opportunities
    endpoints already score successfully - onto the exact same public
    contract _to_recommendation() produces, so this endpoint's frontend
    consumers see an unchanged response shape regardless of which engine
    is behind it.

    Deliberately NOT a reshape of CardIntelligence's own field names onto
    the old V1.2 vocabulary where no honest equivalent exists:
    qualifiedStrategies/heldDecision/purchasePrice/netRoi's conservative-
    /bullish-/potential- tiers stay at their honest empty/None default
    rather than fabricated. card_design_image_url (the rarity/frame art
    FUT.GG itself renders behind the cutout - see auto_sync/futgg_common.py's
    "/rarities-" capture) and the per-stat pace/shooting/passing/dribbling/
    defending/physicality columns (futgg_market_snapshot as of migration
    041) ARE real data FUT.GG provides, so those are surfaced onto the same
    cardBgImage/cardCutoutImage/stats fields PlayerCardArt already knows how
    to render - no generated-card-art pipeline exists for FUT.GG cards, but
    the frame art itself doesn't need generating, FUT.GG already serves it.
    See this router's module docstring precedent (dashboard.py's own
    top-of-file comment: "Anything that can't be computed honestly ... is
    simply omitted rather than faked") - same rule applied to a different
    engine.
    """
    has_evaluation = ci is not None
    signal = ci.signal if ci else None
    status = _SIGNAL_TO_STATUS.get(signal, "INSUFFICIENT_DATA") if has_evaluation else None
    confidence_pct = float(ci.confidence_score) * 100 if (ci and ci.confidence_score is not None) else 0.0
    expected_roi_pct = float(ci.expected_roi) * 100 if (ci and ci.expected_roi is not None) else None
    reasons_text = "; ".join(ci.signal_reasons) if (ci and ci.signal_reasons) else ""
    sbc_note = _sbc_demand_note(status, row.get("nation"), row.get("league"), sbc_demand) if has_evaluation else ""
    evaluated_at = ci.evaluated_at if ci else None
    card_bg_image = row.get("card_design_image_url")
    # The single pre-composited full-card image FUT.GG itself renders
    # (background+cutout+rating+stats baked in), mirrored to our own R2
    # storage by app/services/futgg_card_image_mirror.py - when present,
    # this is always preferred as generatedCardUrl (CardImage() renders
    # it as a plain <img>, no app-drawn overlay) rather than the older
    # bgImage+cutoutImage-composited-by-PlayerCardArt path below, which
    # draws its own rating/position/stats text and would double it up if
    # handed an image that already has that baked in.
    composited_image = row.get("card_image_stored_url") or row.get("card_image_url")

    return {
        "cardId": row.get("source_card_id"),
        "player": {
            "name": row.get("name") or f"Card {row.get('source_card_id')}",
            "rating": row.get("rating"),
            "version": row.get("rarity"),
            "position": row.get("primary_position"),
            "imageUrl": row.get("player_image_url"),
            "cardBgImage": card_bg_image,
            "cardCutoutImage": row.get("player_image_url"),
            # price_tier's "special" bucket is everything outside the plain
            # gold/silver/bronze common/rare set (see auto_sync/futgg_common
            # .py::classify_price_tier) - the same base-vs-promo distinction
            # PlayerCardArt's cutoutType prop expects.
            "cardCutoutType": "base" if row.get("price_tier") not in (None, "special") else "special",
            "cardName": None,
            "generatedCardUrl": composited_image or (None if card_bg_image else row.get("player_image_url")),
            "generatedCardStatus": "ready" if (composited_image or row.get("player_image_url")) else None,
            "generatedCardFlagged": False,
            "stats": {
                "pace": row.get("pace"),
                "shooting": row.get("shooting"),
                "passing": row.get("passing"),
                "dribbling": row.get("dribbling"),
                "defending": row.get("defending"),
                "physicality": row.get("physicality"),
            },
            "nationImage": row.get("nation_image_url"),
            "leagueImage": row.get("league_image_url"),
            "clubImage": row.get("club_image_url"),
        },
        "status": status,
        "recommendation": _STATUS_LABEL.get(status, "WAIT") if has_evaluation else None,
        "confidence": confidence_pct,
        "expectedRoi": expected_roi_pct,
        "netRoi": {
            # Only a single expected-ROI figure exists in this engine (no
            # conservative/bullish/potential tiers) - "likely" is the
            # closest honest slot; the others stay None rather than
            # duplicating or inventing a spread.
            "conservative": None, "likely": expected_roi_pct, "bullish": None, "potential": None,
        },
        "expectedNetRoi": None,
        "expectedNetRoiSource": "futgg_intelligence" if has_evaluation else None,
        # The engine computes four deliberately-separate prices (see
        # futgg_intelligence's module docstring, point 2). Until now the
        # adapter forwarded only this one, so the UI had nothing to show
        # on a WATCH - where current_executable_buy is None BY DESIGN,
        # meaning "not buyable at a sensible price right now" - and fell
        # back to displaying current_bin under a "Maximum entry" label.
        # That is precisely the circularity engine v2 was written to
        # remove: "the maximum you should pay" became "whatever it
        # currently costs". Forward the real ceiling and target so the
        # UI never has to invent one.
        "entryPrice": int(ci.current_executable_buy) if (ci and ci.current_executable_buy is not None) else None,
        "recommendedBuyMax": int(ci.recommended_buy_max) if (ci and ci.recommended_buy_max is not None) else None,
        "recommendedSellTarget": int(ci.recommended_sell_target) if (ci and ci.recommended_sell_target is not None) else None,
        "buyBelow": int(ci.buy_below) if (ci and ci.buy_below is not None) else None,
        # The entry price expectedProfitAfterTax is computed against. The
        # UI must show THIS number next to the profit, not the ceiling:
        # on a buy they are different (live ask vs advised maximum), and
        # pairing the profit with the ceiling overstated it 3.6x on a live
        # Xhaka - "BUY BELOW 85,000" above "+25,650", where 25,650 assumed
        # the 66,500 ask and 85,000 would really have returned 7,150.
        "expectedProfitBasis": (
            int(ci.expected_profit_basis)
            if (ci and ci.expected_profit_basis is not None) else None
        ),
        "breakEvenPrice": int(ci.break_even_price) if (ci and ci.break_even_price is not None) else None,
        "holdingPeriod": "Unavailable",
        "risk": (ci.risk_level or "Unknown").title() if has_evaluation else "Unknown",
        "qualifiedStrategies": [],
        "failedGateReasons": [],
        "currentBin": row.get("current_bin"),
        "fairValue": int(ci.fair_value) if (ci and ci.fair_value is not None) else None,
        "sales24h": None,
        "sales7d": row.get("sales_count"),
        "dataQuality": "GOOD" if row.get("sales_count") else "LIMITED",
        "updatedAt": evaluated_at.isoformat() if evaluated_at else None,
        "reasoning": (reasons_text + sbc_note) if has_evaluation else "",
        "marketDrivers": [],
        "historicalSimilarEvents": [],
        "isHeld": False,
        "heldDecision": None,
        "heldDecisionReasons": [],
        "purchasePrice": None,
        "scores": {},
        "modelVersion": ci.engine_version if ci else None,
    }


def _decode_rec_row(r) -> Dict[str, Any]:
    d = dict(r)
    for key in (
        "market_drivers", "similar_events", "inputs",
        "qualified_strategies", "strategy_results", "failed_gate_reasons", "held_decision_reasons",
    ):
        if isinstance(d.get(key), str):
            d[key] = json.loads(d[key])
    return d


async def _scores_by_card(conn, card_ids: List[int]) -> Dict[int, Dict[str, float]]:
    if not card_ids:
        return {}
    rows = await conn.fetch(
        "SELECT card_id, score_type, value FROM card_scores_latest WHERE card_id = ANY($1::bigint[]) AND platform = 'ps'",
        card_ids,
    )
    out: Dict[int, Dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["card_id"], {})[r["score_type"]] = float(r["value"])
    return out


@router.get("/dashboard")
async def get_dashboard(request: Request) -> Dict[str, Any]:
    player_pool = await get_player_pool()
    core_pool = await get_core_pool()
    ent = await compute_entitlements(request)
    opportunities_unlocked = "opportunity_feed" in ent["features"]

    # --- Recommendation feeds (gated, with a free preview) ---
    # Free/Pro users used to get an all-empty feed with nothing to show
    # the AI actually works - the one thing a free tier needs to prove
    # before anyone upgrades. FREE_PREVIEW_COUNT real BUY calls are now
    # always included (never a fabricated example); PREVIEW_LOCKED_COUNT
    # exists purely so the frontend can render "N more, upgrade to see
    # them" instead of the feed just looking short.
    FREE_PREVIEW_COUNT = 1
    todays_opportunities: List[Dict[str, Any]] = []
    high_confidence: List[Dict[str, Any]] = []
    cards_to_avoid: List[Dict[str, Any]] = []
    recent_predictions: List[Dict[str, Any]] = []
    preview_locked_count = 0

    # FUT.GG-native candidate scan - the exact same source and scoring
    # helpers app/routers/v2/futgg_market.py's already-working Trade
    # Finder/Opportunities endpoints use, not a new/untested path.
    # Replaces the legacy recommendations_latest/recommendations +
    # fut_players + fair_value_mv query set: that path depends entirely
    # on the FUTBIN pipeline this deployment never runs
    # (ENABLE_LEGACY_FUTBIN unset, see app/services/fair_value.py), so
    # recommendations_latest/recommendations sit permanently empty here,
    # and fair_value_mv doesn't exist at all (migration 011 requires
    # fut_players.name/rating/version/position/image_url, which nothing
    # in a FUT.GG-only deployment ever populates) - every query below
    # was unconditionally raising "relation fair_value_mv does not
    # exist", not just returning empty rows.
    core_pool_provider = FutggMarketDataProvider(core_pool)
    candidate_rows = await core_pool_provider.search_players(
        PlayerFilters(), limit=CANDIDATE_SCAN_LIMIT, offset=0, order_by=CANDIDATE_SCAN_ORDER_BY,
    )
    sales_by_card = await _sales_for_rows(core_pool_provider, candidate_rows)

    buy_scored = _score_and_filter(candidate_rows, signals={"strong_buy", "buy"}, sales_by_card=sales_by_card)
    buy_scored.sort(key=lambda pair: -(pair[1].confidence_score or 0.0))

    avoid_scored = _score_and_filter(candidate_rows, signals={"avoid"}, sales_by_card=sales_by_card)
    avoid_scored.sort(
        key=lambda pair: pair[0].get("snapshot_computed_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # "Recent" has no direct FUT.GG-native equivalent: the legacy
    # `recommendations` table was an append-only log of every past
    # evaluation, whereas this engine evaluates live off the current
    # snapshot rather than writing a history row per evaluation. The
    # closest honest analog is "most recently refreshed candidates",
    # regardless of signal, ordered by when their snapshot was computed.
    recent_scored = sorted(
        _score_and_filter(candidate_rows, sales_by_card=sales_by_card),
        key=lambda pair: pair[0].get("snapshot_computed_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # --- Market regime (ungated): recomputed live from this same
    # already-evaluated candidate pool - see _compute_live_market_regime's
    # docstring for why this replaced a market_states table read. ---
    market_regime = _compute_live_market_regime(recent_scored)

    # card_scores_latest (_scores_by_card) is keyed by the legacy FUTBIN
    # card_id space too (see migration 024) - no FUT.GG-native equivalent
    # exists yet, so `scores` on every card below stays the honest empty
    # dict {} rather than joining against a table keyed by a different ID
    # space (see migrations/038's card_source_map comment on why FUT.GG
    # and FUTBIN card IDs are not reconciled in this codebase yet).

    # Aggregate SBC nation/league demand - a new BUY driver, never a hard
    # dependency: a failure here must never break the whole dashboard.
    try:
        sbc_demand = await compute_sbc_demand(player_pool)
    except Exception:
        sbc_demand = None

    all_opportunities = [_futgg_to_recommendation(row, ci, sbc_demand) for row, ci in buy_scored[:8]]
    all_high_confidence = [d for d in all_opportunities if (d.get("confidence") or 0) >= 70]

    if opportunities_unlocked:
        todays_opportunities = all_opportunities
        high_confidence = all_high_confidence
        cards_to_avoid = [_futgg_to_recommendation(row, ci, sbc_demand) for row, ci in avoid_scored[:6]]
        recent_predictions = [_futgg_to_recommendation(row, ci, sbc_demand) for row, ci in recent_scored[:8]]
    else:
        # Free/Pro preview: a real BUY call, not a fabricated example, so
        # the free tier can actually see the product work before paying
        # for the full feed - see `locked.previewCount` below for what's
        # still behind the paywall.
        todays_opportunities = all_opportunities[:FREE_PREVIEW_COUNT]
        preview_locked_count = max(0, len(all_opportunities) - FREE_PREVIEW_COUNT)

    # No generated-card-art step needed for FUT.GG cards (unlike the
    # legacy FUTBIN pipeline's async ensure_cards_requested()) -
    # player_image_url is already served directly by futgg_market_snapshot.

    # --- Biggest movers (ungated) - real fields on futgg_market_snapshot
    # itself (migrations/038): sales_stddev as the volatility proxy,
    # sales_count as the most-traded proxy, and sales-per-minute-of-window
    # (sales_count / sales_window_span_minutes) as a genuine liquidity
    # proxy distinct from raw sales_count, rather than reusing the same
    # metric twice under two different labels. ---
    def _best(rows: List[Dict[str, Any]], key_fn) -> Optional[Dict[str, Any]]:
        candidates = [(key_fn(r), r) for r in rows if key_fn(r) is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda pair: pair[0])[1]

    mover_pool = [r for r in candidate_rows if r.get("sales_count")]
    largest_mover_row = _best(mover_pool, lambda r: r.get("sales_stddev"))
    most_traded_row = _best(mover_pool, lambda r: r.get("sales_count"))
    highest_liquidity_row = _best(
        mover_pool,
        lambda r: (
            float(r["sales_count"]) / float(r["sales_window_span_minutes"])
            if r.get("sales_window_span_minutes") else None
        ),
    )

    mover_cards = [
        _futgg_to_recommendation(row, None)
        for row in (largest_mover_row, most_traded_row, highest_liquidity_row)
        if row is not None
    ]
    # No generated-card-art polling needed here either, same reasoning as
    # the recommendation lists above (player_image_url is already present
    # synchronously on every futgg_market_snapshot row).

    # --- Watchlist alerts: real query against alerts_log, populated by
    # main.py's own live alert loop (_alerts_poll_loop/_eval_alerts_for_pair
    # against the watchlist_alerts table) - NOT app/services/
    # watchlist_engine.py, which is separate, unrelated dead code against
    # a differently-named watchlist_items table that nothing ever writes
    # to (see that module's own header comment). ---
    watchlist_alerts: List[Dict[str, Any]] = []
    uid = request.session.get("user_id")
    if uid:
        try:
            async for wdb in get_watchlist_db():
                rows = await wdb.fetch(
                    "SELECT direction, pct, price, sent_at FROM alerts_log WHERE user_id = $1 ORDER BY sent_at DESC LIMIT 5",
                    str(uid),
                )
                for r in rows:
                    watchlist_alerts.append({
                        "severity": "high" if abs(float(r["pct"])) >= 15 else "medium",
                        "title": f"{'Price rising' if r['direction'] == 'rise' else 'Price falling'} {abs(float(r['pct'])):.1f}%",
                        "message": f"Now {int(r['price']):,} coins",
                    })
                break
        except Exception:
            watchlist_alerts = []

    # --- Latest market events + SBC impact ---
    async with player_pool.acquire() as conn:
        event_rows = await conn.fetch(
            "SELECT id, kind, source, title, starts_at, ends_at, first_seen_at FROM market_events ORDER BY first_seen_at DESC LIMIT 6"
        )
        # HAVING gates out noise, not just low sample size: an SBC with a
        # real, measured price effect on the cards it draws on is worth
        # surfacing; one that measured out at +0.4% on two cards is not a
        # market signal, it's rounding error dressed up as one. Both bars
        # (n and |avg_impact|) exist because either alone lets the other
        # through - a huge swing on one card, or a trivial swing on ten.
        impact_rows = await conn.fetch(
            """
            SELECT e.id AS event_id, e.title, e.fingerprint,
                   AVG(i.price_change_pct) AS avg_impact, COUNT(*) AS n
            FROM event_market_impact i
            JOIN market_events e ON e.id = i.event_id
            WHERE e.kind = 'sbc'
            GROUP BY e.id, e.title, e.fingerprint
            HAVING COUNT(*) >= 2 AND ABS(AVG(i.price_change_pct)) >= 3
            ORDER BY MAX(i.computed_at) DESC
            LIMIT 5
            """
        )

    # --- Next Promo (events table, core DB - see main.py's /api/events/next
    # for the same query and its own comment on why this never fabricates
    # a placeholder event when nothing's been scheduled) ---
    async with core_pool.acquire() as conn:
        next_promo_row = await conn.fetchrow(
            "SELECT name, kind, start_at, confidence FROM events WHERE start_at > now() ORDER BY start_at ASC LIMIT 1"
        )
    next_promo = (
        {
            "name": next_promo_row["name"],
            "kind": next_promo_row["kind"],
            "startAt": next_promo_row["start_at"].isoformat(),
            "confidence": next_promo_row["confidence"] or "heuristic",
        }
        if next_promo_row else None
    )

    latest_market_events = [
        {
            "id": r["id"], "kind": r["kind"], "source": r["source"], "title": r["title"],
            "startsAt": r["starts_at"].isoformat() if r["starts_at"] else None,
            "endsAt": r["ends_at"].isoformat() if r["ends_at"] else None,
        }
        for r in event_rows
    ]
    latest_sbc_impact = [
        {
            "eventId": r["event_id"],
            "title": r["title"],
            "fingerprints": list(r["fingerprint"] or []),
            "estimatedMarketImpact": round(float(r["avg_impact"] or 0), 1),
            # Real, data-derived proxy (more measured cards = more
            # confidence), not an arbitrary number - capped at 95 since a
            # handful of card measurements is never "certain."
            "confidence": min(95, int(r["n"]) * 20),
        }
        for r in impact_rows
    ]

    return {
        "marketRegime": market_regime,
        "todaysOpportunities": todays_opportunities,
        "highConfidenceInvestments": high_confidence,
        "cardsToAvoid": cards_to_avoid,
        "biggestMovers": mover_cards,
        "recentAiPredictions": recent_predictions,
        "watchlistAlerts": watchlist_alerts,
        "nextPromo": next_promo,
        "latestMarketEvents": latest_market_events,
        "latestSbcImpact": latest_sbc_impact,
        "locked": {
            "opportunityFeed": not opportunities_unlocked,
            "previewCount": preview_locked_count,
        },
    }
