#!/usr/bin/env python
"""Daily FUT.GG player catalogue sync.

Discovers cards via real pagination (?page=N - see _paginate_listing()/
collect_listing_urls()), visits only cards that are new (unless
FUTGG_REFRESH_EXISTING=true), and upserts metadata into the independent
futgg_players table. It intentionally does not write prices or sales;
futgg_price_sync.py owns those.

Recommended Railway Cron: once daily, e.g. 15 5 * * *

Two discovery modes:
  - Daily (default): a single partition, FUT.GG's newest-first feed
    (/players/new/), capped at FUTGG_LISTING_MAX_PAGES pages - enough to
    catch what's new since yesterday without re-walking the whole
    catalogue every run.
  - Full scan (FUTGG_LISTING_FULL_SCAN=true): one or more partitions (see
    FUTGG_FULL_SCAN_PARTITIONS below), each capped at
    FUTGG_LISTING_MAX_PAGES_FULL pages, for an occasional full-catalogue
    reconciliation.

    IMPORTANT: /players/new/ is a RECENCY WINDOW, not the whole catalogue.
    Confirmed live 2026-08: a full-scan run walked every page of
    /players/new/ down to 4 consecutive zero-new pages and stopped at
    9,970 unique cards, while futgg_players already held 10,838 rows -
    including verified-live FC26 base cards (e.g. Haaland card 239085,
    Mbappe card 231747) that simply aged out of the "new" feed as the
    catalogue grew past its window. Full-scan mode therefore also walks
    FUT.GG's main catalogue browse (/players/, unfiltered) as a second
    partition by default, using the identical pagination engine. Which
    partitions run is controlled by FUTGG_FULL_SCAN_PARTITIONS so this can
    be re-pointed at a different verified-complete FUT.GG source (a
    rarity/category filter, a profile-version listing, etc.) without a
    code change - see that variable's description and
    collect_listing_urls()'s docstring for why no such filter is
    hardcoded here.

Either mode also stops early, per partition, after FUTGG_LISTING_IDLE_ROUNDS
consecutive pages with zero new cards - the real signal that pagination has
run past the end of that partition's catalogue, so a full scan doesn't have
to walk its whole page cap every time and a daily run doesn't overrun a
slow news day.

A full-scan run also checks its combined result against futgg_players'
current row count (FUTGG_FULL_SCAN_MIN_COVERAGE) before it can report
success - see _full_scan_is_plausible(). It will still upsert whatever
legitimate new cards it found even if that check fails (no reason to
discard real discoveries), but the run is flagged as failed rather than
silently reported as a complete reconciliation.

Important environment variables:
  DATABASE_URL                    required
  PLAYWRIGHT_HEADLESS             true by default
  PLAYWRIGHT_TIMEOUT_MS           45000
  FUTGG_PLAYER_LIMIT              0 = no card limit
  FUTGG_LISTING_FULL_SCAN         false
  FUTGG_LISTING_MAX_PAGES         5 (daily mode)
  FUTGG_LISTING_MAX_PAGES_FULL    400 (full-scan mode, per partition)
  FUTGG_FULL_SCAN_PARTITIONS      "new:/players/new/?page={page},catalogue:/players/?page={page}"
                                   comma-separated name:template pairs, each
                                   template containing a literal '{page}'
                                   placeholder - the full-scan sources to
                                   walk and merge/de-duplicate
  FUTGG_FULL_SCAN_MIN_COVERAGE    0.9 (full-scan must discover >= this
                                   fraction of futgg_players' current row
                                   count to be treated as a plausible
                                   complete catalogue; 0 disables the check)
  FUTGG_LISTING_IDLE_ROUNDS       4 (consecutive pages w/ 0 new cards before stopping, per partition)
  FUTGG_LISTING_PAGE_DELAY        0.5 seconds, between listing-page navigations
  FUTGG_LISTING_STABILIZE_ATTEMPTS 6 (retries waiting for a page's cards to differ from the previous page's)
  FUTGG_LISTING_STABILIZE_POLL_MS 400 (wait between stabilize retries)
  FUTGG_REFRESH_EXISTING          false
  FUTGG_PLAYER_REQUEST_DELAY      0.35 seconds, per fetch worker
  FUTGG_PLAYER_CONCURRENCY        3 (parallel card-detail fetch workers - see _fetch_worker())
  FUTGG_CIRCUIT_BREAKER_THRESHOLD 5 (consecutive 403/429/failed navigations before this run stops)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from dataclasses import asdict
from typing import Any
from urllib.parse import urljoin, urlparse

import asyncpg

from db_connect import connect_with_retry, create_pool_with_retry
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from futgg_common import (
    BASE_URL, CARD_URL_RE, CircuitBreaker, FutggCard, upsize_card_image,
    classify_price_tier, parse_futgg_card,
)
from monitoring import alert, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("futgg_player_sync")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").strip().lower() in {"1", "true", "yes", "on"}
# Pinned browser timezone - see the new_context() call for why this
# matters to released_at parsing. Overridable only if FUT.GG is ever
# shown to render "Added On" in a different fixed zone, in which case
# futgg_common.ADDED_ON_ASSUMED_TIMEZONE must be changed to match.
BROWSER_TIMEZONE = os.getenv("FUTGG_BROWSER_TIMEZONE", "UTC").strip() or "UTC"
# Targeted metadata backfill: revisit cards that predate the
# released_at/display_name/full_name fields instead of forcing every
# normal daily run to re-scrape the whole historical catalogue forever
# (which is what FUTGG_REFRESH_EXISTING=true would do).
BACKFILL_RELEASE_METADATA = os.getenv("FUTGG_BACKFILL_RELEASE_METADATA", "false").strip().lower() in {"1", "true", "yes", "on"}
BACKFILL_LIMIT = int(os.getenv("FUTGG_BACKFILL_LIMIT", "500"))
BACKFILL_GAME_YEAR = os.getenv("FUTGG_BACKFILL_GAME_YEAR", "").strip()
TIMEOUT_MS = max(5000, int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "45000")))
PLAYER_LIMIT = max(0, int(os.getenv("FUTGG_PLAYER_LIMIT", "0")))

# Pagination (replaces scroll-based discovery entirely - see
# collect_listing_urls()). Two page caps, selected by FULL_SCAN: a small
# one for the normal daily run (new cards appear at the front of the
# listing, so a handful of pages is enough) and a large one, applied per
# partition, for an occasional full-catalogue sync.
FULL_SCAN = os.getenv("FUTGG_LISTING_FULL_SCAN", "false").strip().lower() in {"1", "true", "yes", "on"}
MAX_PAGES_DAILY = max(1, int(os.getenv("FUTGG_LISTING_MAX_PAGES", "5")))
MAX_PAGES_FULL = max(1, int(os.getenv("FUTGG_LISTING_MAX_PAGES_FULL", "400")))
MAX_PAGES = MAX_PAGES_FULL if FULL_SCAN else MAX_PAGES_DAILY

# Full-scan discovery sources. /players/new/ is a recency-windowed feed,
# not the whole catalogue (see module docstring) - full-scan mode walks
# each of these partitions and merges/de-duplicates their results. Format:
# comma-separated "name:template" pairs, each template containing a
# literal "{page}" placeholder, resolved against BASE_URL. Configurable
# (rather than hardcoding a rarity/category filter) because this worker
# has no live network access to FUT.GG from this dev/CI environment to
# confirm any such filter's exact query parameter by inspection - an
# operator with live access can repoint this at a verified-complete source
# without a code change.
DEFAULT_FULL_SCAN_PARTITIONS = "new:/players/new/?page={page},catalogue:/players/?page={page}"


def _parse_full_scan_partitions(raw: str) -> list[tuple[str, str]]:
    partitions: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(
                f"Invalid FUTGG_FULL_SCAN_PARTITIONS entry (expected name:template): {chunk!r}"
            )
        name, template = chunk.split(":", 1)
        name, template = name.strip(), template.strip()
        if "{page}" not in template:
            raise ValueError(
                f"FUTGG_FULL_SCAN_PARTITIONS entry {chunk!r} is missing the '{{page}}' placeholder"
            )
        partitions.append((name, template))
    if not partitions:
        raise ValueError("FUTGG_FULL_SCAN_PARTITIONS resolved to zero partitions")
    return partitions


FULL_SCAN_PARTITIONS = _parse_full_scan_partitions(
    os.getenv("FUTGG_FULL_SCAN_PARTITIONS", DEFAULT_FULL_SCAN_PARTITIONS)
)
# A genuinely complete full-scan shouldn't discover meaningfully fewer
# cards than are already confirmed in futgg_players - existing rows don't
# vanish from FUT.GG. See _full_scan_is_plausible().
FULL_SCAN_MIN_COVERAGE = max(0.0, min(1.0, float(os.getenv("FUTGG_FULL_SCAN_MIN_COVERAGE", "0.9"))))
# Same name/default as before scrolling was removed - now counts
# consecutive PAGES with zero new cards instead of scroll rounds, same
# early-stop role either way.
IDLE_ROUNDS = max(1, int(os.getenv("FUTGG_LISTING_IDLE_ROUNDS", "4")))
PAGE_DELAY = max(0.0, float(os.getenv("FUTGG_LISTING_PAGE_DELAY", "0.5")))

# FUT.GG's listing is client-rendered - the previous page's cards can
# still be attached for a moment after a query-param-only navigation
# while new content is still being fetched (confirmed live: a full-scan
# run oscillated 30-new/0-new across pages instead of accumulating
# steadily). collect_listing_urls() polls up to this many times, waiting
# this long between attempts, until the visible cards differ from the
# previous page's - see its own docstring.
LISTING_STABILIZE_MAX_ATTEMPTS = max(1, int(os.getenv("FUTGG_LISTING_STABILIZE_ATTEMPTS", "6")))
LISTING_STABILIZE_POLL_MS = max(50, int(os.getenv("FUTGG_LISTING_STABILIZE_POLL_MS", "400")))

REFRESH_EXISTING = os.getenv("FUTGG_REFRESH_EXISTING", "false").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_DELAY = max(0.0, float(os.getenv("FUTGG_PLAYER_REQUEST_DELAY", "0.35")))
OVERLAP_LOCK_KEY = int(os.getenv("FUTGG_PLAYER_LOCK_KEY", "7741021"))

# Card-detail fetching (not the listing/pagination phase, which stays on
# its own single page - it's inherently sequential/stateful and capped
# at a few hundred pages regardless) was fully sequential: one page, one
# card at a time. For a 10,000+ card full-catalogue backfill that's
# multiple hours. This runs FUTGG_PLAYER_CONCURRENCY pages in parallel
# (same page-pool/queue pattern as futgg_price_sync.py's worker_loop),
# each still respecting FUTGG_PLAYER_REQUEST_DELAY between its own
# requests - so total throughput scales with concurrency while
# per-connection request rate to FUT.GG stays exactly as polite as
# before. Kept conservative by default; raise only after confirming no
# increase in 403/429s.
PLAYER_CONCURRENCY = max(1, int(os.getenv("FUTGG_PLAYER_CONCURRENCY", "3")))
CIRCUIT_BREAKER_THRESHOLD = max(1, int(os.getenv("FUTGG_CIRCUIT_BREAKER_THRESHOLD", "5")))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS futgg_players (
            source_card_id BIGINT PRIMARY KEY,
            source_player_id BIGINT NOT NULL,
            source_slug TEXT NOT NULL,
            source_url TEXT NOT NULL,
            game_year SMALLINT NOT NULL,

            name TEXT,
            rating SMALLINT,
            primary_position TEXT,
            alternate_positions TEXT[] NOT NULL DEFAULT '{}',
            rarity TEXT,
            squad TEXT,
            price_tier TEXT NOT NULL DEFAULT 'bronze',

            club TEXT,
            league TEXT,
            nation TEXT,
            club_source_id INTEGER,
            league_source_id INTEGER,
            nation_source_id INTEGER,

            height_cm SMALLINT,
            weight_kg SMALLINT,
            foot TEXT,
            skill_moves SMALLINT,
            weak_foot SMALLINT,
            accelerate_type TEXT,
            body_type TEXT,
            real_face BOOLEAN,
            shirt_number SMALLINT,
            age SMALLINT,

            pace SMALLINT,
            shooting SMALLINT,
            passing SMALLINT,
            dribbling SMALLINT,
            defending SMALLINT,
            physicality SMALLINT,
            diving SMALLINT,
            handling SMALLINT,
            kicking SMALLINT,
            reflexes SMALLINT,
            speed SMALLINT,
            positioning SMALLINT,

            player_image_url TEXT,
            card_design_image_url TEXT,
            club_image_url TEXT,
            league_image_url TEXT,
            nation_image_url TEXT,

            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            price_updated_at TIMESTAMPTZ,
            next_price_due_at TIMESTAMPTZ,
            last_price_status TEXT,
            metadata_warnings TEXT[] NOT NULL DEFAULT '{}'
        )
        """
    )
    # is_tradeable starts NULL ("unknown, never priced") - futgg_price_sync.py
    # sets it TRUE/FALSE once a real price observation confirms which. NULL
    # cards are still eligible for price selection (see fetch_due_cards);
    # only a confirmed FALSE (SBC/objective/untradeable) is excluded, so
    # metadata sync never has to guess tradeability itself.
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS is_tradeable BOOLEAN"
    )
    # display_name / full_name / released_at: see FutggCard's own comments
    # for the naming rationale. Defensive ADD COLUMN IF NOT EXISTS here
    # follows this worker's existing convention (it owns its own tables and
    # must not crash on a database that has not had the migration applied
    # yet) - but backend/migrations/063 is the real application migration,
    # and is what the backend's own reads depend on.
    for column, ddl_type in (
        ("display_name", "TEXT"),
        ("full_name", "TEXT"),
        ("released_at", "TIMESTAMPTZ"),
        ("released_at_raw", "TEXT"),
    ):
        await conn.execute(
            f"ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS {column} {ddl_type}"
        )
    # card_image_url: the single pre-composited full-card image FUT.GG
    # itself renders (background+cutout+badges baked in) - see
    # futgg_common.py's "/player-item-card/" branch. The
    # card_image_stored_* columns track backend's own re-hosted copy of
    # that image in R2 (backend/app/services/futgg_card_image_mirror.py) -
    # this worker only ever writes card_image_url; it never uploads
    # anything or writes the stored_* columns itself, same ownership split
    # as generated_card_* on fut_players (backend owns the storage
    # lifecycle, the scraper only owns "here's the source URL").
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS card_image_url TEXT"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS card_image_stored_url TEXT"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS card_image_stored_key TEXT"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS card_image_stored_hash VARCHAR(64)"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS card_image_stored_at TIMESTAMPTZ"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS card_image_stored_status TEXT"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS card_image_stored_error TEXT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_card_image_pending_idx "
        "ON futgg_players (card_image_stored_status) "
        "WHERE card_image_url IS NOT NULL AND card_image_stored_status IS DISTINCT FROM 'ready'"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_price_due_idx "
        "ON futgg_players (next_price_due_at, price_tier) "
        "WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_player_idx ON futgg_players (source_player_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_name_idx ON futgg_players (LOWER(name))"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_rarity_idx ON futgg_players (rarity)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_rating_idx ON futgg_players (rating)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_active_idx ON futgg_players (is_active)"
    )


