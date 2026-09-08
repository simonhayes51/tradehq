"""Serve Dashboard's market-wide scoring slice from persisted intelligence.

Dashboard contains user-specific alerts, free-pick gating, events and SBC data,
so caching its whole response would be wrong. The expensive user-independent
part is the evaluated market pair set used for opportunities/avoid/regime.

The scanner persists one current-intelligence row for every successfully
evaluated card, including non-actionable states. Dashboard reads that persisted
state only. It never falls back to evaluating the market inside the web process.

If persisted coverage is still warming, an existing complete cache stays live;
on a genuinely cold/empty process Dashboard gets an empty market slice until
coverage clears the conservative floor. That is preferable to reintroducing the
~1,300-card synchronous evaluation path that previously starved FastAPI's event
loop. Warming retries are throttled so concurrent Dashboard requests do not turn
one missing population into a database read storm.
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
WARMING_RETRY_SECONDS = float(os.getenv("DASHBOARD_CURRENT_INTELLIGENCE_WARMING_RETRY_SECONDS", "15"))

_lock = asyncio.Lock()
_pairs: Optional[List[Tuple[Dict[str, Any], Any]]] = None
_cached_at = 0.0
_last_attempt_at = 0.0
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


async def _compute(provider):
    started = time.monotonic()
    try:
        persisted, coverage, candidate_count = await _load_persisted(provider)
    except Exception:
        # A missing/new table or temporary DB read failure must not send Home
        # back through live evaluate_card(). Persisted state is the only source.
        log.warning("dashboard persisted-intelligence read failed", exc_info=True)
        return [], "warming"

    if persisted and coverage >= MIN_PERSISTED_COVERAGE:
        log.info(
            "dashboard market state loaded %d/%d persisted cards (%.1f%%) in %.2fs",
            len(persisted),
            candidate_count,
            coverage * 100.0,
            time.monotonic() - started,
        )
        return persisted, "persisted"

    log.info(
        "dashboard persisted intelligence warming: %d/%d cards (%.1f%%; need %.0f%%)",
        len(persisted),
        candidate_count,
        coverage * 100.0,
        MIN_PERSISTED_COVERAGE * 100.0,
    )
    return persisted, "warming"


async def _refresh(provider):
    global _pairs, _cached_at, _last_attempt_at, _cache_source
    async with _lock:
        now = time.monotonic()
        if _pairs is not None and now - _cached_at < CACHE_SECONDS:
            return _pairs
        if _pairs is None and _last_attempt_at and now - _last_attempt_at < WARMING_RETRY_SECONDS:
            return []

        _last_attempt_at = now
        pairs, source = await _compute(provider)
        _cache_source = source

        # Never promote a partial warmup population into the live cache. Keep
        # the previous complete universe if one exists; otherwise remain empty
        # until persisted coverage is genuinely sufficient.
        if source == "warming":
            return _pairs or []

        _pairs = pairs
        _cached_at = time.monotonic()
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
    """Prewarm persisted state only. This function cannot invoke live scoring."""
    try:
        await _refresh(provider)
    except Exception:
        log.warning("dashboard persisted-state prewarm failed", exc_info=True)


def cache_health() -> Dict[str, Any]:
    now = time.monotonic()
    return {
        "source": _cache_source,
        "cards": len(_pairs or []),
        "age_seconds": round(now - _cached_at, 1) if _pairs is not None else None,
        "last_attempt_age_seconds": round(now - _last_attempt_at, 1) if _last_attempt_at else None,
        "ttl_seconds": CACHE_SECONDS,
        "warming_retry_seconds": WARMING_RETRY_SECONDS,
        "live_fallback_enabled": False,
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
    log.info("dashboard persisted-only intelligence helpers installed")

    @application.app.on_event("startup")
    async def _schedule_dashboard_market_prewarm() -> None:
        async def runner() -> None:
            from app.db import get_core_pool
            from app.services.market_data_provider import FutggMarketDataProvider

            # First attempt catches an already-populated table on ordinary
            # redeploys. A second attempt lands after the scanner's 45s startup
            # delay on a genuinely fresh database. Partial state is never
            # promoted as complete.
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
