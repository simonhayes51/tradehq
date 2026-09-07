"""Serve Dashboard's market-wide scoring slice from persisted intelligence.

Dashboard contains user-specific alerts, free-pick gating, events and SBC data,
so caching its whole response would be wrong. The expensive user-independent
part is the evaluated market pair set used for opportunities/avoid/regime.

The scanner now persists one current intelligence row for every successfully
evaluated card. This module therefore prefers that persisted state and only
falls back to the old live market-wide CPU pass while the new table is still
warming after its first deployment or if persisted coverage drops below a
conservative floor.

That fallback is intentionally transitional. Once production shows stable
coverage, it can be removed completely and Dashboard will never perform a
market-wide evaluate_card() pass again.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("dashboard_market_cache")

CACHE_SECONDS = float(os.getenv("DASHBOARD_MARKET_CACHE_SECONDS", "90"))
CURRENT_MAX_AGE_MINUTES = int(os.getenv("DASHBOARD_CURRENT_INTELLIGENCE_MAX_AGE_MINUTES", "180"))
MIN_PERSISTED_COVERAGE = float(os.getenv("DASHBOARD_CURRENT_INTELLIGENCE_MIN_COVERAGE", "0.60"))

_lock = asyncio.Lock()
_pairs: Optional[List[Tuple[Dict[str, Any], Any]]] = None
_cached_at = 0.0
_cache_source = "empty"
_refresh_task: Optional[asyncio.Task] = None


async def _load_persisted(provider):
    """Load persisted intelligence for Dashboard's existing candidate universe.

    Keeping the same segmented candidate IDs preserves the product's current
    selection contract while replacing only the expensive evaluation stage.
    """
    from app.routers.v2 import futgg_market as market
    from app.services.current_intelligence_surfaces import load_current_intelligence_pairs

    rows = await market._segmented_candidates(provider)
    if not rows:
        return [], 1.0, 0
    card_ids = [int(row["source_card_id"]) for row in rows]
    pairs = await load_current_intelligence_pairs(
        provider,
        card_ids=card_ids,
        max_age_minutes=CURRENT_MAX_AGE_MINUTES,
    )
    coverage = len(pairs) / len(card_ids)
    return pairs, coverage, len(card_ids)


async def _compute_live_fallback(provider) -> List[Tuple[Dict[str, Any], Any]]:
    """Old safe path retained only while persisted current state warms up."""
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
    log.warning(
        "dashboard current-intelligence coverage insufficient; live fallback scored %d cards in %.2fs",
        len(pairs),
        time.monotonic() - started,
    )
    return pairs


async def _compute(provider, *, allow_fallback: bool = True):
    started = time.monotonic()
    try:
        persisted, coverage, candidate_count = await _load_persisted(provider)
    except Exception:
        # Expected during the brief migration-080 rollout window on the very
        # first deploy. Do not turn a missing new table into a broken Home.
        log.warning("dashboard persisted-intelligence read failed", exc_info=True)
        persisted, coverage, candidate_count = [], 0.0, 0

    if persisted and coverage >= MIN_PERSISTED_COVERAGE:
        log.info(
            "dashboard market state loaded %d/%d persisted cards (%.1f%%) in %.2fs",
            len(persisted),
            candidate_count,
            coverage * 100.0,
            time.monotonic() - started,
        )
        return persisted, "persisted"

    if not allow_fallback:
        log.info(
            "dashboard persisted intelligence warming: %d/%d cards (%.1f%%; need %.0f%%)",
            len(persisted),
            candidate_count,
            coverage * 100.0,
            MIN_PERSISTED_COVERAGE * 100.0,
        )
        return persisted, "warming"

    return await _compute_live_fallback(provider), "live_fallback"


async def _refresh(provider, *, allow_fallback: bool = True):
    global _pairs, _cached_at, _cache_source
    async with _lock:
        if _pairs is not None and time.monotonic() - _cached_at < CACHE_SECONDS:
            return _pairs

        pairs, source = await _compute(provider, allow_fallback=allow_fallback)

        # Never promote a partial warmup population into the live cache. On the
        # first migration deploy this table fills over scanner passes; caching a
        # 10-20% slice would make Home look valid while silently omitting most
        # of its candidate universe. Keep the previous complete value if one
        # exists, otherwise stay empty until either coverage is sufficient or a
        # real request chooses the transitional live fallback.
        if source == "warming":
            return _pairs or []

        _pairs = pairs
        _cached_at = time.monotonic()
        _cache_source = source
        return _pairs


def _schedule_refresh(provider) -> None:
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return

    async def runner() -> None:
        try:
            await _refresh(provider)
        except Exception:
            log.warning("dashboard market state background refresh failed", exc_info=True)

    _refresh_task = asyncio.create_task(runner())


async def get_pairs(provider) -> List[Tuple[Dict[str, Any], Any]]:
    if _pairs is None:
        return await _refresh(provider)
    if time.monotonic() - _cached_at >= CACHE_SECONDS:
        _schedule_refresh(provider)
    return _pairs


async def prewarm(provider) -> None:
    """Prewarm from persisted state only; never burn CPU merely to prewarm."""
    try:
        await _refresh(provider, allow_fallback=False)
    except Exception:
        log.warning("dashboard persisted-state prewarm failed", exc_info=True)


def cache_health() -> Dict[str, Any]:
    return {
        "source": _cache_source,
        "cards": len(_pairs or []),
        "age_seconds": round(time.monotonic() - _cached_at, 1) if _pairs is not None else None,
        "ttl_seconds": CACHE_SECONDS,
    }


def install_dashboard_market_cache(application) -> None:
    """Patch only Dashboard's imported market helper references."""
    from app.routers.v2 import dashboard as dashboard_module
    from app.services.snapshot_surface_routes import _pair_filter

    async def cached_segmented_candidates(provider):
        pairs = await get_pairs(provider)
        return [row for row, _ in pairs]

    async def cached_sales_for_rows(provider, rows):
        # cached_score_and_filter consumes persisted CardIntelligence objects;
        # raw sales are no longer needed in the HTTP request.
        return {}

    async def cached_score_and_filter(rows, **kwargs):
        allowed = {int(row["source_card_id"]) for row in rows}
        pairs = [
            pair for pair in (_pairs or [])
            if int(pair[0]["source_card_id"]) in allowed
        ]
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

    dashboard_module._segmented_candidates = cached_segmented_candidates
    dashboard_module._sales_for_rows = cached_sales_for_rows
    dashboard_module._score_and_filter = cached_score_and_filter
    log.info("dashboard persisted-intelligence helpers installed")

    @application.app.on_event("startup")
    async def _schedule_dashboard_market_prewarm() -> None:
        async def runner() -> None:
            from app.db import get_core_pool
            from app.services.market_data_provider import FutggMarketDataProvider

            # First attempt catches an already-populated table on ordinary
            # redeploys. A second attempt lands after the scanner's 45s startup
            # delay on migration 080's first deploy. Partial state is never
            # cached by prewarm, so the second attempt remains meaningful.
            for delay in (8, 55):
                await asyncio.sleep(delay)
                try:
                    pool = await get_core_pool()
                    await prewarm(FutggMarketDataProvider(pool))
                    if _pairs is not None:
                        return
                except Exception:
                    log.warning("dashboard market state startup prewarm failed", exc_info=True)

        asyncio.create_task(runner())