async def dismiss_cookie_banner(page) -> None:
    for selector in (
        "#onetrust-accept-btn-handler",
        "#onetrust-reject-all-handler",
        "button:has-text('Accept all')",
        "button:has-text('Reject all')",
    ):
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=500):
                await button.click(timeout=2000)
                return
        except Exception:
            pass


async def _read_card_hrefs(page) -> dict[str, str | None]:
    """The canonical card URLs currently visible on the page, mapped to
    each card's composited full-card image src if present (the single
    background+cutout+badges render FUT.GG itself composites - see
    futgg_common.py's "player-item-card/" branch). This listing grid is
    the ONLY place that image can be captured from: a live production
    backfill reached 100% coverage on the per-card detail page's own
    cutout/background fields while the composited image stayed at 0%
    coverage, confirming FUT.GG does not render it on the individual
    card page at all, only here."""
    pairs = await page.locator("a[href]").evaluate_all(
        """
        nodes => nodes.map(node => ({
            href: node.getAttribute('href'),
            image: node.querySelector('img[src]')?.src || null,
        }))
        """
    )
    matched: dict[str, str | None] = {}
    for pair in pairs:
        href = pair.get("href")
        if not href:
            continue
        absolute = urljoin(BASE_URL, href)
        parsed = urlparse(absolute)
        if not CARD_URL_RE.match(parsed.path):
            continue
        canonical = urljoin(BASE_URL, parsed.path)
        image = pair.get("image")
        if image and "player-item-card/" in image:
            # This listing grid serves a 300px thumbnail (see
            # _upsize_card_image's own docstring) - bump it to a real
            # source resolution before it's ever stored/mirrored, same as
            # futgg_common.py's own (now-dead-in-practice, since this
            # image never actually appears on the per-card detail page)
            # capture branch intended.
            image = upsize_card_image(image)
        else:
            image = None
        if canonical not in matched or (matched[canonical] is None and image):
            matched[canonical] = image
    return matched


