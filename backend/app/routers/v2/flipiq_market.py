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
