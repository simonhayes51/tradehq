# app/routers/v2/companion.py
"""
EAFC Live Companion API - backs the read-only browser extension
(eafc-live-extension/) that runs inside the EA SPORTS FC Web App.

Single endpoint: GET /api/v2/companion/card/{source_card_id}

This is deliberately a thin, read-only reshaping of data that already
exists. It does not compute a second recommendation engine - it calls
the same provider.get_player() + evaluate_card() pair
app/routers/v2/futgg_market.py's get_player_detail() uses, and re-shapes
the result into the flat object the extension's overlay renders.

Why source_card_id is the join key
-----------------------------------
auto_sync/futgg_common.py's CARD_URL_RE parses FUT.GG card URLs of the
shape /players/{source_player_id}-{slug}/{game_year}-{source_card_id}/,
and futgg_player_sync.py writes that source_card_id straight into
futgg_players (see migrations/038's card_source_map and
futgg_market_snapshot, both keyed on source_card_id). FUT.GG's own card
ID *is* EA's definitionId/resourceId for that exact card version - it is
not a FUT.GG-internal id needing reconciliation - so the extension can
map EA's resourceId directly onto this column with no fuzzy matching.

Security
--------
This endpoint takes nothing but an integer path parameter and returns
data already computed from our own database. It has no knowledge of,
and must never be given, any EA session credential (X-UT-SID,
X-UT-PHISHING-TOKEN, EA cookies, EA password) - those stay entirely
inside the extension/browser and are combined with this response
client-side (see eafc-live-extension/src/eafc-api.js and market-calc.js).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.routers.v2.futgg_market import ProviderDep
from app.auth.access import Viewer, get_viewer, serialise_card_detail
from app.services.free_picks import unlocked_pick_ids
from app.services.futgg_intelligence import CardIntelligence, evaluate_card
from app.services.market_data_provider import FutggMarketDataProvider

router = APIRouter(prefix="/companion", tags=["v2-companion"])
log = logging.getLogger("companion")


def _app_base_url() -> str:
    return (os.getenv("FRONTEND_URL") or "https://app.eafc.live").rstrip("/")


def _liquidity_bucket(score: Optional[float]) -> Optional[str]:
    """Coarse high/medium/low label over the engine's existing
    liquidity_score (0..1, see futgg_intelligence._compute_liquidity_score).

    Not a new calculation - just a label for a small overlay badge, where
    a raw 0.0-1.0 float is harder to read at a glance than "high risk /
    3 listings under target". Thresholds are round numbers, not tuned
    against outcome data, unlike the engine's own gates.
    """
    if score is None:
        return None
    if score >= 0.66:
        return "high"
    if score >= 0.33:
        return "medium"
    return "low"


def _num(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def _int(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _build_companion_payload(row: Dict[str, Any], ci: CardIntelligence) -> Dict[str, Any]:
    return {
        "source_card_id": int(row["source_card_id"]),
        "name": row.get("name"),
        "rating": row.get("rating"),
        "rarity": row.get("rarity"),
        "image_url": row.get("player_image_url"),
        # The BIN our own backend last observed for this card - explicitly
        # named "reference" (not "live") because it can be minutes old.
        # The extension's own live EA auction data is the "live" figure;
        # keeping the two under different names is what stops the overlay
        # from conflating "what we last saw" with "what EA is showing you
        # right now" (see eafc-live-extension/src/market-calc.js).
        "reference_bin": _int(row.get("current_bin")),
        "reference_bin_age_minutes": ci.price_age_minutes,
        "fair_value": _int(ci.fair_value),
        "buy_below": _int(ci.buy_below) or _int(ci.recommended_buy_max),
        "recommended_buy_max": _int(ci.recommended_buy_max),
        "recommended_sell_target": _int(ci.recommended_sell_target),
        "signal": ci.signal,
        "status": ci.status,
        "confidence": ci.confidence_score,
        "expected_profit": _num(ci.expected_profit_after_tax),
        "expected_profit_basis": _int(ci.expected_profit_basis),
        "expected_roi": _num(ci.expected_roi),
        "sales_count": ci.sales_sample_size,
        "sales_median": _int(row.get("sales_median")),
        "liquidity": _liquidity_bucket(ci.liquidity_score),
        "liquidity_score": ci.liquidity_score,
        "trend_state": ci.trend_state,
        "risk": {"overall": ci.risk_level},
        "engine_version": ci.engine_version,
        "evaluated_at": (ci.evaluated_at or datetime.now(timezone.utc)).isoformat(),
        "expires_at": ci.expires_at.isoformat() if ci.expires_at else None,
    }


@router.get("/card/{source_card_id}")
async def get_companion_card(
    source_card_id: int,
    provider: FutggMarketDataProvider = ProviderDep,
    viewer: Viewer = Depends(get_viewer),
) -> Dict[str, Any]:
    """One clean intelligence object for the given exact card version.

    404 (not the usual empty-body 200) when we have never seen this
    source_card_id, so the extension's failure path is a single, cheap
    branch: no recommendation table, no second lookup - "ask again for a
    different card, or show nothing".
    """
    row = await provider.get_player(source_card_id)
    if row is None:
        raise HTTPException(404, "No EAFC Live intelligence for this card")

    try:
        sales = await provider.get_recent_sales(source_card_id)
    except Exception:
        log.warning("get_recent_sales failed for source_card_id=%s", source_card_id, exc_info=True)
        sales = []

    ci = evaluate_card(row, sales=sales)
    payload = _build_companion_payload(row, ci)

    # This endpoint was the widest bypass in the product. It is one card
    # per request, unauthenticated, and it returned buy_below,
    # recommended_buy_max, recommended_sell_target, expected_profit and
    # confidence - so anyone willing to iterate source_card_ids could
    # rebuild the entire paid feed without an account, and the extension
    # itself sent `credentials: "omit"` by design.
    #
    # It now goes through the same serialiser as everything else. An Elite
    # session gets the full object; anyone else gets the card, the
    # reference price and the verdict, with the trade plan withheld.
    if not viewer.has("opportunity_feed"):
        unlocked = await unlocked_pick_ids() if viewer.is_free else []
        payload = serialise_card_detail(payload, viewer, unlocked_ids=unlocked)
        # The overlay needs to render a locked state, so tell it plainly
        # rather than leaving it to infer meaning from missing keys.
        payload["locked"] = "trade_plan_locked" in payload
        payload["upgrade_url"] = f"{_app_base_url()}/#/billing"
    return payload
