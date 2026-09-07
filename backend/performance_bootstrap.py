"""Production API bootstrap with shared single-flight caches for the two
largest market-history reads used by Dashboard / Opportunities / Trade Finder.

Why this exists
---------------
A cold page load fires several endpoints at nearly the same time. The router
already caches the completed sales/BIN dictionaries, but that cache is only
populated *after* the database call finishes. Several concurrent callers can
therefore all observe the cache as empty and issue the same large batch query
before any one of them has had a chance to fill it.

This was visible in production on 2026-09-07: _segmented_candidates completed
in ~0.5-1.9s while /opportunities and /trade-finder/strategy/best_overall still
hit the 20s client timeout. Both endpoints fetch sales and up to seven days of
BIN history for ~1,300 candidates before entering the serialized scoring loop.

Install process-wide single-flight + 90s subset-aware caches around those two
loaders before importing main.py. Behaviour and data contracts are unchanged:
we only share identical/redundant reads within a short window. If a later
caller asks for cards not present in the current cache, only the missing card
ids are fetched and merged.

This bootstrap is deliberately small and removable. The long-term home for
this logic is inside the underlying loaders themselves; keeping it here avoids
a risky whole-file rewrite of the very large v2 market router during a live
performance incident.
"""
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
    # Import the BIN module first and replace its public loader before
    # market_data_provider imports that name into its own module namespace.
    from app.services import bin_market_signal

    original_bin_loader = bin_market_signal.load_bin_observations
    bin_lock = asyncio.Lock()
    bin_cache: Dict[Tuple[int, int, int], Dict[str, Any]] = {}

    async def cached_bin_loader(
        pool,
        card_ids,
        *,
        days: int = bin_market_signal.OBSERVATION_WINDOW_DAYS,
        limit_per_card: int = bin_market_signal.MAX_OBSERVATIONS_PER_CARD,
    ):
        ids = frozenset(int(card_id) for card_id in card_ids)
        if not ids:
            return {}
        key = (id(pool), int(days), int(limit_per_card))
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
                fetched = await original_bin_loader(
                    pool,
                    list(missing),
                    days=int(days),
                    limit_per_card=int(limit_per_card),
                )
                entry["data"].update(fetched)
                # Include cards with no history as covered too, otherwise a
                # negative result would be re-queried on every caller.
                entry["ids"] = entry["ids"] | missing
                entry["at"] = time.monotonic()
                log.info(
                    "shared BIN batch fetched %d missing cards in %.0fms",
                    len(missing),
                    (time.monotonic() - started) * 1000,
                )
            return _subset(entry["data"], ids)

    bin_market_signal.load_bin_observations = cached_bin_loader

    # Import only after the BIN patch above, then explicitly replace its
    # copied symbol as well so import order can never defeat the hotfix.
    from app.services import market_data_provider

    market_data_provider.load_bin_observations = cached_bin_loader

    original_sales_loader = market_data_provider.FutggMarketDataProvider.get_sales_by_ids
    sales_lock = asyncio.Lock()
    sales_cache: Dict[Tuple[int, int], Dict[str, Any]] = {}

    async def cached_sales_loader(self, card_ids: List[int], limit_per_card: int = market_data_provider.MAX_RECENT_SALES):
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

    workers = int(os.getenv("WEB_CONCURRENCY", "1"))
    if workers != 1:
        # Passing an app object to uvicorn intentionally keeps this process
        # single-worker so the process-local cache/lock is genuinely shared.
        # Multiple workers would each have their own cache and recreate the
        # exact duplicate-work problem this bootstrap fixes. Scale the API
        # horizontally only after these caches move to Redis/the database.
        log.warning("WEB_CONCURRENCY=%d requested; forcing 1 while market caches are process-local", workers)

    uvicorn.run(
        application.app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("UVICORN_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
