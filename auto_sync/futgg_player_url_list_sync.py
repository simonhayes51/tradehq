#!/usr/bin/env python
"""Import FUT.GG player cards from a pre-collected URL list.

This is the fallback for FC27 launch pulls when FUT.GG blocks the deployed
server from browsing listing pages with HTTP 403. It deliberately bypasses
listing discovery only; detail-page parsing and futgg_players upserts still
reuse futgg_player_sync.py, including the (game_year, source_card_id)
identity that keeps FC27 rows separate from FC26 rows.

Usage:
  FUTGG_CARD_URLS_FILE=/path/to/futgg-fc27-urls.txt python futgg_player_url_list_sync.py

The file can contain full FUT.GG URLs or paths, one per line. Blank lines
and # comments are ignored. Duplicate URLs are de-duplicated.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

from futgg_common import BASE_URL, CARD_URL_RE, CircuitBreaker
from futgg_player_sync import (
    BROWSER_TIMEZONE,
    CIRCUIT_BREAKER_THRESHOLD,
    DATABASE_URL,
    GAME_YEAR,
    HEADLESS,
    PLAYER_CONCURRENCY,
    REFRESH_EXISTING,
    USER_AGENT,
    FetchTarget,
    ListingCardMetadata,
    _build_targets,
    _fetch_worker,
    ensure_schema,
    existing_ids,
)
from db_connect import connect_with_retry, create_pool_with_retry
from monitoring import alert, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("futgg_player_url_list_sync")

URLS_FILE = (os.getenv("FUTGG_CARD_URLS_FILE") or "").strip()
OVERLAP_LOCK_KEY = int(os.getenv("FUTGG_PLAYER_LOCK_KEY", "7741021"))


def _normalise_card_url(raw: str) -> str | None:
    value = raw.strip()
    if not value or value.startswith("#"):
        return None
    absolute = urljoin(BASE_URL, value)
    parsed = urlparse(absolute)
    match = CARD_URL_RE.match(parsed.path)
    if not match:
        return None
    return urljoin(BASE_URL, parsed.path)


def load_url_file(path: str) -> dict[str, ListingCardMetadata]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"FUTGG_CARD_URLS_FILE does not exist: {source}")

    urls: dict[str, ListingCardMetadata] = {}
    ignored = 0
    for line in source.read_text(encoding="utf-8").splitlines():
        url = _normalise_card_url(line)
        if url:
            urls[url] = ListingCardMetadata()
        elif line.strip() and not line.lstrip().startswith("#"):
            ignored += 1

    log.info("Loaded FUT.GG URL file %s: valid=%d ignored=%d", source, len(urls), ignored)
    if not urls:
        raise RuntimeError(f"No valid FUT.GG card URLs found in {source}")
    return urls


async def run_once() -> None:
    if not URLS_FILE:
        raise RuntimeError("Set FUTGG_CARD_URLS_FILE to a newline-delimited FUT.GG card URL file")

    urls = load_url_file(URLS_FILE)
    lock_conn = await connect_with_retry(DATABASE_URL)
    got_lock = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", OVERLAP_LOCK_KEY)
    if not got_lock:
        log.info("Previous FUT.GG player sync still running; skipping")
        await lock_conn.close()
        return

    pool = await create_pool_with_retry(DATABASE_URL, min_size=1, max_size=PLAYER_CONCURRENCY + 2)
    playwright = browser = context = None
    stats = {
        "discovered": len(urls),
        "new": 0,
        "updated": 0,
        "failed": 0,
        "skipped_existing": 0,
    }
    breaker = CircuitBreaker(CIRCUIT_BREAKER_THRESHOLD)

    try:
        async with pool.acquire() as conn:
            await ensure_schema(conn)
            known = await existing_ids(conn, urls, GAME_YEAR)

        targets, skipped_existing = _build_targets(urls, known, REFRESH_EXISTING)
        stats["skipped_existing"] = skipped_existing
        log.info(
            "URL-list mode game_year=%s discovered=%d known=%d targets=%d refresh_existing=%s concurrency=%d",
            GAME_YEAR,
            len(urls),
            len(known),
            len(targets),
            REFRESH_EXISTING,
            PLAYER_CONCURRENCY,
        )

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-GB",
            timezone_id=BROWSER_TIMEZONE,
            viewport={"width": 1440, "height": 1000},
        )

        queue: "asyncio.Queue[tuple[int, FetchTarget]]" = asyncio.Queue()
        for index, target in enumerate(targets, start=1):
            queue.put_nowait((index, target))

        pages = [await context.new_page() for _ in range(PLAYER_CONCURRENCY)]
        try:
            await asyncio.gather(
                *(
                    _fetch_worker(worker_id + 1, page, pool, queue, len(targets), stats, breaker)
                    for worker_id, page in enumerate(pages)
                )
            )
        finally:
            for page in pages:
                await page.close()

        ok = (stats["failed"] == 0 or (stats["new"] + stats["updated"]) > 0) and not breaker.tripped
        async with pool.acquire() as conn:
            await heartbeat(
                conn,
                "futgg_player_url_list_sync",
                ok=ok,
                detail=" ".join(f"{key}={value}" for key, value in stats.items()),
            )
        if not ok:
            await alert(f"futgg_player_url_list_sync failed: {stats}")
        log.info("Run complete: %s", stats)
    finally:
        for close in (
            context.close if context else None,
            browser.close if browser else None,
            playwright.stop if playwright else None,
            pool.close,
        ):
            if close is None:
                continue
            try:
                await close()
            except Exception:
                log.warning("cleanup failed", exc_info=True)
        try:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", OVERLAP_LOCK_KEY)
        except Exception:
            pass
        try:
            await lock_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(run_once())
    except Exception as exc:
        log.exception("futgg_player_url_list_sync failed: %s", exc)
        sys.exit(1)
