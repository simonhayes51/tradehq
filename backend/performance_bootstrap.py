"""Production API bootstrap for the request-path performance protections."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, FrozenSet, List, Tuple

import uvicorn

CACHE_SECONDS = float(os.getenv("FUTGG_REQUEST_BATCH_CACHE_SECONDS", "90"))
PREWARM_DELAY_SECONDS = float(os.getenv("FUTGG_REQUEST_BATCH_PREWARM_DELAY_SECONDS", "5"))
log = logging.getLogger("performance_bootstrap")


def _subset(mapping: Dict[int, Any], ids: FrozenSet[int]) -> Dict[int, Any]:
    return {card_id: mapping[card_id] for card_id in ids if card_id in mapping}


def install_market_batch_singleflight() -> None:
    from app.services import market_data_provider
    from app.services.bin_valuation_loader import (
        OBSERVATION_WINDOW_DAYS,
        load_hourly_bin_valuation_observations,
    )

    bin_lock = asyncio.Lock()
    bin_cache: Dict[Tuple[int, int], Dict[str, Any]] = {}

    async def cached_bin_loader(self, card_ids: List[int]):
        ids = frozenset(int(card_id) for card_id in card_ids)
        if not ids:
            return {}
        key = (id(self._pool), OBSERVATION_WINDOW_DAYS)
        now = time.monotonic()
        entry = bin_cache.get(key)
        if entry is not None and now - entry["at"] < CACHE_SECONDS:
            missing = ids - entry["ids"]
            if not missing:
                return _subset(entry["data"], ids)

        async with bin_lock:
            now = time.monotonic()
            entry = bin_cache.get(key)
            if entry is None or now - entry["at"] >= CACHE_SECONDS:
                entry = {"at": now, "ids": frozenset(), "data": {}}
                bin_cache[key] = entry

            missing = ids - entry["ids"]
            if missing:
                started = time.monotonic()
                fetched = await load_hourly_bin_valuation_observations(
                    self._pool,
                    list(missing),
                    days=OBSERVATION_WINDOW_DAYS,
                )
                entry["data"].update(fetched)
                entry["ids"] = entry["ids"] | missing
                entry["at"] = time.monotonic()
                log.info(
                    "shared hourly BIN valuation batch fetched %d missing cards in %.0fms",
                    len(missing),
                    (time.monotonic() - started) * 1000,
                )
            return _subset(entry["data"], ids)

    market_data_provider.FutggMarketDataProvider.get_bin_observations_by_ids = cached_bin_loader

    original_sales_loader = market_data_provider.FutggMarketDataProvider.get_sales_by_ids
    sales_lock = asyncio.Lock()
    sales_cache: Dict[Tuple[int, int], Dict[str, Any]] = {}

    async def cached_sales_loader(
        self,
        card_ids: List[int],
        limit_per_card: int = market_data_provider.MAX_RECENT_SALES,
    ):
        ids = frozenset(int(card_id) for card_id in card_ids)
        if not ids:
            return {}
        key = (id(self._pool), int(limit_per_card))
        now = time.monotonic()
        entry = sales_cache.get(key)
        if entry is not None and now - entry["at"] < CACHE_SECONDS:
            missing = ids - entry["ids"]
            if not missing:
                return _subset(entry["data"], ids)

        async with sales_lock:
            now = time.monotonic()
            entry = sales_cache.get(key)
            if entry is None or now - entry["at"] >= CACHE_SECONDS:
                entry = {"at": now, "ids": frozenset(), "data": {}}
                sales_cache[key] = entry

            missing = ids - entry["ids"]
            if missing:
                started = time.monotonic()
                fetched = await original_sales_loader(
                    self,
                    list(missing),
                    limit_per_card=int(limit_per_card),
                )
                entry["data"].update(fetched)
                entry["ids"] = entry["ids"] | missing
                entry["at"] = time.monotonic()
                log.info(
                    "shared sales batch fetched %d missing cards in %.0fms",
                    len(missing),
                    (time.monotonic() - started) * 1000,
                )
            return _subset(entry["data"], ids)

    market_data_provider.FutggMarketDataProvider.get_sales_by_ids = cached_sales_loader
    log.info("installed market-history single-flight caches (ttl=%.0fs)", CACHE_SECONDS)


def _install_prewarm(application) -> None:
    @application.app.on_event("startup")
    async def _schedule_market_cache_prewarm() -> None:
        async def _run() -> None:
            await asyncio.sleep(PREWARM_DELAY_SECONDS)
            started = time.monotonic()
            try:
                from app.db import get_core_pool
                from app.routers.v2.futgg_market import _segmented_candidates
                from app.services.market_data_provider import FutggMarketDataProvider

                pool = await get_core_pool()
                provider = FutggMarketDataProvider(pool)
                rows = await _segmented_candidates(provider)
                card_ids = [int(row["source_card_id"]) for row in rows]
                if not card_ids:
                    log.info("market cache prewarm skipped: no candidates")
                    return

                await asyncio.gather(
                    provider.get_sales_by_ids(card_ids),
                    provider.get_bin_observations_by_ids(card_ids),
                )
                log.info(
                    "market cache prewarm complete: %d cards in %.1fs",
                    len(card_ids),
                    time.monotonic() - started,
                )
            except Exception:
                log.warning(
                    "market cache prewarm failed; on-demand path remains available",
                    exc_info=True,
                )

        asyncio.create_task(_run())


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    install_market_batch_singleflight()

    # Import the app only after the shared loaders have been wrapped.
    import main as application

    # Heavy market-list endpoints consume scanner-persisted evaluations rather
    # than re-running evaluate_card() across the market inside HTTP requests.
    from app.services.snapshot_surface_routes import install_snapshot_surface_routes
    install_snapshot_surface_routes(application)

    # Home still needs the full signal universe (including AVOID/regime), so
    # precompute that user-independent scoring slice off the request path while
    # leaving per-user alerts, gating, free picks and event data live.
    from app.services.dashboard_market_cache import install_dashboard_market_cache
    install_dashboard_market_cache(application)

    # Track Record is historical evidence. Cache the audited result and warm
    # the common windows after startup so a page view never has to replay tens
    # of sent Proof Ledger days synchronously.
    from app.services.track_record_cache_routes import (
        install_track_record_cache_route,
        install_track_record_prewarm,
    )
    install_track_record_cache_route(application)
    install_track_record_prewarm(application)

    _install_prewarm(application)

    workers = int(os.getenv("WEB_CONCURRENCY", "1"))
    if workers != 1:
        log.warning(
            "WEB_CONCURRENCY=%d requested; forcing 1 while request caches are process-local",
            workers,
        )

    uvicorn.run(
        application.app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("UVICORN_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
