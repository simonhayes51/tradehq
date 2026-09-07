"""Precompute the expensive market-scoring slice used by /api/v2/dashboard.

The Home response also contains user-specific alerts, free-pick gating, events and
SBC data, so caching the whole HTTP response would be incorrect. The expensive
part is user-independent: building the segmented FUT.GG candidate pool and
running evaluate_card() over it for buy/avoid/recent/regime calculations.

This module caches that evaluated pair set for five minutes with stale-while-
revalidate semantics. Dashboard keeps constructing its normal per-user response,
but its market-scoring helpers read this already-computed pair set instead of
re-running ~1,300 synchronous evaluations in the request.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("dashboard_market_cache")

CACHE_SECONDS = 300.0
_lock = asyncio.Lock()
_pairs: Optional[List[Tuple[Dict[str, Any], Any]]] = None
_cached_at = 0.0
_refresh_task: Optional[asyncio.Task] = None


async def _compute(provider) -> List[Tuple[Dict[str, Any], Any]]:
    from app.routers.v2 import futgg_market as market

    started = time.monotonic()
    rows = await market._segmented_candidates(provider)
    sales_by_card = await market._sales_for_rows(provider, rows)
    bin_by_card = await market._bin_observations_for_rows(provider, rows)
    pairs = await market._score_and_filter(
        rows,
        sales_by_card=sales_by_card,
        bin_by_card=bin_by_card,
    )
    log.info(
        "dashboard market cache computed %d cards in %.2fs",
        len(pairs),
        time.monotonic() - started,
    )
    return pairs


async def _refresh(provider) -> List[Tuple[Dict[str, Any], Any]]:
    global _pairs, _cached_at
    async with _lock:
        # Another waiter may already have refreshed it.
        if _pairs is not None and time.monotonic() - _cached_at < CACHE_SECONDS:
            return _pairs
        pairs = await _compute(provider)
        _pairs = pairs
        _cached_at = time.monotonic()
        return pairs


def _schedule_refresh(provider) -> None:
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return

    async def runner() -> None:
        try:
            await _refresh(provider)
        except Exception:
            log.warning("dashboard market cache background refresh failed", exc_info=True)

    _refresh_task = asyncio.create_task(runner())


async def get_pairs(provider) -> List[Tuple[Dict[str, Any], Any]]:
    """Return current evaluated market pairs without making users wait for refresh.

    First ever call has no stale value to serve, so it computes synchronously.
    Normal production startup prewarms this cache before users arrive. Once a
    value exists, expiry triggers a background refresh and the request receives
    the last good value immediately.
    """
    if _pairs is None:
        return await _refresh(provider)
    if time.monotonic() - _cached_at >= CACHE_SECONDS:
        _schedule_refresh(provider)
    return _pairs


async def prewarm(provider) -> None:
    try:
        await _refresh(provider)
    except Exception:
        log.warning("dashboard market cache prewarm failed", exc_info=True)


def install_dashboard_market_cache(application) -> None:
    """Patch only Dashboard's imported helper references.

    get_dashboard() keeps all of its existing auth, free-pick, alert, events,
    SBC and response-shape logic. Only its three market helpers are replaced.
    Other routes keep their existing implementations.
    """
    from app.routers.v2 import dashboard as dashboard_module
    from app.services.snapshot_surface_routes import _pair_filter

    async def cached_segmented_candidates(provider):
        pairs = await get_pairs(provider)
        return [row for row, _ in pairs]

    async def cached_sales_for_rows(provider, rows):
        # cached_score_and_filter below consumes already-evaluated CI objects,
        # so Dashboard no longer needs raw sales rows in the HTTP request.
        return {}

    async def cached_score_and_filter(rows, **kwargs):
        # The cached pair set may be broader than the rows supplied by the
        # caller, so preserve the original helper's row-subset semantics.
        allowed = {int(row["source_card_id"]) for row in rows}
        pairs = [pair for pair in await get_pairs_from_cache_only() if int(pair[0]["source_card_id"]) in allowed]
        return _pair_filter(
            pairs,
            risk=kwargs.get("risk"),
            min_profit=kwargs.get("min_expected_profit"),
            min_roi=kwargs.get("min_roi"),
            min_confidence=kwargs.get("min_confidence"),
            min_liquidity=kwargs.get("min_liquidity"),
            signals=kwargs.get("signals"),
            require_confirmed_sales=kwargs.get("require_confirmed_sales", False),
            max_evidence_age_minutes=kwargs.get("max_evidence_age_minutes"),
        )

    async def get_pairs_from_cache_only():
        # cached_segmented_candidates is always called before any scoring pass
        # in get_dashboard(), so this should be populated. Keep a defensive
        # empty list rather than accidentally invoking expensive work with no
        # provider available.
        return _pairs or []

    dashboard_module._segmented_candidates = cached_segmented_candidates
    dashboard_module._sales_for_rows = cached_sales_for_rows
    dashboard_module._score_and_filter = cached_score_and_filter
    log.info("dashboard market cache helpers installed")

    @application.app.on_event("startup")
    async def _schedule_dashboard_market_prewarm() -> None:
        async def runner() -> None:
            # Let DB pools/migrations and the lighter request caches settle.
            await asyncio.sleep(8)
            try:
                from app.db import get_core_pool
                from app.services.market_data_provider import FutggMarketDataProvider

                pool = await get_core_pool()
                await prewarm(FutggMarketDataProvider(pool))
            except Exception:
                log.warning("dashboard market cache startup prewarm failed", exc_info=True)

        asyncio.create_task(runner())
