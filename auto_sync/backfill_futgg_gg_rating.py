#!/usr/bin/env python
"""Resumable listing-only FUT.GG GG Rating backfill.

Uses the main /players/ catalogue tiles and never opens card detail pages.
Progress is committed after each page in futgg_gg_rating_backfill_state.
FUTGG_GAME_YEAR is the existing production cycle setting; --game-year is
an explicit historical override and does not alter that setting.
"""

from __future__ import annotations

from playwright_stealth import Stealth

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlparse

from playwright.async_api import async_playwright

import futgg_player_sync as sync
from db_connect import connect_with_retry

log = logging.getLogger("backfill_futgg_gg_rating")


@dataclass
class BackfillCounters:
    pages_processed: int = 0
    cards_seen: int = 0
    ratings_found: int = 0
    rows_matched: int = 0
    rows_updated: int = 0
    missing_ratings: int = 0
    invalid_ratings: int = 0
    failures: int = 0


async def _load_start_page(conn, game_year: int, override: int | None) -> int:
    if override is not None:
        return override
    value = await conn.fetchval(
        "SELECT next_page FROM futgg_gg_rating_backfill_state WHERE game_year=$1",
        game_year,
    )
    return int(value or 1)


async def _checkpoint(conn, game_year: int, page_number: int, counters: BackfillCounters) -> None:
    await conn.execute(
        """
        INSERT INTO futgg_gg_rating_backfill_state (
            game_year, next_page, pages_processed, cards_seen, ratings_found,
            rows_matched, rows_updated, missing_ratings, invalid_ratings, failures, updated_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
        ON CONFLICT (game_year) DO UPDATE SET
            next_page=EXCLUDED.next_page,
            pages_processed=futgg_gg_rating_backfill_state.pages_processed + EXCLUDED.pages_processed,
            cards_seen=futgg_gg_rating_backfill_state.cards_seen + EXCLUDED.cards_seen,
            ratings_found=futgg_gg_rating_backfill_state.ratings_found + EXCLUDED.ratings_found,
            rows_matched=futgg_gg_rating_backfill_state.rows_matched + EXCLUDED.rows_matched,
            rows_updated=futgg_gg_rating_backfill_state.rows_updated + EXCLUDED.rows_updated,
            missing_ratings=futgg_gg_rating_backfill_state.missing_ratings + EXCLUDED.missing_ratings,
            invalid_ratings=futgg_gg_rating_backfill_state.invalid_ratings + EXCLUDED.invalid_ratings,
            failures=futgg_gg_rating_backfill_state.failures + EXCLUDED.failures,
            completed_at=NULL,
            updated_at=NOW()
        """,
        game_year, page_number + 1, counters.pages_processed, counters.cards_seen,
        counters.ratings_found, counters.rows_matched, counters.rows_updated,
        counters.missing_ratings, counters.invalid_ratings, counters.failures,
    )


