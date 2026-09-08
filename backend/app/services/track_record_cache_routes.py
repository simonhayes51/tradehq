"""Cached Track Record route for production.

The customer-facing Track Record is historical evidence, not a live market
quote. Replaying every sent Proof Ledger day on every page view is therefore
both expensive and unnecessary. A 90-day request currently fans out into one
verified outcome query per sent day, and production measured the endpoint at
~10.8s while the rest of Home was already below 3s.

This transition layer keeps the public route contract unchanged while adding:
  * 10-minute process-local caching per effective window,
  * single-flight cold refreshes,
  * stale-while-revalidate so an expired cache never makes a user wait,
  * best-effort startup prewarm for the common 30d and 90d windows.

The underlying proof/validation logic is untouched. We cache its completed
answer, not a looser approximation of it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Tuple

from fastapi.routing import APIRoute

log = logging.getLogger("track_record_cache")

CACHE_SECONDS = float(os.getenv("TRACK_RECORD_CACHE_SECONDS", "600"))
STALE_SECONDS = float(os.getenv("TRACK_RECORD_STALE_SECONDS", "3600"))
PREWARM_DELAY_SECONDS = float(os.getenv("TRACK_RECORD_PREWARM_DELAY_SECONDS", "8"))

_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
_locks: Dict[Tuple[str, int], asyncio.Lock] = {}
_refresh_tasks: Dict[Tuple[str, int], asyncio.Task] = {}


def _lock_for(key: Tuple[str, int]) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def _compute(resolved_window: str, window_days: int) -> Dict[str, Any]:
    """Run the existing, audited Track Record calculations once."""
    from app.db import get_core_pool
    from app.routers.v2 import recommendations as rec
    from app.services.discord_daily_proof import (
        _fetch_published_eligible_rows,
        published_recent_wins_and_losses,
        published_track_record,
    )

    started = time.monotonic()
    try:
        core_pool = await get_core_pool()
        eligible_rows = await _fetch_published_eligible_rows(core_pool, window_days)
        published = await published_track_record(
            core_pool,
            window_days=window_days,
            eligible_rows=eligible_rows,
        )
        if published.get("has_published_data"):
            recent = await published_recent_wins_and_losses(
                core_pool,
                window_days=window_days,
                limit=5,
                eligible_rows=eligible_rows,
            )
            published["recent_wins"] = recent["recent_wins"]
            published["recent_losses"] = recent["recent_losses"]
    except Exception:
        log.warning("cached published track-record computation failed", exc_info=True)
        published = {
            "has_published_data": False,
            "tracking_since": None,
            "window_days": window_days,
        }

    model_validation = await rec._model_validation(resolved_window, window_days)
    elapsed = time.monotonic() - started
    log.info(
        "track-record cache computed window=%s days=%d in %.2fs",
        resolved_window,
        window_days,
        elapsed,
    )
    return {
        "published": published,
        "model_validation": model_validation,
    }


async def _refresh(key: Tuple[str, int]) -> Dict[str, Any]:
    resolved_window, window_days = key
    async with _lock_for(key):
        now = time.monotonic()
        existing = _cache.get(key)
        if existing is not None and now - existing["at"] < CACHE_SECONDS:
            return existing["data"]

        data = await _compute(resolved_window, window_days)
        _cache[key] = {"at": time.monotonic(), "data": data}
        return data


async def _refresh_background(key: Tuple[str, int]) -> None:
    try:
        await _refresh(key)
    except Exception:
        log.warning("background track-record refresh failed for %s", key, exc_info=True)
    finally:
        _refresh_tasks.pop(key, None)


async def get_cached_track_record(resolved_window: str, window_days: int) -> Dict[str, Any]:
    """Fresh cache hit, stale-while-revalidate hit, or one cold compute."""
    key = (resolved_window, int(window_days))
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None:
        age = now - entry["at"]
        if age < CACHE_SECONDS:
            return entry["data"]
        if age < STALE_SECONDS:
            task = _refresh_tasks.get(key)
            if task is None or task.done():
                _refresh_tasks[key] = asyncio.create_task(_refresh_background(key))
            return entry["data"]

    return await _refresh(key)


def install_track_record_cache_route(application) -> None:
    """Replace only /api/v2/track-record's final callable.

    FastAPI's already-built query parsing and Viewer dependency stay intact,
    exactly like snapshot_surface_routes.py's market-list transition.
    """
    from app.auth.entitlements import FREE_TRACK_RECORD_WINDOW_DAYS
    from app.routers.v2 import recommendations as rec

    async def cached_track_record(**kw):
        window = kw.get("window", "90d")
        viewer = kw["viewer"]
        window_days = rec._TRACK_RECORD_WINDOW_DAYS.get(window, 90)
        resolved_window = window if window in rec._TRACK_RECORD_WINDOW_DAYS else "90d"

        capped_window = False
        if not viewer.has("full_track_record"):
            if window_days > FREE_TRACK_RECORD_WINDOW_DAYS:
                window_days = FREE_TRACK_RECORD_WINDOW_DAYS
                capped_window = True

        data = await get_cached_track_record(resolved_window, window_days)
        model_validation = data["model_validation"]
        return {
            "window": resolved_window,
            "window_days": window_days,
            "window_capped": capped_window,
            "full_history_available": viewer.has("full_track_record"),
            "published": data["published"],
            "model_validation": model_validation,
            "strategies": model_validation.get("strategies", []),
        }

    installed = False
    for route in application.app.routes:
        if isinstance(route, APIRoute) and route.path == "/api/v2/track-record":
            route.endpoint = cached_track_record
            route.dependant.call = cached_track_record
            installed = True
            break

    if installed:
        log.info(
            "track-record cached route installed (ttl=%.0fs stale=%.0fs)",
            CACHE_SECONDS,
            STALE_SECONDS,
        )
    else:
        log.warning("track-record cache installer did not find /api/v2/track-record")


def install_track_record_prewarm(application) -> None:
    from app.services.managed_startup_tasks import register_startup_job

    async def track_record_prewarm_job() -> None:
        await asyncio.sleep(PREWARM_DELAY_SECONDS)
        # Home defaults to 90d for Elite; Free is capped to 30d. Warm both
        # sequentially so startup does not fan out two proof-ledger replays
        # against Postgres at once.
        for key in (("90d", 90), ("30d", 30)):
            try:
                await _refresh(key)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("track-record prewarm failed for %s", key, exc_info=True)

    register_startup_job("track_record_prewarm", track_record_prewarm_job)
