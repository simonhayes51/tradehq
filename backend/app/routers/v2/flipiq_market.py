"""Private, server-to-server FLIPIQ integration for the live TradeHQ backend.

Preserves the normal opportunity feed's freshness, liquidity, sales evidence,
BIN-history and risk requirements, while returning full results only to a
first-party caller authenticated with a strong shared service token.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from app.routers.v2.futgg_market import (
    LIVE_OPPORTUNITY_MAX_PRICE_AGE_MINUTES,
    MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES,
    MAX_PAGE_SIZE,
    ProviderDep,
    _apply_filters_in_python,
    _bin_observations_for_rows,
    _item_with_intelligence,
    _live_opportunity_min_liquidity,
    _opportunity_sort_key,
    _sales_for_rows,
    _score_and_filter,
    _segmented_candidates,
)
from app.services.market_data_provider import FutggMarketDataProvider, PlayerFilters

FLIPIQ_RECENT_SALES_HOURS = max(1, int(os.getenv("FLIPIQ_RECENT_SALES_HOURS", "3")))
FLIPIQ_MIN_RECENT_SALES = max(2, int(os.getenv("FLIPIQ_MIN_RECENT_SALES", "2")))

def _recent_completed_sales_count(sales, now):
    cutoff = now - timedelta(hours=FLIPIQ_RECENT_SALES_HOURS)
    count = 0
    for sale in sales or []:
        sold_at = sale.get("approximate_sold_at") if isinstance(sale, dict) else None
        if isinstance(sold_at, str):
            try:
                sold_at = datetime.fromisoformat(sold_at.replace("Z", "+00:00"))
            except ValueError:
                continue
        if not isinstance(sold_at, datetime):
            continue
        if sold_at.tzinfo is None:
            sold_at = sold_at.replace(tzinfo=timezone.utc)
        if cutoff <= sold_at <= now:
            count += 1
    return count

router = APIRouter(tags=["v2-flipiq-service"])


@router.get("/market/flipiq-opportunities")
async def flipiq_opportunities(
    min_profit: float = Query(500, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    service_token: Optional[str] = Header(None, alias="X-FLIPIQ-Token"),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    expected = os.getenv("FLIPIQ_SERVICE_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="FLIPIQ service integration not configured")
    if not service_token or not hmac.compare_digest(service_token, expected):
        raise HTTPException(status_code=401, detail="Invalid FLIPIQ service token")

    filters = PlayerFilters(max_price_age_minutes=LIVE_OPPORTUNITY_MAX_PRICE_AGE_MINUTES)
    candidates = await _segmented_candidates(provider)
    rows = _apply_filters_in_python(candidates, filters)
    rejections: Dict[str, int] = {}
    recent_sales = await _sales_for_rows(provider, rows)
    scored = await _score_and_filter(
        rows,
        min_expected_profit=min_profit,
        min_liquidity=_live_opportunity_min_liquidity(None),
        signals={"buy", "strong_buy"},
        require_confirmed_sales=True,
        max_evidence_age_minutes=MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES,
        sales_by_card=recent_sales,
        bin_by_card=await _bin_observations_for_rows(provider, rows),
        diagnostic_rejections=rejections,
    )
    now = datetime.now(timezone.utc)
    recent_enough = []
    for row, ci in scored:
        count = _recent_completed_sales_count(recent_sales.get(int(row["source_card_id"]), []), now)
        if count < FLIPIQ_MIN_RECENT_SALES:
            rejections["insufficient_recent_sales"] = rejections.get("insufficient_recent_sales", 0) + 1
            continue
        recent_enough.append((row, ci))
    scored = recent_enough
    scored.sort(key=_opportunity_sort_key)
    total = len(scored)
    window = scored[(page - 1) * page_size : page * page_size]
    # Only hydrate displayed cards, never the full scoring population.
    check_states = await provider.get_sales_check_status_by_ids(
        [int(row["source_card_id"]) for row, _ci in window]
    )
    items = []
    for row, ci in window:
        item = _item_with_intelligence(row, ci)
        item.update(check_states.get(int(row["source_card_id"]), {}))
        items.append(item)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "scanner_candidates_considered": len(rows),
        "diagnostics": {
            "candidate_pool": len(candidates),
            "fresh_price_candidates": len(rows),
            "price_stale_or_untradeable": len(candidates) - len(rows),
            "rejected_by_price_or_tradeability": len(candidates) - len(rows),
            "qualified_opportunities": total,
            "rejection_reasons": rejections,
            "recent_sales_window_hours": FLIPIQ_RECENT_SALES_HOURS,
            "minimum_recent_sales": FLIPIQ_MIN_RECENT_SALES,
            "max_price_age_minutes": LIVE_OPPORTUNITY_MAX_PRICE_AGE_MINUTES,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
        "source": "tradehq_live_revalidated",
    }


@router.get("/market/flipiq-price-drops")
async def flipiq_price_drops(
    drop: float = Query(16, ge=1, le=75),
    hours: int = Query(6),
    page_size: int = Query(50, ge=1, le=80),
    service_token: Optional[str] = Header(None, alias="X-FLIPIQ-Token"),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    """Price drop discoveries, NOT trading recommendations.

    Scans all priced active cards, irrespective of BUY signal. Bounce
    evidence is measured from historical BIN data, not predicted by AI.
    """
    from app.services.flipiq_recovery import recovery_evidence
    expected = os.getenv("FLIPIQ_SERVICE_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="FLIPIQ service integration not configured")
    if not service_token or not hmac.compare_digest(service_token, expected):
        raise HTTPException(status_code=401, detail="Invalid FLIPIQ service token")
    if hours not in (3, 6, 9):
        raise HTTPException(status_code=422, detail="hours must be 3, 6 or 9")
    detected = await provider.select_flipiq_price_drops(
        threshold=drop, hours=hours, limit=page_size,
    )
    ids = [int(row["source_card_id"]) for row in detected]
    observations = await provider.get_bin_observations_by_ids(ids)
    # Detail lookup is deliberately limited to returned cards; don't
    # evaluate all ~20k priced cards or fetch 7d history for non-drops.
    items = []
    now = datetime.now(timezone.utc)
    for candidate in detected:
        card_id = int(candidate["source_card_id"])
        player = await provider.get_player(card_id)
        if not player or not player.get("name") or not player.get("rating"):
            continue
        evidence = recovery_evidence(
            observations.get(card_id, []),
            drop_pct=drop, lookback_hours=hours,
        )
        items.append({
            **dict(player),
            "price_history": [{"time": at.isoformat(), "price": int(price)} for price, at in sorted(observations.get(card_id, []), key=lambda point: point[1]) if at >= now - timedelta(hours=12)][-200:],
            "drop_pct": round(float(candidate["drop_pct"]), 2),
            "reference_bin": int(candidate["reference_bin"]),
            "reference_at": candidate["reference_at"].isoformat(),
            "recovery": evidence,
            "recovery_status": (
                "HISTORICALLY_RECOVERED" if evidence["recovery_rate"] is not None
                and evidence["recovery_rate"] >= 60 else
                "HISTORICALLY_WEAK" if evidence["recovery_rate"] is not None
                else "INSUFFICIENT_HISTORY"
            ),
            "potential_rebound_net": (
                int(candidate["reference_bin"] * .95) -
                int(int(candidate["reference_bin"] * .95) * .05) -
                int(candidate["current_bin"])
            ),
        })
    return {
        "items": items,
        "total": len(items),
        "drop_threshold_pct": drop,
        "window_hours": hours,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "observed_bin_history_not_buy_signals",
        "disclaimer": "Historical BIN recovery is not a forecast; sale prices and profit are not guaranteed.",
    }


@router.get("/market/flipiq-investments")
async def flipiq_investments(
    page_size: int = Query(50, ge=1, le=80),
    service_token: Optional[str] = Header(None, alias="X-FLIPIQ-Token"),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    """Evidence-based investment shortlist, expressly NOT publishable BUY tips.

    Source snapshots + sustained historic BIN ranges are distinct from
    live order-book depth. Every result requires human supply verification.
    """
    from app.services.flipiq_investments import investment_case
    expected = os.getenv("FLIPIQ_SERVICE_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="FLIPIQ service integration not configured")
    if not service_token or not hmac.compare_digest(service_token, expected):
        raise HTTPException(status_code=401, detail="Invalid FLIPIQ service token")
    universe = await provider.select_flipiq_investment_universe(limit=120)
    histories = await provider.get_flipiq_investment_history(
        [int(p["source_card_id"]) for p in universe]
    )
    now = datetime.now(timezone.utc)
    results = []
    reasons: Dict[str, int] = {}
    for player in universe:
        card_id = int(player["source_card_id"])
        case = investment_case(
            histories.get(card_id, []),
            current_bin=int(player["current_bin"]), now=now,
        )
        if not case["qualified"]:
            reason = case["reason"]
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        observations = sorted(histories.get(card_id, []), key=lambda item: item[1])
        hourly = {}
        for price, at in observations:
            if at >= now - timedelta(hours=12):
                hourly[int(at.timestamp() // 3600)] = (price, at)
        results.append({
            **dict(player), **case,
            "price_history": [
                {"time": at.isoformat(), "price": int(price)}
                for _hour, (price, at) in sorted(hourly.items())
            ],
            "sales_evidence": "at_least_two_sales_rows_in_last_three_hours",
        })
    results.sort(key=lambda item: (item["hypothetical_net_profit"], item["hypothetical_roi_pct"]), reverse=True)
    return {
        "items": results[:page_size],
        "total": len(results),
        "universe_checked": len(universe),
        "rejection_reasons": reasons,
        "constraints": {
            "min_rating": 82, "min_bin": 15000,
            "min_hypothetical_net": 10000, "min_roi_pct": 10,
            "recent_sales_hours": 3, "market_depth_verified": False,
        },
        "checked_at": now.isoformat(),
        "disclaimer": "Research leads only. Historical BIN medians are not realizable offers; no depth, subscriber capacity or future return is verified.",
    }
