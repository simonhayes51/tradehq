"""Production API bootstrap for the request-path performance protections."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, FrozenSet, List, Tuple

import uvicorn

CACHE_SECONDS = float(os.getenv("FUTGG_REQUEST_BATCH_CACHE_SECONDS", "90"))
log = logging.getLogger("performance_bootstrap")


def _subset(mapping: Dict[int, Any], ids: FrozenSet[int]) -> Dict[int, Any]:
    return {card_id: mapping[card_id] for card_id in ids if card_id in mapping}


def install_market_batch_singleflight() -> None:
    """Collapse duplicate fresh evidence reads inside one API process.

    Bulk market surfaces no longer depend on these loaders on their request path,
    but fresh single-card evaluation still does. Keeping single-flight protects a
    burst of detail/Discord/Companion calls without preloading the whole market.
    """
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


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    install_market_batch_singleflight()

    # Import the app only after the shared loaders have been wrapped.
    import main as application

    # Heavy market-list endpoints consume scanner-persisted actionable
    # recommendations rather than re-running evaluate_card() across the market
    # inside HTTP requests.
    from app.services.snapshot_surface_routes import install_snapshot_surface_routes
    install_snapshot_surface_routes(application)

    # The one-card detail surface is genuinely a fresh evaluation. Route it
    # through the same canonical evidence path as Discord, Companion and held
    # positions without sending bulk surfaces back through that path.
    from app.services.fresh_card_routes import install_fresh_player_detail_route
    install_fresh_player_detail_route(application)

    # Home needs the full signal universe, including AVOID/regime. It now reads
    # scanner-persisted current intelligence. A temporary live-scoring fallback
    # remains only while migration 080/current-state coverage warms up.
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

    # Do NOT prewarm sales/BIN history for the whole segmented market here.
    # Those bulk HTTP consumers are persisted-state readers now, so loading
    # ~1,300 cards of evidence at every API restart is pure startup DB/memory
    # pressure. Fresh one-card callers populate the single-flight cache on
    # demand; the background scanner owns the market-wide evidence pass.

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
