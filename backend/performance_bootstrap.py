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
    from app.services.futgg_sales_window import filter_valuation_sales

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
                # The SQL loader still returns its historical 14-day window,
                # while migrations 084/085 define the market snapshot over the
                # newest 50 sales in seven days. Filter centrally here so every
                # fresh-evaluation caller uses the same evidence horizon as the
                # snapshot aggregates.
                fetched = {
                    int(card_id): filter_valuation_sales(rows)
                    for card_id, rows in fetched.items()
                }
                entry["data"].update(fetched)
                entry["ids"] = entry["ids"] | missing
                entry["at"] = time.monotonic()
                log.info(
                    "shared 7d sales batch fetched %d missing cards in %.0fms",
                    len(missing),
                    (time.monotonic() - started) * 1000,
                )
            return _subset(entry["data"], ids)

    market_data_provider.FutggMarketDataProvider.get_sales_by_ids = cached_sales_loader
    log.info("installed market-history single-flight caches (ttl=%.0fs)", CACHE_SECONDS)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    install_market_batch_singleflight()

    # Install the temporary FC27 preseason entitlement overlay BEFORE main.py
    # imports the route tree. This keeps every direct import of the canonical
    # resolver consistent, including Discord reconciliation.
    from app.services.preseason_elite_access import install_preseason_elite_access
    install_preseason_elite_access()

    # Import the app only after the shared loaders and entitlement overlay have
    # been wrapped.
    import main as application

    # Replace the legacy aggregate Free Discord overview with one canonical
    # Trade of the Day and add the no-card/no-Stripe Free setup path. Paid
    # Server Feed selection and billing remain on their existing code paths.
    from app.services.discord_free_server_feed import install_free_server_feed
    install_free_server_feed()

    # While preseason Elite is free, prevent a new personal Stripe checkout
    # from charging somebody for access they already have. Existing paid
    # subscriptions and the separate Discord Server Feed billing are untouched.
    from app.services.preseason_elite_access import install_preseason_checkout_guard
    install_preseason_checkout_guard(application.app)

    # Heavy market-list endpoints consume scanner-persisted current
    # intelligence rather than re-running evaluate_card() across the market
    # inside HTTP requests.
    from app.services.snapshot_surface_routes import install_snapshot_surface_routes
    install_snapshot_surface_routes(application)

    # The one-card detail surface is genuinely a fresh evaluation. Route it
    # through the same canonical evidence path as Companion and held positions
    # without sending bulk surfaces back through that path.
    from app.services.fresh_card_routes import install_fresh_player_detail_route
    install_fresh_player_detail_route(application)

    # Home needs the full signal universe, including AVOID/regime. It is
    # persisted-only now and never falls back to a market-wide live CPU pass.
    from app.services.dashboard_market_cache import install_dashboard_market_cache
    install_dashboard_market_cache(application)

    # Some background consumers, notably Discord's live publisher, still lazily
    # import futgg_market's old bulk helper names. Redirect those names to
    # scanner-persisted current intelligence. Dashboard imported its own copies
    # earlier and has already been patched above; futgg_scanner uses its own
    # evaluation loop and is not touched by this installer.
    from app.services.persisted_market_helpers import install_persisted_market_helpers
    install_persisted_market_helpers()

    # Keep historical Proof Ledger calculations intact, but make the Free
    # Discord overview's "open opportunities" count use exact-BIN current
    # intelligence rather than lingering immutable recommendation snapshots.
    from app.services.discord_current_state_overrides import install_discord_current_state_overrides
    install_discord_current_state_overrides()

    # Add the scanner/current-intelligence state to the existing readiness
    # report. It is a degraded operational check, not a load-balancer-fatal
    # dependency, so stale intelligence is visible without taking the API down.
    from app.services.intelligence_readiness import install_intelligence_readiness
    install_intelligence_readiness()

    # Track Record is historical evidence. Cache the audited result and warm
    # the common windows after startup so a page view never has to replay tens
    # of sent Proof Ledger days synchronously.
    from app.services.track_record_cache_routes import (
        install_track_record_cache_route,
        install_track_record_prewarm,
    )
    install_track_record_cache_route(application)
    install_track_record_prewarm(application)

    # The Investment runner consumes frozen cohort memberships but historically
    # never populated live FC27 memberships itself. Register a managed bootstrap
    # job that classifies priced cards for the configured game year using the
    # existing immutable cohort classifier. It is inert when Investments are
    # disabled and never changes publication policy.
    from app.services.investment_cohort_bootstrap import install_investment_cohort_bootstrap
    install_investment_cohort_bootstrap(application)

    # Dashboard + Track Record + Investment cohort bootstrap registered their
    # managed jobs above. Wrap the app's existing lifespan once so those jobs
    # start after main.py has created its pools/app.state and are cancelled
    # cleanly before shutdown.
    from app.services.managed_startup_tasks import install_managed_startup_jobs
    install_managed_startup_jobs(application)

    # Do NOT prewarm sales/BIN history for the whole segmented market here.
    # Bulk HTTP and Discord consumers are persisted-state readers now, so loading
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