async def _paginate_listing(
    page, url_template: str, max_pages: int, partition_label: str
) -> dict[str, str | None]:
    """Paginates a single FUT.GG listing partition (?page=1, ?page=2, ...
    against url_template, a path containing a literal '{page}'
    placeholder) - no window.scrollTo() or scroll-settle waits anywhere.
    Each page is a direct navigation; max_pages caps how many pages are
    visited, and IDLE_ROUNDS consecutive pages with zero new cards stops
    this partition early regardless of the cap, since that's the real
    signal pagination has run past the end of it. This is the shared
    engine behind both daily discovery (a single partition) and full-scan
    discovery (one or more partitions, merged - see collect_listing_urls());
    partition_label only affects logging.

    Returns card URL -> composited card image URL (or None if that
    card's tile had none) - see _read_card_hrefs' docstring for why this
    listing crawl is the only place that image can be captured."""
    seen: dict[str, str | None] = {}
    idle_pages = 0
    previous_page_hrefs: dict[str, str | None] = {}

    for page_number in range(1, max_pages + 1):
        listing_url = f"{BASE_URL}{url_template.format(page=page_number)}"
        response = await page.goto(listing_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        status = response.status if response else 0
        if status != 200:
            if page_number == 1:
                raise RuntimeError(f"FUT.GG listing partition={partition_label} returned HTTP {status}")
            log.warning(
                "FUT.GG listing partition=%s page %d returned HTTP %d - stopping pagination",
                partition_label, page_number, status,
            )
            break

        await dismiss_cookie_banner(page)
        try:
            await page.locator("a[href*='/players/']").first.wait_for(state="attached", timeout=TIMEOUT_MS)
        except PlaywrightTimeoutError as exc:
            if page_number == 1:
                raise RuntimeError(f"No card links appeared on FUT.GG listing partition={partition_label}") from exc
            log.info(
                "No card links on listing partition=%s page %d - treating as end of catalogue",
                partition_label, page_number,
            )
            break

        # FUT.GG's listing is client-rendered - a query-param-only
        # navigation can leave the PREVIOUS page's cards still attached
        # for a moment while the new content is still being fetched, so
        # the "attached" wait above only confirms *some* /players/ link
        # exists, not that it's THIS page's content. Confirmed live: a
        # full-scan run oscillated between 30-new and 0-new pages instead
        # of steadily accumulating, because some pages were read before
        # their content had actually swapped in. Poll until the visible
        # card hrefs differ from the previous page's, or give up after a
        # bounded number of attempts - a genuine end-of-catalogue page
        # that keeps re-serving the same content still correctly settles
        # into "0 new" once attempts are exhausted; this only adds
        # patience for the transient render-timing case.
        current_hrefs = await _read_card_hrefs(page)
        for _ in range(LISTING_STABILIZE_MAX_ATTEMPTS - 1):
            # Retry while the read looks untrustworthy: EMPTY (the client
            # render hasn't populated the card grid yet - true on page 1
            # too, not just later pages with something to compare
            # against - the earlier "a[href*='/players/']" wait above can
            # resolve instantly against the site's static nav link before
            # the real listing has rendered) or IDENTICAL to the previous
            # page's cards (stale content still attached). Only a
            # non-empty read that actually differs from the previous page
            # is trusted without retrying.
            if current_hrefs and current_hrefs != previous_page_hrefs:
                break
            await page.wait_for_timeout(LISTING_STABILIZE_POLL_MS)
            current_hrefs = await _read_card_hrefs(page)

        before = len(seen)
        for card_url, image_url in current_hrefs.items():
            if card_url not in seen or (seen[card_url] is None and image_url):
                seen[card_url] = image_url
        new_this_page = len(seen) - before
        previous_page_hrefs = current_hrefs

        log.info(
            "Listing partition=%s page %d: %d new cards, %d unique total",
            partition_label, page_number, new_this_page, len(seen),
        )

        if PLAYER_LIMIT and len(seen) >= PLAYER_LIMIT:
            break

        if new_this_page == 0:
            idle_pages += 1
            if idle_pages >= IDLE_ROUNDS:
                log.info(
                    "partition=%s: %d consecutive pages with no new cards - stopping pagination",
                    partition_label, idle_pages,
                )
                break
        else:
            idle_pages = 0

        if page_number < max_pages and PAGE_DELAY:
            await asyncio.sleep(random.uniform(PAGE_DELAY * 0.7, PAGE_DELAY * 1.3))

    return seen


async def collect_listing_urls(page) -> dict[str, str | None]:
    """Top-level discovery entry point - see module docstring for the
    daily-vs-full-scan distinction and why full-scan walks more than
    /players/new/.

    Daily mode (FULL_SCAN=false): a single partition, /players/new/,
    capped at MAX_PAGES_DAILY pages.

    Full-scan mode: every partition in FULL_SCAN_PARTITIONS (each capped
    at MAX_PAGES_FULL pages), merged into one URL -> image dict,
    de-duplicating by canonical card URL using the same "a None image
    coalesces to a real one" rule _paginate_listing() applies across
    pages of a single partition - a card that surfaces under more than
    one partition (e.g. a genuinely new card that appears in both the
    "new" feed and the main catalogue browse) is only fetched once.

    Returns card URL -> composited card image URL (or None) - see
    _read_card_hrefs' docstring for why this listing crawl is the only
    place that image can be captured. Callers that only need the URLs can
    still iterate/len() this like a plain list - dict keys behave
    identically for that."""
    if not FULL_SCAN:
        urls = await _paginate_listing(page, "/players/new/?page={page}", MAX_PAGES, "daily-new")
        log.info("Discovery mode=daily source=partition:daily-new unique=%d", len(urls))
        if PLAYER_LIMIT:
            return dict(list(urls.items())[:PLAYER_LIMIT])
        return urls

    combined: dict[str, str | None] = {}
    for name, template in FULL_SCAN_PARTITIONS:
        partition_urls = await _paginate_listing(page, template, MAX_PAGES_FULL, name)
        before = len(combined)
        for card_url, image_url in partition_urls.items():
            if card_url not in combined or (combined[card_url] is None and image_url):
                combined[card_url] = image_url
        log.info(
            "Discovery mode=full source=partition:%s partition_unique=%d new_to_combined=%d combined_total=%d",
            name, len(partition_urls), len(combined) - before, len(combined),
        )
        if PLAYER_LIMIT and len(combined) >= PLAYER_LIMIT:
            break

    log.info(
        "Discovery mode=full partitions=%d combined_unique=%d",
        len(FULL_SCAN_PARTITIONS), len(combined),
    )
    if PLAYER_LIMIT:
        return dict(list(combined.items())[:PLAYER_LIMIT])
    return combined


def _full_scan_is_plausible(discovered: int, db_total: int, min_coverage: float) -> bool:
    """A genuinely complete full-scan should discover at least
    min_coverage (default 90%) of whatever's already confirmed in
    futgg_players - existing rows don't vanish from FUT.GG, so a
    full-scan result well below the current row count means the
    discovery source(s) returned an incomplete catalogue, not that the
    real catalogue shrank. db_total<=0 (empty table - first ever run) or
    min_coverage<=0 (check disabled) always passes."""
    if db_total <= 0 or min_coverage <= 0:
        return True
    return discovered >= db_total * min_coverage


async def existing_ids(conn: asyncpg.Connection, urls) -> set[int]:
    """urls: any iterable of card URLs - a plain list, or (as
    collect_listing_urls now returns) a dict keyed by URL, iterated for
    its keys exactly the same way."""
    ids = []
    for url in urls:
        match = CARD_URL_RE.match(urlparse(url).path)
        if match:
            ids.append(int(match.group("card_id")))
    if not ids:
        return set()
    rows = await conn.fetch(
        "SELECT source_card_id FROM futgg_players WHERE source_card_id = ANY($1::bigint[])", ids
    )
    return {int(row["source_card_id"]) for row in rows}


async def cards_needing_release_metadata(
    conn: asyncpg.Connection, *, limit: int, game_year: str | None = None,
) -> list[tuple[str, str | None]]:
    """Cards that predate the released_at/display_name/full_name fields.

    Targeted metadata backfill (FUTGG_BACKFILL_RELEASE_METADATA=true)
    rather than FUTGG_REFRESH_EXISTING=true, which would force EVERY
    normal daily run to re-scrape the entire historical catalogue
    forever. This selects only rows actually missing something.

    Resumable and idempotent by construction: each pass re-runs the same
    query, so completed cards drop out of the result set naturally and an
    interrupted run simply picks up where it left off. Ordering is stable
    (released_at NULLs first, then oldest-seen) so progress is monotonic
    rather than reshuffling every pass.
    """
    where = [
        "source_url IS NOT NULL",
        "(released_at IS NULL OR display_name IS NULL OR full_name IS NULL)",
    ]
    args: list = []
    if game_year:
        args.append(int(game_year))
        where.append(f"game_year = ${len(args)}")
    args.append(limit)
    rows = await conn.fetch(
        f"""
        SELECT source_url, card_image_url
        FROM futgg_players
        WHERE {' AND '.join(where)}
        ORDER BY released_at NULLS FIRST, first_seen_at
        LIMIT ${len(args)}
        """,
        *args,
    )
    return [(r["source_url"], r["card_image_url"]) for r in rows]


async def count_cards_needing_release_metadata(
    conn: asyncpg.Connection, *, game_year: str | None = None,
) -> int:
    """How much work remains - logged before a backfill starts so the run
    can be sized (and its duration estimated at the configured
    concurrency/delay) rather than started blind."""
    where = [
        "source_url IS NOT NULL",
        "(released_at IS NULL OR display_name IS NULL OR full_name IS NULL)",
    ]
    args: list = []
    if game_year:
        args.append(int(game_year))
        where.append(f"game_year = ${len(args)}")
    return await conn.fetchval(
        f"SELECT count(*) FROM futgg_players WHERE {' AND '.join(where)}", *args
    )


def _build_targets(
    urls: dict[str, str | None], known: set[int], refresh_existing: bool,
) -> tuple[list[tuple[str, bool, str | None]], int]:
    """Splits discovered URLs into (fetch targets, skipped-existing
    count). A card already in futgg_players (card_id in known) is skipped
    unless refresh_existing - same skip rule crawl_once has always
    applied, pulled out into its own pure function so it's unit-testable
    without a live DB/Playwright."""
    targets: list[tuple[str, bool, str | None]] = []
    skipped = 0
    for url, listing_image_url in urls.items():
        match = CARD_URL_RE.match(urlparse(url).path)
        if not match:
            continue
        card_id = int(match.group("card_id"))
        existed = card_id in known
        if existed and not refresh_existing:
            skipped += 1
            continue
        targets.append((url, existed, listing_image_url))
    return targets, skipped


async def upsert_card(conn: asyncpg.Connection, card: FutggCard) -> None:
    data = asdict(card)
    price_tier = classify_price_tier(card)
    await conn.execute(
        """
        INSERT INTO futgg_players (
            source_card_id, source_player_id, source_slug, source_url, game_year,
            name, display_name, full_name, released_at, released_at_raw,
            rating, primary_position, alternate_positions, rarity, squad, price_tier,
            club, league, nation, club_source_id, league_source_id, nation_source_id,
            height_cm, weight_kg, foot, skill_moves, weak_foot, accelerate_type,
            body_type, real_face, shirt_number, age,
            pace, shooting, passing, dribbling, defending, physicality,
            diving, handling, kicking, reflexes, speed, positioning,
            player_image_url, card_design_image_url, card_image_url, club_image_url,
            league_image_url, nation_image_url, last_seen_at, metadata_updated_at,
            next_price_due_at, metadata_warnings
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
            $19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,
            $35,$36,$37,$38,$39,$40,$41,$42,$43,$44,$45,$46,$47,$48,$49,$50,
            NOW(),NOW(),NOW(),$51
        )
        ON CONFLICT (source_card_id) DO UPDATE SET
            source_player_id = EXCLUDED.source_player_id,
            source_slug = EXCLUDED.source_slug,
            source_url = EXCLUDED.source_url,
            game_year = EXCLUDED.game_year,
            name = COALESCE(EXCLUDED.name, futgg_players.name),
            -- Prefer a freshly parsed value, but never let a temporarily
            -- failed parse blank out metadata we already have. This is
            -- what makes the backfill safe to re-run and safe to
            -- interrupt: a card that fails once keeps whatever it had.
            display_name = COALESCE(EXCLUDED.display_name, futgg_players.display_name),
            full_name = COALESCE(EXCLUDED.full_name, futgg_players.full_name),
            released_at = COALESCE(EXCLUDED.released_at, futgg_players.released_at),
            released_at_raw = COALESCE(EXCLUDED.released_at_raw, futgg_players.released_at_raw),
            rating = COALESCE(EXCLUDED.rating, futgg_players.rating),
            primary_position = COALESCE(EXCLUDED.primary_position, futgg_players.primary_position),
            alternate_positions = EXCLUDED.alternate_positions,
            rarity = COALESCE(EXCLUDED.rarity, futgg_players.rarity),
            squad = COALESCE(EXCLUDED.squad, futgg_players.squad),
            price_tier = EXCLUDED.price_tier,
            club = COALESCE(EXCLUDED.club, futgg_players.club),
            league = COALESCE(EXCLUDED.league, futgg_players.league),
            nation = COALESCE(EXCLUDED.nation, futgg_players.nation),
            club_source_id = COALESCE(EXCLUDED.club_source_id, futgg_players.club_source_id),
            league_source_id = COALESCE(EXCLUDED.league_source_id, futgg_players.league_source_id),
            nation_source_id = COALESCE(EXCLUDED.nation_source_id, futgg_players.nation_source_id),
            height_cm = COALESCE(EXCLUDED.height_cm, futgg_players.height_cm),
            weight_kg = COALESCE(EXCLUDED.weight_kg, futgg_players.weight_kg),
            foot = COALESCE(EXCLUDED.foot, futgg_players.foot),
            skill_moves = COALESCE(EXCLUDED.skill_moves, futgg_players.skill_moves),
            weak_foot = COALESCE(EXCLUDED.weak_foot, futgg_players.weak_foot),
            accelerate_type = COALESCE(EXCLUDED.accelerate_type, futgg_players.accelerate_type),
            body_type = COALESCE(EXCLUDED.body_type, futgg_players.body_type),
            real_face = COALESCE(EXCLUDED.real_face, futgg_players.real_face),
            shirt_number = COALESCE(EXCLUDED.shirt_number, futgg_players.shirt_number),
            age = COALESCE(EXCLUDED.age, futgg_players.age),
            pace = COALESCE(EXCLUDED.pace, futgg_players.pace),
            shooting = COALESCE(EXCLUDED.shooting, futgg_players.shooting),
            passing = COALESCE(EXCLUDED.passing, futgg_players.passing),
            dribbling = COALESCE(EXCLUDED.dribbling, futgg_players.dribbling),
            defending = COALESCE(EXCLUDED.defending, futgg_players.defending),
            physicality = COALESCE(EXCLUDED.physicality, futgg_players.physicality),
            diving = COALESCE(EXCLUDED.diving, futgg_players.diving),
            handling = COALESCE(EXCLUDED.handling, futgg_players.handling),
            kicking = COALESCE(EXCLUDED.kicking, futgg_players.kicking),
            reflexes = COALESCE(EXCLUDED.reflexes, futgg_players.reflexes),
            speed = COALESCE(EXCLUDED.speed, futgg_players.speed),
            positioning = COALESCE(EXCLUDED.positioning, futgg_players.positioning),
            player_image_url = COALESCE(EXCLUDED.player_image_url, futgg_players.player_image_url),
            card_design_image_url = COALESCE(EXCLUDED.card_design_image_url, futgg_players.card_design_image_url),
            card_image_url = CASE
                WHEN EXCLUDED.card_image_url IS DISTINCT FROM futgg_players.card_image_url
                THEN EXCLUDED.card_image_url
                ELSE futgg_players.card_image_url
            END,
            -- A changed source image (new art, FUT.GG re-hashed the asset)
            -- must invalidate any previously-mirrored R2 copy so backend's
            -- mirror pass re-fetches it - otherwise a stale image would
            -- sit at card_image_stored_status='ready' forever pointing at
            -- old pixels. COALESCE guards the common case (URL unchanged
            -- or scrape didn't see an image this pass) from clobbering a
            -- perfectly good existing mirror.
            card_image_stored_status = CASE
                WHEN EXCLUDED.card_image_url IS NOT NULL
                     AND EXCLUDED.card_image_url IS DISTINCT FROM futgg_players.card_image_url
                THEN NULL
                ELSE futgg_players.card_image_stored_status
            END,
            club_image_url = COALESCE(EXCLUDED.club_image_url, futgg_players.club_image_url),
            league_image_url = COALESCE(EXCLUDED.league_image_url, futgg_players.league_image_url),
            nation_image_url = COALESCE(EXCLUDED.nation_image_url, futgg_players.nation_image_url),
            is_active = TRUE,
            last_seen_at = NOW(),
            metadata_updated_at = NOW(),
            metadata_warnings = EXCLUDED.metadata_warnings
        """,
        data["source_card_id"], data["source_player_id"], data["source_slug"], data["source_url"], data["game_year"],
        data["name"], data["display_name"], data["full_name"],
        data["released_at"], data["released_at_raw"],
        data["rating"], data["primary_position"], data["alternate_positions"], data["rarity"], data["squad"], price_tier,
        data["club"], data["league"], data["nation"], data["club_source_id"], data["league_source_id"], data["nation_source_id"],
        data["height_cm"], data["weight_kg"], data["foot"], data["skill_moves"], data["weak_foot"], data["accelerate_type"],
        data["body_type"], data["real_face"], data["shirt_number"], data["age"],
        data["pace"], data["shooting"], data["passing"], data["dribbling"], data["defending"], data["physicality"],
        data["diving"], data["handling"], data["kicking"], data["reflexes"], data["speed"], data["positioning"],
        data["player_image_url"], data["card_design_image_url"], data["card_image_url"],
        data["club_image_url"], data["league_image_url"], data["nation_image_url"],
        data["parse_warnings"],
    )


async def _fetch_worker(
    worker_id: int,
    page,
    pool: asyncpg.Pool,
    queue: "asyncio.Queue[tuple[int, str, bool, str | None]]",
    total: int,
    stats: dict[str, int],
    breaker: CircuitBreaker,
) -> None:
    while True:
        if breaker.tripped:
            log.warning("worker=%d stopping: circuit breaker tripped (%s)", worker_id, breaker.trip_reason)
            return
        try:
            index, url, existed, listing_image_url = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            status = response.status if response else 0
            if status in (403, 429):
                breaker.record_failure(f"HTTP {status}")
                stats["failed"] += 1
                log.warning("[%d/%d] worker=%d blocked HTTP %d: %s", index, total, worker_id, status, url)
                queue.task_done()
                continue
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            await page.locator(".fc-card").first.wait_for(state="attached", timeout=15000)
            await page.wait_for_timeout(800)
            card = parse_futgg_card(await page.content(), url)
            if listing_image_url:
                # The composited full-card image only exists on the
                # listing grid tile, never on the card's own detail page
                # (confirmed live - see collect_listing_urls'/
                # _read_card_hrefs' docstrings) - parse_futgg_card() can
                # never set this itself, so apply what the listing crawl
                # already captured for this URL.
                card.card_image_url = listing_image_url
            async with pool.acquire() as conn:
                await upsert_card(conn, card)
            breaker.record_success()
            stats["updated" if existed else "new"] += 1
            log.info(
                "[%d/%d] worker=%d upserted id=%s name=%r rating=%s rarity=%r tier=%s",
                index, total, worker_id, card.source_card_id, card.name, card.rating,
                card.rarity, classify_price_tier(card),
            )
        except Exception as exc:
            stats["failed"] += 1
            breaker.record_failure(f"{type(exc).__name__}: {exc}")
            log.warning("[%d/%d] worker=%d failed %s: %s", index, total, worker_id, url, exc)
        finally:
            queue.task_done()

        if REQUEST_DELAY:
            await asyncio.sleep(random.uniform(REQUEST_DELAY * 0.7, REQUEST_DELAY * 1.3))


async def crawl_once() -> None:
    # Retried: this is the worker's first statement, and an unguarded
    # connect here turned a one-second TLS blip into a dead worker.
    lock_conn = await connect_with_retry(DATABASE_URL)
    got_lock = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", OVERLAP_LOCK_KEY)
    if not got_lock:
        log.info("Previous FUT.GG player sync still running; skipping")
        await lock_conn.close()
        return

    pool = await create_pool_with_retry(
        DATABASE_URL, min_size=1, max_size=PLAYER_CONCURRENCY + 2
    )
    playwright = browser = context = None
    stats: dict[str, int] = {"discovered": 0, "new": 0, "updated": 0, "failed": 0, "skipped_existing": 0}
    breaker = CircuitBreaker(CIRCUIT_BREAKER_THRESHOLD)
    try:
        async with pool.acquire() as conn:
            await ensure_schema(conn)

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        # timezone_id is pinned deliberately. FUT.GG's "Added On" value
        # (parsed into FutggCard.released_at) carries no timezone in the
        # markup, so if that timestamp is rendered client-side in the
        # viewer's local zone - the common pattern - the string we scrape
        # would silently shift with whatever timezone the container host
        # happened to have. Pinning makes the parse deterministic and
        # makes futgg_common.ADDED_ON_ASSUMED_TIMEZONE correct by
        # construction rather than by assumption. If FUT.GG turns out to
        # render server-side in a fixed zone instead, this changes
        # nothing and costs nothing.
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-GB",
            timezone_id=BROWSER_TIMEZONE,
            viewport={"width": 1440, "height": 1000},
        )
        # Listing/pagination stays on its own dedicated page - sequential
        # by nature (stateful stabilize-retry against the previous page,
        # capped at a few hundred pages regardless), not the bottleneck.
        listing_page = await context.new_page()

        if BACKFILL_RELEASE_METADATA:
            # Backfill mode reuses EVERYTHING below this point - the same
            # fetch workers, the same parser, the same upsert, the same
            # circuit breaker and per-request delay. The only difference
            # is where the target list comes from: existing rows missing
            # the new metadata, rather than the listing crawl. No parallel
            # scraper, no duplicated fetch logic.
            async with pool.acquire() as conn:
                remaining = await count_cards_needing_release_metadata(
                    conn, game_year=BACKFILL_GAME_YEAR or None,
                )
                backfill_rows = await cards_needing_release_metadata(
                    conn, limit=BACKFILL_LIMIT, game_year=BACKFILL_GAME_YEAR or None,
                )
            est_seconds = (len(backfill_rows) * REQUEST_DELAY) / max(PLAYER_CONCURRENCY, 1)
            log.info(
                "BACKFILL MODE: %d card(s) still need release metadata%s; "
                "processing up to %d this run (~%.1f min at concurrency=%d delay=%.2fs). "
                "Re-run to continue - the query is resumable and idempotent.",
                remaining,
                f" (game_year={BACKFILL_GAME_YEAR})" if BACKFILL_GAME_YEAR else "",
                len(backfill_rows), est_seconds / 60.0, PLAYER_CONCURRENCY, REQUEST_DELAY,
            )
            urls = {url: image for url, image in backfill_rows}
            stats["discovered"] = len(urls)
            known = set()          # force every backfill row to be fetched
            db_total = remaining   # skips the full-scan plausibility check below
        else:
            urls = await collect_listing_urls(listing_page)  # card_url -> composited image url (or None)
            stats["discovered"] = len(urls)
            async with pool.acquire() as conn:
                known = await existing_ids(conn, urls)
                db_total = await conn.fetchval("SELECT COUNT(*) FROM futgg_players")

        scan_plausible = True
        if FULL_SCAN:
            scan_plausible = _full_scan_is_plausible(len(urls), db_total, FULL_SCAN_MIN_COVERAGE)
            log.info(
                "Full-scan plausibility check: discovered=%d db_total=%d min_coverage=%.2f plausible=%s",
                len(urls), db_total, FULL_SCAN_MIN_COVERAGE, scan_plausible,
            )
            if not scan_plausible:
                log.error(
                    "Full-scan discovered only %d cards but futgg_players already has %d rows "
                    "(below %.0f%% coverage) - discovery source(s) look incomplete; this run's "
                    "any legitimate upserts still apply, but it will NOT be reported as a "
                    "successful complete reconciliation",
                    len(urls), db_total, FULL_SCAN_MIN_COVERAGE * 100,
                )

        targets, skipped_existing = _build_targets(urls, known, REFRESH_EXISTING)
        stats["skipped_existing"] = skipped_existing

        log.info(
            "Discovery mode=%s discovered=%d known=%d targets=%d refresh_existing=%s concurrency=%d",
            "full" if FULL_SCAN else "daily", len(urls), len(known), len(targets), REFRESH_EXISTING, PLAYER_CONCURRENCY,
        )

        queue: "asyncio.Queue[tuple[int, str, bool, str | None]]" = asyncio.Queue()
        for index, (url, existed, listing_image_url) in enumerate(targets, start=1):
            queue.put_nowait((index, url, existed, listing_image_url))

        fetch_pages = [await context.new_page() for _ in range(PLAYER_CONCURRENCY)]
        try:
            await asyncio.gather(
                *(
                    _fetch_worker(worker_id + 1, fetch_page, pool, queue, len(targets), stats, breaker)
                    for worker_id, fetch_page in enumerate(fetch_pages)
                )
            )
        finally:
            for fetch_page in fetch_pages:
                await fetch_page.close()

        if breaker.tripped:
            log.warning("Circuit breaker tripped: %s - stopped fetching remaining cards this run", breaker.trip_reason)

        ok = (
            (stats["failed"] == 0 or (stats["new"] + stats["updated"]) > 0)
            and not breaker.tripped
            and scan_plausible
        )
        async with pool.acquire() as conn:
            await heartbeat(
                conn,
                "futgg_player_sync",
                ok=ok,
                detail=" ".join(f"{key}={value}" for key, value in stats.items()),
            )
        if not scan_plausible:
            await alert(
                f"futgg_player_sync full-scan looked incomplete: discovered={len(urls)} "
                f"db_total={db_total} min_coverage={FULL_SCAN_MIN_COVERAGE}"
            )
        elif not ok:
            await alert(f"futgg_player_sync failed: {stats}")
        log.info("Run complete: mode=%s scan_plausible=%s %s", "full" if FULL_SCAN else "daily", scan_plausible, stats)

    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        await pool.close()
        await lock_conn.execute("SELECT pg_advisory_unlock($1)", OVERLAP_LOCK_KEY)
        await lock_conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(crawl_once())
    except Exception as exc:
        log.exception("futgg_player_sync failed: %s", exc)
        sys.exit(1)
