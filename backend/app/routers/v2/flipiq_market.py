"""Private, server-to-server FLIPIQ integration for the live TradeHQ backend.

Preserves the normal opportunity feed's freshness, liquidity, sales evidence,
BIN-history and risk requirements, while returning full results only to a
first-party caller authenticated with a strong shared service token.
"""
from __future__ import annotations

import hmac
import os
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
    rows = _apply_filters_in_python(await _segmented_candidates(provider), filters)
    scored = await _score_and_filter(
        rows,
        min_expected_profit=min_profit,
        min_liquidity=_live_opportunity_min_liquidity(None),
        signals={"buy", "strong_buy"},
        require_confirmed_sales=True,
        max_evidence_age_minutes=MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES,
        sales_by_card=await _sales_for_rows(provider, rows),
        bin_by_card=await _bin_observations_for_rows(provider, rows),
    )
    scored.sort(key=_opportunity_sort_key)
    total = len(scored)
    window = scored[(page - 1) * page_size : page * page_size]
    return {
        "items": [_item_with_intelligence(row, ci) for row, ci in window],
        "page": page,
        "page_size": page_size,
        "total": total,
        "scanner_candidates_considered": len(rows),
        "source": "tradehq_live_revalidated",
    }
