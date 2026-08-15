# app/routers/v2/futgg_market.py
"""
FUT.GG-backed v2 market-intelligence endpoints. All under the v2_router's
own /api/v2 prefix (see app/routers/v2/__init__.py) - final paths are
/api/v2/players, /api/v2/players/{id}, /api/v2/players/{id}/prices,
/api/v2/players/{id}/sales, /api/v2/opportunities, /api/v2/trade-finder,
/api/v2/market/freshness.

Deliberately does NOT collide with v1's /api/players or v1's
/api/trade-finder (app/routers/players.py, app/routers/trade_finder.py)
- those stay exactly as they are, backed by fut_players/FUTBIN. This
router is entirely new surface, backed entirely by
app/services/market_data_provider.py + app/services/futgg_intelligence.py,
themselves reading only futgg_players/futgg_bin_history/
futgg_sales_history/futgg_market_snapshot (migrations/038) - never the
legacy fut_players/sales_history/bin_history tables.

Ungated (no entitlement check): this is new provider-neutral surface,
not a replacement for any existing gated feature - mirrors the precedent
already set by GET /api/v2/market/regime (see app/routers/v2/market.py's
own comment on why that route is free). If/when product wants a teaser/
paid split here, follow app/routers/fair_value.py's _teaser() convention
on top of this router rather than inside the provider/intelligence layers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_core_pool
from app.routers.admin import require_admin
from app.services.futgg_intelligence import (
    DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES,
    MAX_ACCEPTABLE_PRICE_AGE_MINUTES_BY_TIER,
    CardIntelligence,
    evaluate_card,
)
from app.services.futgg_snipe_filter import build_snipe_filter
from app.services.market_data_provider import (
    MAX_CHART_DAYS,
    MAX_CHART_SALES,
    MAX_RECENT_SALES,
    FutggMarketDataProvider,
    PlayerFilters,
)
from app.services.priority_config import PRIORITY_ENGINE_CONFIG
from app.services.priority_recompute import (
    _CONSERVATIVE_DEMOTION_CANDIDATES_QUERY,
    is_eligible_for_conservative_demotion,
)

router = APIRouter(tags=["v2-futgg-market"])
log = logging.getLogger("futgg_market")

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _is_stale_for_tier(price_age_minutes: Optional[int], price_tier: Optional[str]) -> bool:
    if price_age_minutes is None:
        return True
    threshold = MAX_ACCEPTABLE_PRICE_AGE_MINUTES_BY_TIER.get(price_tier, DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES)
    return price_age_minutes > threshold

# Bound on how many snapshot rows /opportunities and /trade-finder ever
# pull from Postgres before scoring in Python - the intelligence layer
# has no SQL equivalent (fair value/confidence/signal are computed in
# Python, not the view), so those two endpoints can't push their
# ROI/confidence/signal filters all the way into the SQL WHERE clause.
# Capping the candidate scan keeps the request bounded regardless of how
# large futgg_market_snapshot grows, at the cost of only ever considering
# the top CANDIDATE_SCAN_LIMIT rows (by sales_count, as a liquidity-first
# proxy for "cards worth scoring at all") per request.
CANDIDATE_SCAN_LIMIT = 500

# The candidate-scan calls below must pass this explicitly: search_players's
# own default order_by ("rating DESC NULLS LAST") is a different ordering
# entirely, and silently falling back to it here would candidate-limit
# /players (intelligence-filtered), /opportunities, and /trade-finder to
# the top-rated 500 cards rather than the top-500-most-liquid cards the
# comment above documents - systematically hiding genuinely liquid,
# profitable mid/low-rated opportunities behind a wall of high-rated but
# possibly illiquid/rarely-traded cards, with no error or symptom other
# than "why does this card never show up as an opportunity".
CANDIDATE_SCAN_ORDER_BY = "sales_count DESC NULLS LAST"


async def get_provider() -> FutggMarketDataProvider:
    """FastAPI dependency - lazily creates (and caches, via
    app.db.get_core_pool()'s own module-level cache) the shared asyncpg
    pool keyed off DATABASE_URL. A plain Depends rather than a module-
    level singleton so tests can override it with a fake provider via
    app.dependency_overrides[get_provider], with no real DB involved."""
    pool = await get_core_pool()
    return FutggMarketDataProvider(pool)


ProviderDep = Depends(get_provider)


def _int_or_none(value) -> Optional[int]:
    return int(value) if value is not None else None


def _float_or_none(value) -> Optional[float]:
    return float(value) if value is not None else None


def _serialize_intelligence(ci: CardIntelligence) -> Dict[str, Any]:
    return {
        "fair_value": _int_or_none(ci.fair_value),
        # The four distinct prices (engine v2, item 10). recommended_buy_max
        # is no longer clamped to the live ask, so these can and do differ -
        # the frontend must render them as separate numbers rather than
        # assuming any two are interchangeable.
        "theoretical_max_buy": _int_or_none(ci.theoretical_max_buy),
        "recommended_buy_max": _int_or_none(ci.recommended_buy_max),
        "current_executable_buy": _int_or_none(ci.current_executable_buy),
        "break_even_price": _int_or_none(ci.break_even_price),
        "recommended_sell_target": _int_or_none(ci.recommended_sell_target),
        "buy_below": _int_or_none(ci.buy_below),
        "expected_profit_after_tax": _float_or_none(ci.expected_profit_after_tax),
        "expected_roi": _float_or_none(ci.expected_roi),
        "liquidity_score": ci.liquidity_score,
        "confidence_score": ci.confidence_score,
        "risk_level": ci.risk_level,
        "signal": ci.signal,
        "status": ci.status,
        "signal_reasons": ci.signal_reasons,
        "reason_codes": ci.reason_codes,
        "reasons": ci.reasons,
        "blocking_codes": ci.blocking_codes,
        "price_age_minutes": ci.price_age_minutes,
        "sales_sample_size": ci.sales_sample_size,
        "sales_window_span_minutes": ci.sales_window_span_minutes,
        "trend_state": ci.trend_state,
        "trend_description": ci.trend_description,
        "trend_features": ci.trend_features,
        "engine_version": ci.engine_version,
        "trend_version": ci.trend_version,
        "expires_at": ci.expires_at.isoformat() if ci.expires_at else None,
        "expiry_minutes": ci.expiry_minutes,
        "evaluated_at": ci.evaluated_at.isoformat() if ci.evaluated_at else None,
        "evaluated_bin": ci.evaluated_bin,
    }


def _serialize_player(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    bin_captured_at = out.get("bin_captured_at")
    # Frontend-facing aliases (current_bin_captured_at / price_age_seconds)
    # alongside the raw column names - computed here, not duplicated in SQL,
    # since "now" has to be evaluated at serialization time either way.
    out["current_bin_captured_at"] = bin_captured_at.isoformat() if bin_captured_at else None
    out["price_age_seconds"] = (
        int((datetime.now(timezone.utc) - bin_captured_at).total_seconds())
        if bin_captured_at is not None else None
    )
    # image_url alias: player_image_url (the cutout-only photo, no frame/
    # rating/stats baked in) under the frontend's generic name every
    # existing consumer (Players/Opportunities/Trade Finder result rows,
    # HomeDashboard's normaliseFutgg, PlayerCardArt's cutoutImage/
    # fallbackImage props) already assumes this means - do NOT repoint
    # this at the composited card image below; several of those consumers
    # draw their own rating/position/stats text on top of whatever this
    # field points to, which double-renders that text if it's already
    # baked into the image (this exact regression shipped once already -
    # see composited_image_url's own comment for the fix).
    out["image_url"] = out.get("player_image_url")
    # composited_image_url: the single pre-composited full-card image
    # (background+cutout+rating+stats already baked in by FUT.GG itself,
    # mirrored to our own R2 storage - see app/services/
    # futgg_card_image_mirror.py) - a DELIBERATELY separate field from
    # image_url above. Only ever meant to be shown as a plain <img> with
    # no app-drawn overlay on top (PlayerCardImage.jsx's "isReady" path,
    # HomeDashboard's CardImage() checking player.generatedCardUrl) -
    # never pass this into a component that also draws its own rating/
    # stats text (e.g. PlayerCardArt's bgImage/cutoutImage props).
    out["composited_image_url"] = out.get("card_image_stored_url") or out.get("card_image_url")
    for key in ("price_updated_at", "next_price_due_at", "bin_captured_at",
                "latest_sale_at", "sales_window_earliest_at",
                "sales_window_latest_at", "snapshot_computed_at"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat()
    return out


def _item_with_intelligence(row: Dict[str, Any], ci: CardIntelligence) -> Dict[str, Any]:
    """Flattens the CardIntelligence fields onto the player dict (what the
    frontend reads directly, e.g. item.recommended_buy_max) while also
    keeping them nested under "intelligence" for any consumer that prefers
    the grouped shape.

    The snipe filter rides along on every actionable item: a tip the user
    cannot execute before it goes stale is the product's core failure, and
    an executable search instruction is the cheapest fix for it that does
    not depend on scraping faster."""
    intelligence = _serialize_intelligence(ci)
    snipe = build_snipe_filter(row, ci)
    return {
        **_serialize_player(row),
        **intelligence,
        "intelligence": intelligence,
        "snipe_filter": snipe.as_dict() if snipe else None,
    }


def _resolve_rating_bounds(
    rating: Optional[int], rating_min: Optional[int], rating_max: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    """`rating` is a single-value convenience alias (exact match) some
    callers use instead of the rating_min/rating_max pair."""
    if rating is not None:
        return rating, rating
    return rating_min, rating_max


def _filters_from_query(
    search: Optional[str], rating_min: Optional[int], rating_max: Optional[int],
    position: Optional[str], rarity: Optional[str], club: Optional[str],
    league: Optional[str], nation: Optional[str], max_price: Optional[int],
    min_price: Optional[int], max_price_age_minutes: Optional[int],
    tradeable_only: bool = True,
) -> PlayerFilters:
    return PlayerFilters(
        search=search, rating_min=rating_min, rating_max=rating_max,
        position=position, rarity=rarity, club=club, league=league, nation=nation,
        max_price=max_price, min_price=min_price,
        max_price_age_minutes=max_price_age_minutes, tradeable_only=tradeable_only,
    )


@router.get("/players")
async def list_players(
    search: Optional[str] = Query(None, description="Diacritic-insensitive name search"),
    rating: Optional[int] = Query(None, ge=0, le=99, description="Single-value exact-rating alias for rating_min=rating_max"),
    rating_min: Optional[int] = Query(None, ge=0, le=99),
    rating_max: Optional[int] = Query(None, ge=0, le=99),
    position: Optional[str] = None,
    rarity: Optional[str] = None,
    club: Optional[str] = None,
    league: Optional[str] = None,
    nation: Optional[str] = None,
    max_price: Optional[int] = Query(None, ge=0, description="Affordability filter: current BIN <= this"),
    min_price: Optional[int] = Query(None, ge=0),
    max_price_age_minutes: Optional[int] = Query(None, ge=0, description="Freshness filter"),
    max_price_age: Optional[int] = Query(None, ge=0, description="Alias for max_price_age_minutes"),
    risk: Optional[str] = Query(None, description="low|medium|high|avoid - computed, applied post-fetch"),
    min_expected_profit: Optional[float] = Query(None),
    min_profit: Optional[float] = Query(None, description="Alias for min_expected_profit"),
    min_roi: Optional[float] = Query(None, description="Minimum expected net ROI, e.g. 0.05 for 5%"),
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    min_liquidity: Optional[float] = Query(None, ge=0, le=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    rating_min, rating_max = _resolve_rating_bounds(rating, rating_min, rating_max)
    max_price_age_minutes = max_price_age_minutes if max_price_age_minutes is not None else max_price_age
    min_expected_profit = min_expected_profit if min_expected_profit is not None else min_profit
    filters = _filters_from_query(
        search, rating_min, rating_max, position, rarity, club, league, nation,
        max_price, min_price, max_price_age_minutes,
    )

    needs_intelligence = any(
        v is not None for v in (risk, min_expected_profit, min_roi, min_confidence, min_liquidity)
    )

    if not needs_intelligence:
        total = await provider.count_players(filters)
        rows = await provider.search_players(
            filters, limit=page_size, offset=(page - 1) * page_size,
        )
        items = [_serialize_player(r) for r in rows]
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    # Any intelligence-derived filter requires scoring candidates in
    # Python first (see CANDIDATE_SCAN_LIMIT), so pagination happens
    # after scoring+filtering rather than pushing LIMIT/OFFSET into SQL.
    rows = await provider.search_players(filters, limit=CANDIDATE_SCAN_LIMIT, offset=0, order_by=CANDIDATE_SCAN_ORDER_BY)
    scored = _score_and_filter(
        rows, risk=risk, min_expected_profit=min_expected_profit,
        min_roi=min_roi, min_confidence=min_confidence, min_liquidity=min_liquidity,
    )
    total = len(scored)
    window = scored[(page - 1) * page_size: page * page_size]
    items = [_item_with_intelligence(row, ci) for row, ci in window]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


async def _sales_for_rows(provider, rows: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """Batch-fetch the raw sales series backing the trend layer.

    Without this every list endpoint would either issue one sales query
    per candidate (500+ round trips) or skip the trend layer entirely -
    and skipping it is what allowed falling knives to be ranked as the
    best opportunities in the first place. Best-effort: if the batch
    fetch fails, evaluation continues with no trend data, which caps
    signals rather than silently restoring the old behaviour.
    """
    try:
        return await provider.get_sales_by_ids([int(r["source_card_id"]) for r in rows])
    except Exception:
        log.warning("batch sales fetch failed; evaluating without trend data", exc_info=True)
        return {}


def _score_and_filter(
    rows: List[Dict[str, Any]], *, risk: Optional[str] = None,
    min_expected_profit: Optional[float] = None, min_roi: Optional[float] = None,
    min_confidence: Optional[float] = None, min_liquidity: Optional[float] = None,
    signals: Optional[set] = None,
    sales_by_card: Optional[Dict[int, List[Dict[str, Any]]]] = None,
) -> List[Any]:
    out = []
    sales_by_card = sales_by_card or {}
    for row in rows:
        ci = evaluate_card(row, sales=sales_by_card.get(int(row["source_card_id"]), []))
        if signals is not None and ci.signal not in signals:
            continue
        if risk is not None and ci.risk_level != risk:
            continue
        if min_expected_profit is not None and (
            ci.expected_profit_after_tax is None or float(ci.expected_profit_after_tax) < min_expected_profit
        ):
            continue
        if min_roi is not None and (ci.expected_roi is None or float(ci.expected_roi) < min_roi):
            continue
        if min_confidence is not None and (ci.confidence_score is None or ci.confidence_score < min_confidence):
            continue
        if min_liquidity is not None and (ci.liquidity_score is None or ci.liquidity_score < min_liquidity):
            continue
        out.append((row, ci))
    return out


@router.get("/players/{card_id}")
async def get_player_detail(
    card_id: int, provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    row = await provider.get_player(card_id)
    if row is None:
        raise HTTPException(404, "Card not found in the FUT.GG-backed market layer")

    # The detail view is the one place we can always afford the extra
    # query for raw sales, so the trend layer runs with real data here
    # rather than degrading to "trend unknown". List endpoints fetch
    # sales in batch (see _score_and_filter's caller).
    try:
        sales = await provider.get_recent_sales(card_id)
    except Exception:
        log.warning("get_recent_sales failed for card_id=%s", card_id, exc_info=True)
        sales = []

    ci = evaluate_card(row, sales=sales)
    if _is_stale_for_tier(ci.price_age_minutes, row.get("price_tier")):
        # A user actually opening this card is the strongest "someone
        # might act on this" signal short of it already being a surfaced
        # opportunity - re-queue it for the price worker's next pass
        # instead of leaving it to wait out its tier's normal interval.
        # Best-effort: a transient DB hiccup here must never break the
        # page load itself.
        try:
            await provider.bump_price_priority(card_id)
        except Exception:
            log.warning("bump_price_priority failed for card_id=%s", card_id, exc_info=True)
    # Flattened onto the top level (frontend reads e.g. data.current_bin
    # directly) as well as under "player"/"intelligence" for consumers
    # that prefer the grouped shape.
    return {
        "source": "futgg",
        **_item_with_intelligence(row, ci),
        "player": _serialize_player(row),
        "intelligence": _serialize_intelligence(ci),
    }


@router.get("/players/{card_id}/prices")
async def get_player_prices(
    card_id: int,
    period: Optional[str] = Query(None, description="1h|6h|24h|1d|7d|14d|30d - omit for full history"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    rows = await provider.get_price_history(card_id, period=period)
    total = len(rows)
    window = rows[(page - 1) * page_size: page * page_size]
    items = [
        {
            "lowest_bin": r["lowest_bin"],
            "price_range_low": r["price_range_low"],
            "price_range_high": r["price_range_high"],
            "source_age_text": r["source_age_text"],
            "captured_at": r["captured_at"].isoformat(),
        }
        for r in window
    ]
    return {"card_id": card_id, "items": items, "page": page, "page_size": page_size, "total": total}


@router.get("/players/{card_id}/sales")
async def get_player_sales(
    card_id: int,
    days: int = Query(MAX_CHART_DAYS, ge=1, le=MAX_CHART_DAYS),
    limit: int = Query(MAX_CHART_SALES, ge=1, le=MAX_CHART_SALES),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    """Sales series for the chart, over an explicit window.

    `days` exists because the frontend range picker previously filtered
    client-side over whatever this returned, which was always the newest
    50 prints - a few hours on a liquid card, so every range drew the same
    few hours and the toggle appeared dead. The window is capped at
    MAX_CHART_DAYS because futgg_market_snapshot's own sales window stops
    at 14 days; a longer range would promise data that does not exist.
    """
    rows = await provider.get_sales_series(card_id, days=days, limit=limit)
    # "No sales" and "we have not looked" are different facts, and the UI
    # could not tell them apart. See get_sales_coverage: for most of the
    # catalogue an empty window means the sales worker has not visited
    # this card recently, not that nobody bought it.
    coverage = await provider.get_sales_coverage(card_id)
    items = [
        {
            "sold_price": r["sold_price"],
            "ea_tax": r["ea_tax"],
            "net_price": r["net_price"],
            "age_text": r["source_age_text"],
            "approximate_sold_at": r["approximate_sold_at"].isoformat(),
            "approximate": True,  # never present approximate_sold_at as exact
            "source_age_seconds": r["source_age_seconds"],
        }
        for r in rows
    ]
    # The chart needs to distinguish "this range is empty" from "this card
    # only has six hours of history", so it can say so rather than looking
    # like the picker is broken.
    span_seconds = None
    if len(items) > 1:
        span_seconds = int(
            (rows[0]["approximate_sold_at"] - rows[-1]["approximate_sold_at"]).total_seconds()
        )
    checked_at = coverage.get("checked_at")
    return {
        "card_id": card_id,
        "items": items,
        "count": len(items),
        "requested_days": days,
        "covered_seconds": span_seconds,
        "truncated_by_limit": len(items) >= limit,
        # Everything the caller needs to say WHY a window is empty.
        "sales_checked_at": checked_at.isoformat() if checked_at else None,
        "sales_never_checked": coverage.get("never_checked"),
        "sales_check_stale_for_window": (
            checked_at is not None
            and (datetime.now(timezone.utc) - checked_at).total_seconds() > days * 86400
        ),
        "note": "approximate_sold_at is derived from a relative age string (e.g. '4 minutes ago') at scrape time, not an exact EA transaction timestamp.",
    }


@router.get("/opportunities")
async def list_opportunities(
    rating: Optional[int] = Query(None, ge=0, le=99),
    rating_min: Optional[int] = Query(None, ge=0, le=99),
    rating_max: Optional[int] = Query(None, ge=0, le=99),
    position: Optional[str] = None,
    rarity: Optional[str] = None,
    club: Optional[str] = None,
    league: Optional[str] = None,
    nation: Optional[str] = None,
    max_price: Optional[int] = Query(None, ge=0),
    min_price: Optional[int] = Query(None, ge=0),
    max_price_age_minutes: Optional[int] = Query(None, ge=0),
    max_price_age: Optional[int] = Query(None, ge=0, description="Alias for max_price_age_minutes"),
    risk: Optional[str] = Query(None, description="low|medium|high|avoid - exact match"),
    min_profit: Optional[float] = Query(None),
    min_roi: Optional[float] = Query(None),
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    min_liquidity: Optional[float] = Query(None, ge=0, le=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    rating_min, rating_max = _resolve_rating_bounds(rating, rating_min, rating_max)
    max_price_age_minutes = max_price_age_minutes if max_price_age_minutes is not None else max_price_age
    filters = _filters_from_query(
        None, rating_min, rating_max, position, rarity, club, league, nation,
        max_price, min_price, max_price_age_minutes,
    )
    rows = await provider.search_players(filters, limit=CANDIDATE_SCAN_LIMIT, offset=0, order_by=CANDIDATE_SCAN_ORDER_BY)
    scored = _score_and_filter(
        rows, risk=risk, min_expected_profit=min_profit, min_roi=min_roi,
        min_confidence=min_confidence, min_liquidity=min_liquidity,
        signals={"buy", "strong_buy"},
        sales_by_card=await _sales_for_rows(provider, rows),
    )
    # Sort by opportunity strength: strong_buy first, then by expected ROI.
    scored.sort(
        key=lambda pair: (
            0 if pair[1].signal == "strong_buy" else 1,
            -(float(pair[1].expected_roi) if pair[1].expected_roi is not None else 0.0),
        )
    )
    total = len(scored)
    window = scored[(page - 1) * page_size: page * page_size]
    items = [_item_with_intelligence(row, ci) for row, ci in window]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


_SORT_ALIASES = {"best": "best_opportunity"}

_TRADE_FINDER_SORTS = {
    "best_opportunity": lambda row_ci: (
        0 if row_ci[1].signal == "strong_buy" else (1 if row_ci[1].signal == "buy" else 2),
        -(row_ci[1].confidence_score or 0.0) * float(row_ci[1].expected_roi or 0),
    ),
    "profit": lambda row_ci: -(float(row_ci[1].expected_profit_after_tax) if row_ci[1].expected_profit_after_tax is not None else 0.0),
    "roi": lambda row_ci: -(float(row_ci[1].expected_roi) if row_ci[1].expected_roi is not None else 0.0),
    "confidence": lambda row_ci: -(row_ci[1].confidence_score or 0.0),
    "liquidity": lambda row_ci: -(row_ci[1].liquidity_score or 0.0),
    "newest": lambda row_ci: row_ci[0].get("price_updated_at") is None,  # placeholder tiebreak, refined below
    "freshest": lambda row_ci: (row_ci[1].price_age_minutes if row_ci[1].price_age_minutes is not None else float("inf")),
}


@router.get("/trade-finder")
async def trade_finder(
    budget: Optional[int] = Query(None, ge=0, description="Max current BIN"),
    min_profit: Optional[float] = Query(None),
    min_roi: Optional[float] = Query(None),
    risk_tolerance: Optional[str] = Query(None, description="low|medium|high - includes that risk level and better"),
    risk: Optional[str] = Query(None, description="Alias for risk_tolerance"),
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    position: Optional[str] = None,
    rarity: Optional[str] = None,
    rating: Optional[int] = Query(None, ge=0, le=99),
    rating_min: Optional[int] = Query(None, ge=0, le=99),
    rating_max: Optional[int] = Query(None, ge=0, le=99),
    min_liquidity: Optional[float] = Query(None, ge=0, le=1),
    max_price_age_minutes: Optional[int] = Query(None, ge=0),
    max_price_age: Optional[int] = Query(None, ge=0, description="Alias for max_price_age_minutes"),
    sort_by: str = Query("best_opportunity", description="best_opportunity|profit|roi|confidence|liquidity|newest|freshest"),
    sort: Optional[str] = Query(None, description="Alias for sort_by; also accepts 'best' for 'best_opportunity'"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    if sort is not None:
        sort_by = _SORT_ALIASES.get(sort, sort)
    if sort_by not in _TRADE_FINDER_SORTS:
        raise HTTPException(400, f"sort_by must be one of {sorted(_TRADE_FINDER_SORTS)}")
    risk_tolerance = risk_tolerance if risk_tolerance is not None else risk
    rating_min, rating_max = _resolve_rating_bounds(rating, rating_min, rating_max)
    max_price_age_minutes = max_price_age_minutes if max_price_age_minutes is not None else max_price_age

    filters = _filters_from_query(
        None, rating_min, rating_max, position, rarity, None, None, None,
        budget, None, max_price_age_minutes,
    )
    rows = await provider.search_players(filters, limit=CANDIDATE_SCAN_LIMIT, offset=0, order_by=CANDIDATE_SCAN_ORDER_BY)

    risk_order = {"low": 0, "medium": 1, "high": 2, "avoid": 3}
    max_risk_rank = risk_order.get(risk_tolerance, 2) if risk_tolerance else None

    scored = _score_and_filter(
        rows, min_expected_profit=min_profit, min_roi=min_roi,
        min_confidence=min_confidence, min_liquidity=min_liquidity,
        signals={"buy", "strong_buy"},
        sales_by_card=await _sales_for_rows(provider, rows),
    )
    if max_risk_rank is not None:
        scored = [(row, ci) for row, ci in scored if risk_order.get(ci.risk_level, 3) <= max_risk_rank]

    if sort_by == "newest":
        # price_updated_at is nullable (a card can be discovered before its
        # first price sync completes) - `or ""` as the None-fallback used
        # to crash the whole request with a 500 the moment any row in the
        # candidate scan had a real datetime AND any other row had None
        # (`TypeError: '<' not supported between instances of 'str' and
        # 'datetime.datetime'`), since Python won't compare a str sentinel
        # against a datetime. datetime.min (tz-aware, to compare against
        # the tz-aware column) sorts a missing timestamp to the oldest
        # position instead, which is also the semantically correct
        # "least new" ordering for reverse=True.
        scored.sort(
            key=lambda pair: pair[0].get("price_updated_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    else:
        scored.sort(key=_TRADE_FINDER_SORTS[sort_by])

    total = len(scored)
    window = scored[(page - 1) * page_size: page * page_size]
    items = [_item_with_intelligence(row, ci) for row, ci in window]
    return {"items": items, "page": page, "page_size": page_size, "total": total, "sort_by": sort_by}


@router.get("/market/freshness")
async def market_freshness(
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    summary = await provider.get_freshness_summary()
    heartbeats = []
    for h in summary["heartbeats"]:
        heartbeats.append({
            "worker": h["worker"],
            "last_run_at": h["last_run_at"].isoformat() if h["last_run_at"] else None,
            "ok": h["ok"],
            "detail": h["detail"],
        })
    return {
        "heartbeats": heartbeats,
        "cards_discovered": summary["cards_discovered"],
        "cards_priced": summary["cards_priced"],
        "cards_no_market": summary["cards_no_market"],
        "cards_untradeable": summary["cards_untradeable"],
        "cards_stale": summary["cards_stale"],
        "stale_by_price_tier": summary["stale_by_price_tier"],
        "stale_sales_by_priority_tier": summary.get("stale_sales_by_priority_tier", {}),
        "latest_source_errors": summary["latest_source_errors"],
    }


@router.get("/market/priority-metrics")
async def priority_metrics(
    provider: FutggMarketDataProvider = ProviderDep,
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    """Admin-only (production-readiness review: this was shipped public
    alongside every other endpoint in this router, which is fine for
    player-facing market data but wrong for internal scraper-operations
    detail - gated the same way app/routers/admin.py's own endpoints
    are, via require_admin, rather than inventing a new auth pattern).

    Observability for the adaptive priority engine (Milestone 1
    classification + Milestone 1.5 conservative demotion - see
    app/services/priority_recompute.py). baseline vs effective tier
    counts (they can differ - a card whose effective tier is only
    elevated because of an active trigger is worth seeing separately
    from one that's baseline-CRITICAL), active trigger counts by type,
    and the last 24h's classification/trigger/demotion events - read
    directly off card_priority/priority_triggers/priority_engine_events
    rather than a separate metrics stack (this codebase has no
    Prometheus/`/metrics` endpoint anywhere - reusing this router's
    existing convention instead of introducing one)."""
    pool = provider._pool  # same core pool every other method here already uses
    async with pool.acquire() as conn:
        try:
            baseline_rows = await conn.fetch(
                "SELECT baseline_tier AS tier, count(*) AS card_count, avg(importance_score) AS avg_score "
                "FROM card_priority GROUP BY baseline_tier"
            )
            effective_rows = await conn.fetch(
                "SELECT effective_tier AS tier, count(*) AS card_count "
                "FROM card_priority GROUP BY effective_tier"
            )
            event_rows = await conn.fetch(
                """
                SELECT reason, count(*) AS event_count
                FROM priority_engine_events
                WHERE occurred_at > now() - interval '24 hours'
                GROUP BY reason
                """
            )
            active_trigger_rows = await conn.fetch(
                """
                SELECT trigger_type, count(*) AS trigger_count
                FROM priority_triggers
                WHERE expires_at IS NULL OR expires_at > now()
                GROUP BY trigger_type
                """
            )
            last_computed_at = await conn.fetchval("SELECT max(computed_at) FROM card_priority")

            # Conservative-demotion eligible count + projected request
            # reduction - reuses the same predicate/query
            # run_conservative_demotion_pass() itself uses, but read-only:
            # this reports "how many cards WOULD be affected right now"
            # regardless of whether PRIORITY_ENGINE_ALLOW_CONSERVATIVE_
            # DEMOTION is actually on, so the projection is visible before
            # anyone flips the flag.
            demotion_config = PRIORITY_ENGINE_CONFIG
            candidates = await conn.fetch(
                _CONSERVATIVE_DEMOTION_CANDIDATES_QUERY, demotion_config.trigger_confirmation_grace_hours
            )
            as_of = datetime.now(timezone.utc)
            eligible_rows = [
                row for row in candidates
                if is_eligible_for_conservative_demotion(
                    card_type_component=row["card_type_component"] or 0.0,
                    sales_count=int(row["sales_count"]),
                    watcher_count=int(row["watcher_count"]),
                    has_active_trigger=False,  # already excluded by the candidates query's NOT EXISTS
                    first_seen_at=row["first_seen_at"],
                    next_sales_due_at=row["next_sales_due_at"],
                    last_sales_status_at=row["last_sales_status_at"],
                    as_of=as_of,
                    config=demotion_config,
                )
            ]
            conservative_demotion_eligible_count = len(eligible_rows)
            # Requests/day before = each eligible card's current interval
            # (from its live next_sales_due_at - last_sales_status_at gap);
            # after = the conservative-demotion ceiling. Both estimates,
            # labelled as such - see priority_validation_report.py's
            # identical formula for the full report-grade version.
            current_intervals_min = [
                (row["next_sales_due_at"] - row["last_sales_status_at"]).total_seconds() / 60.0
                for row in eligible_rows
                if row["next_sales_due_at"] and row["last_sales_status_at"]
                and (row["next_sales_due_at"] - row["last_sales_status_at"]).total_seconds() > 0
            ]
            requests_per_day_before_est = sum(1440.0 / m for m in current_intervals_min)
            requests_per_day_after_est = (
                conservative_demotion_eligible_count * 1440.0 / demotion_config.conservative_demotion_max_interval_minutes
                if conservative_demotion_eligible_count else 0.0
            )

            # Scheduling health: overdue cards by tier, oldest sales data
            # by tier, discovery-ceiling violations.
            overdue_rows = await conn.fetch(
                """
                SELECT cp.effective_tier AS tier, count(*) AS overdue_count
                FROM futgg_players p
                JOIN card_priority cp ON cp.card_id = p.source_card_id
                WHERE p.is_active AND p.next_sales_due_at IS NOT NULL
                  AND p.next_sales_due_at < now()
                GROUP BY cp.effective_tier
                """
            )
            oldest_sales_rows = await conn.fetch(
                """
                SELECT cp.effective_tier AS tier, min(p.last_sales_status_at) AS oldest
                FROM futgg_players p
                JOIN card_priority cp ON cp.card_id = p.source_card_id
                WHERE p.is_active AND p.last_sales_status_at IS NOT NULL
                GROUP BY cp.effective_tier
                """
            )
            # "Discovery ceiling violated" = a DORMANT-tier card whose
            # next_sales_due_at has drifted further out than the
            # conservative-demotion ceiling permits even accounting for
            # the anchor (last_sales_status_at + ceiling) - i.e. it is
            # starved beyond what this milestone's own hard cap allows.
            # Should always be 0; a nonzero count is a real bug signal.
            discovery_ceiling_violations = await conn.fetchval(
                """
                SELECT count(*) FROM futgg_players p
                JOIN card_priority cp ON cp.card_id = p.source_card_id
                WHERE p.is_active AND cp.effective_tier = 'DORMANT'
                  AND p.next_sales_due_at IS NOT NULL AND p.last_sales_status_at IS NOT NULL
                  AND p.next_sales_due_at > p.last_sales_status_at + ($1 * interval '1 minute')
                """,
                demotion_config.conservative_demotion_max_interval_minutes,
            )

            heartbeat_row = await conn.fetchrow(
                "SELECT last_run_at, ok, detail FROM pipeline_heartbeats WHERE worker = 'priority_recompute'"
            )
        except Exception:
            # card_priority/priority_triggers/priority_engine_events
            # (migration 044) not created yet on this database - report
            # an empty-but-valid shape rather than 500ing, same
            # tolerance the freshness endpoint above gives an
            # un-bootstrapped deployment.
            return {
                "cards_per_baseline_tier": {}, "cards_per_effective_tier": {}, "avg_score_per_baseline_tier": {},
                "classifications_24h": 0, "trigger_activations_24h": 0, "trigger_expirations_24h": 0,
                "conservative_demotions_24h": 0, "active_triggers_by_type": {}, "last_computed_at": None,
                "conservative_demotion_eligible_count": 0,
                "projected_requests_per_day_before_estimate": 0.0,
                "projected_requests_per_day_after_estimate": 0.0,
                "projected_request_reduction_pct_estimate": None,
                "overdue_cards_by_tier": {}, "oldest_sales_data_by_tier": {},
                "discovery_ceiling_violations": 0,
                "last_recompute_run": None,
                "engine_version": PRIORITY_ENGINE_CONFIG.version,
            }

    cards_per_baseline_tier = {r["tier"]: int(r["card_count"]) for r in baseline_rows}
    cards_per_effective_tier = {r["tier"]: int(r["card_count"]) for r in effective_rows}
    avg_score_per_baseline_tier = {r["tier"]: float(r["avg_score"]) for r in baseline_rows if r["avg_score"] is not None}
    events_by_reason = {r["reason"]: int(r["event_count"]) for r in event_rows}
    active_triggers_by_type = {r["trigger_type"]: int(r["trigger_count"]) for r in active_trigger_rows}
    overdue_cards_by_tier = {r["tier"]: int(r["overdue_count"]) for r in overdue_rows}
    oldest_sales_data_by_tier = {r["tier"]: r["oldest"].isoformat() for r in oldest_sales_rows if r["oldest"]}

    reduction_pct = None
    if requests_per_day_before_est > 0:
        reduction_pct = round(
            100.0 * (requests_per_day_before_est - requests_per_day_after_est) / requests_per_day_before_est, 1
        )

    last_recompute_run = None
    if heartbeat_row is not None:
        detail_raw = heartbeat_row["detail"]
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else (detail_raw or {})
        except (ValueError, TypeError):
            detail = {}
        last_recompute_run = {
            "last_run_at": heartbeat_row["last_run_at"].isoformat() if heartbeat_row["last_run_at"] else None,
            "ok": bool(heartbeat_row["ok"]),
            "duration_seconds": detail.get("duration_seconds"),
            "cards_processed": detail.get("cards_processed"),
            "cards_failed": detail.get("cards_failed"),
            "engine_version": detail.get("engine_version"),
        }

    return {
        "cards_per_baseline_tier": cards_per_baseline_tier,
        "cards_per_effective_tier": cards_per_effective_tier,
        "avg_score_per_baseline_tier": avg_score_per_baseline_tier,
        "classifications_24h": events_by_reason.get("initial", 0) + events_by_reason.get("baseline_change", 0),
        "trigger_activations_24h": events_by_reason.get("trigger_activated", 0),
        "trigger_expirations_24h": events_by_reason.get("trigger_expired", 0),
        "conservative_demotions_24h": events_by_reason.get("conservative_demotion", 0),
        "active_triggers_by_type": active_triggers_by_type,
        "last_computed_at": last_computed_at.isoformat() if last_computed_at else None,
        "conservative_demotion_eligible_count": conservative_demotion_eligible_count,
        "projected_requests_per_day_before_estimate": round(requests_per_day_before_est, 1),
        "projected_requests_per_day_after_estimate": round(requests_per_day_after_est, 1),
        "projected_request_reduction_pct_estimate": reduction_pct,
        "overdue_cards_by_tier": overdue_cards_by_tier,
        "oldest_sales_data_by_tier": oldest_sales_data_by_tier,
        "discovery_ceiling_violations": int(discovery_ceiling_violations or 0),
        "last_recompute_run": last_recompute_run,
        "engine_version": PRIORITY_ENGINE_CONFIG.version,
    }


# =============================================================================
# Track record + evaluation coverage
# =============================================================================
#
# These replace the legacy /api/v2/recommendations/track-record, which
# grades ml_labels rows produced from fair_value_mv - the broken FUTBIN
# view that no longer drives a single user-visible recommendation. That
# endpoint was therefore reporting on a pipeline nobody uses, while the
# FUT.GG engine that drives everything had no outcome feedback at all.
#
# Deliberately ungated, for the same reason the legacy one was: a trust
# signal that only paying users can see is not doing its job.


def _degraded_reason(exc: BaseException) -> str:
    """A short, safe description of why a diagnostic endpoint is degraded.

    Deliberately names the missing relation when that is the cause, since
    that is the single most useful fact for whoever is looking - a
    skipped migration is invisible from the outside otherwise. Anything
    unrecognised is reported by exception class only; no driver message
    is echoed, because those can carry connection details and these two
    endpoints are ungated by design.
    """
    if isinstance(exc, asyncpg.exceptions.UndefinedTableError):
        relation = getattr(exc, "table_name", None)
        if relation:
            return f"required table '{relation}' does not exist (migration not applied)"
        return "a required table does not exist (migration not applied)"
    if isinstance(exc, asyncpg.exceptions.UndefinedColumnError):
        return "schema is behind the application (migration not applied)"
    if isinstance(exc, asyncpg.PostgresError):
        return f"database error ({type(exc).__name__})"
    return f"unexpected error ({type(exc).__name__})"


@router.get("/market/track-record")
async def market_track_record(
    horizon: str = Query("24h", description="24h|48h|7d"),
    window_days: int = Query(90, ge=1, le=365),
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    """Outcome-graded performance of the FUT.GG engine's own calls.

    Every number here comes from recommendations frozen at the moment
    they were made and graded later in chronological order - entry before
    exit, no best-price-in-hindsight. Percentages are withheld entirely
    below the minimum sample size rather than shown with a caveat.
    """
    from app.services.futgg_outcome_grader import HORIZONS
    from app.services.futgg_recommendation_store import track_record

    if horizon not in HORIZONS:
        raise HTTPException(400, f"horizon must be one of {sorted(HORIZONS)}")
    try:
        result = await track_record(provider._pool, horizon=horizon, window_days=window_days)
        result["status"] = "ok"
        return result
    except Exception as exc:
        # An honest empty state still beats a 500 on a public trust page -
        # but "empty" and "broken" must not look the same, which is what
        # this handler used to do. It returned a fully success-shaped
        # payload with total_recommendations=0 and the "no graded outcomes
        # yet" note, so a missing table read exactly like a quiet first
        # day. Migration 040 was skipped in production for want of a
        # prerequisite table and this endpoint reported it as normal for
        # seven hours.
        #
        # `status` is now the field to trust: "ok" only ever comes from
        # the real query below, so any caller can tell the difference.
        log.warning("track_record query failed", exc_info=True)
        return {
            "horizon": horizon,
            "window_days": window_days,
            "status": "unavailable",
            "error": _degraded_reason(exc),
            "has_enough_data": False,
            "headline": None,
            "breakdowns": None,
            "methodology": (
                "The track record could not be read. This is a fault, not an "
                "absence of results - no conclusion should be drawn from it."
            ),
        }


@router.get("/market/evaluation-coverage")
async def market_evaluation_coverage(
    provider: FutggMarketDataProvider = ProviderDep,
) -> Dict[str, Any]:
    """How much of the card pool the engine is actually evaluating.

    Exists because the previous single `ORDER BY sales_count DESC LIMIT
    500` scan silently confined the engine to the most-traded 500 cards
    with no symptom other than "why does this card never appear". Coverage
    is now a number you can look at.
    """
    from app.services.futgg_scanner import coverage_report

    try:
        result = await coverage_report(provider._pool)
        result["status"] = "ok"
        return result
    except Exception as exc:
        # Same trap as track-record above: the old fallback returned
        # player_pool=0, which is indistinguishable from a working call
        # against an empty pool - except that it never can be, since
        # coverage_report's first query is a plain count over
        # futgg_players. A zero here was always a lie.
        log.warning("coverage_report failed", exc_info=True)
        return {
            "status": "unavailable",
            "error": _degraded_reason(exc),
            "player_pool": None,
            "cards_evaluated_24h": None,
            "pool_coverage_24h_pct": None,
        }