async def run_backfill(*, game_year: int, start_page: int | None, max_pages: int) -> BackfillCounters:
    conn = await connect_with_retry(sync.DATABASE_URL)
    got_lock = await conn.fetchval("SELECT pg_try_advisory_lock($1)", sync.OVERLAP_LOCK_KEY)
    if not got_lock:
        await conn.close()
        raise RuntimeError("FUT.GG player sync/backfill is already running")

    playwright = browser = context = None
    total = BackfillCounters()
    started = monotonic()
    try:
        await sync.ensure_schema(conn)
        actual_start = await _load_start_page(conn, game_year, start_page)
        log.info(
            "Starting listing-only GG Rating backfill game_year=%d start_page=%d max_pages=%d",
            game_year, actual_start, max_pages,
        )

        async with Stealth().use_async(async_playwright()) as patched_playwright:
            playwright = patched_playwright
            browser = await playwright.chromium.launch(
                headless=sync.HEADLESS,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-GB,en;q=0.9',
                    'Sec-Ch-Ua': '"Chromium";v="128", "Google Chrome";v="128", "Not;A=Brand";v="99"',
                    'Sec-Ch-Ua-Mobile': '?0',
                    'Sec-Ch-Ua-Platform': '"Windows"',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Upgrade-Insecure-Requests': '1',
                },
                locale='en-GB',
            )
            page = await context.new_page()

            async def process_page(page_number: int, cards: dict[str, sync.ListingCardMetadata]) -> None:
                page_cards = {
                    url: metadata
                    for url, metadata in cards.items()
                    if (match := sync.CARD_URL_RE.match(urlparse(url).path))
                    and int(match.group("game_year")) == game_year
                }
                ratings_found = sum(metadata.gg_rating is not None for metadata in page_cards.values())
                if page_cards and ratings_found == 0:
                    raise RuntimeError(
                        f"FUT.GG page {page_number} rendered {len(page_cards)} cards but no GG Ratings; "
                        "refusing to advance the durable checkpoint"
                    )
                page_counts = BackfillCounters(
                    pages_processed=1,
                    cards_seen=len(page_cards),
                    ratings_found=ratings_found,
                    missing_ratings=sum(
                        metadata.gg_rating is None and not metadata.gg_rating_invalid
                        for metadata in page_cards.values()
                    ),
                    invalid_ratings=sum(metadata.gg_rating_invalid for metadata in page_cards.values()),
                )
                result = await sync.update_listing_gg_ratings(
                    conn, page_cards, game_year, only_missing=True,
                )
                page_counts.rows_matched = result.matched
                page_counts.rows_updated = result.updated
                await _checkpoint(conn, game_year, page_number, page_counts)
                for field in page_counts.__dataclass_fields__:
                    setattr(total, field, getattr(total, field) + getattr(page_counts, field))
                log.info(
                    "page=%d seen=%d ratings=%d matched=%d updated=%d missing=%d invalid=%d",
                    page_number, page_counts.cards_seen, page_counts.ratings_found,
                    page_counts.rows_matched, page_counts.rows_updated, page_counts.missing_ratings,
                    page_counts.invalid_ratings,
                )

            await sync._paginate_listing(
                page,
                "/players/?page={page}",
                max_pages,
                f"gg-rating-backfill-fc{game_year}",
                start_page=actual_start,
                page_callback=process_page,
            )
            if total.pages_processed < max_pages:
                await conn.execute(
                    "UPDATE futgg_gg_rating_backfill_state "
                    "SET completed_at=NOW(), updated_at=NOW() WHERE game_year=$1",
                    game_year,
                )
            log.info("Backfill run complete runtime=%.1fs counters=%s", monotonic() - started, total)
            return total
    except Exception:
        total.failures += 1
        try:
            await conn.execute(
                "UPDATE futgg_gg_rating_backfill_state "
                "SET failures=failures+1, updated_at=NOW() WHERE game_year=$1",
                game_year,
            )
        except Exception:
            log.warning("Could not persist backfill failure counter", exc_info=True)
        raise
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", sync.OVERLAP_LOCK_KEY)
        except Exception:
            # Advisory locks are session-scoped. A closed connection has
            # already released it, and cleanup must not turn a successful
            # multi-hour backfill into a failed process exit.
            log.warning("Advisory unlock failed during cleanup", exc_info=True)
        try:
            await conn.close()
        except Exception:
            log.warning("Database connection cleanup failed", exc_info=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=int(os.getenv("FUTGG_GAME_YEAR", "27")))
    parser.add_argument("--start-page", type=int, default=None, help="Override the durable DB checkpoint")
    parser.add_argument("--max-pages", type=int, default=sync.MAX_PAGES_FULL)
    args = parser.parse_args()
    if args.game_year < 1 or args.start_page is not None and args.start_page < 1 or args.max_pages < 1:
        parser.error("game year, start page and max pages must be positive")
    return args


if __name__ == "__main__":
    try:
        asyncio.run(run_backfill(**vars(_parse_args())))
    except Exception as exc:
        log.exception("GG Rating backfill failed: %s", exc)
        sys.exit(1)

