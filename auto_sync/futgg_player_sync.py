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
    (/players/new/), capped at FUTGG_LISTING_MAX_PAGES pages as a safety
    ceiling. /players/new/ is a plain recency feed, not a same-year feed -
    confirmed live 2026-09-15: once the genuinely-new FC27 cards run out,
    the same feed starts resurfacing freshly-touched FC26 cards instead
    of ending, so pagination now also stops as soon as a page's cards
    stop matching the target game year (see _paginate_listing's
    target_game_year), rather than only on a page with zero new URLs or
    hitting the page cap. That is what lets FUTGG_LISTING_MAX_PAGES be
    set generously (enough to catch a launch day with hundreds of new
    cards) without also risking a wasted walk through the entire prior
    year's catalogue when today's new-card count is small.
  - Full scan (FUTGG_LISTING_FULL_SCAN=true): one or more partitions (see
    FUTGG_FULL_SCAN_PARTITIONS below), each capped at
    FUTGG_LISTING_MAX_PAGES_FULL pages, for an occasional full-catalogue
    reconciliation. In this mode page 1 is canonicalised to FUT.GG's
    public URL shape (`/players/` or `/players/new/`); page 2+ uses
    `?page=N`.

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
  FUTGG_LISTING_MAX_PAGES         50 (daily mode; a safety ceiling now -
                                   discovery stops as soon as a page's
                                   cards stop matching the current game
                                   year, so this rarely gets hit)
  FUTGG_LISTING_MAX_PAGES_FULL    400 (full-scan mode, per partition)
  FUTGG_GAME_YEAR                 27 (current FUT.GG cycle)
  FUTGG_FULL_SCAN_PARTITIONS      defaults to the selected game year:
                                   FC27/current: "new:/players/new/?page={page},catalogue:/players/?page={page}"
                                   older years: "new:/{game_year}/players/new/?page={page},catalogue:/{game_year}/players/?page={page}"
                                   comma-separated name:template pairs, each
                                   template containing a literal '{page}'
                                   placeholder. Templates may also include
                                   '{game_year}' and '{game_prefix}'.
  FUTGG_FULL_SCAN_MIN_COVERAGE    0.9 (full-scan must discover >= this
                                   fraction of futgg_players' current row
                                   count to be treated as a plausible
                                   complete catalogue; 0 disables the check)
  FUTGG_LISTING_IDLE_ROUNDS       4 (consecutive pages w/ 0 new cards before stopping, per partition)
  FUTGG_LISTING_PAGE_DELAY        0.5 seconds, between listing-page navigations
  FUTGG_LISTING_STABILIZE_ATTEMPTS 10 (retries waiting for card links and rating footers to hydrate)
  FUTGG_LISTING_STABILIZE_POLL_MS 400 (wait between stabilize retries)
  FUTGG_REFRESH_EXISTING          false
  FUTGG_PLAYER_REQUEST_DELAY      0.35 seconds, per fetch worker
  FUTGG_PLAYER_CONCURRENCY        3 (parallel card-detail fetch workers - see _fetch_worker())
  FUTGG_CIRCUIT_BREAKER_THRESHOLD 5 (consecutive 403/429/failed navigations before this run stops)
"""

from __future__ import annotations

from playwright_stealth import Stealth

import asyncio
import logging
import os
import random
import re
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import asyncpg
from bs4 import BeautifulSoup, Tag

from db_connect import connect_with_retry, create_pool_with_retry
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from futgg_common import (
    BASE_URL, CARD_URL_RE, POSITIONS, CircuitBreaker, FutggCard, upsize_card_image,
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
GAME_YEAR = int(os.getenv("FUTGG_GAME_YEAR", "27"))
CURRENT_FUTGG_GAME_YEAR = int(os.getenv("FUTGG_CURRENT_GAME_YEAR", "27"))
GAME_PREFIX = "" if GAME_YEAR == CURRENT_FUTGG_GAME_YEAR else f"/{GAME_YEAR}"
TIMEOUT_MS = max(5000, int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "45000")))
PLAYER_LIMIT = max(0, int(os.getenv("FUTGG_PLAYER_LIMIT", "0")))

# Pagination (replaces scroll-based discovery entirely - see
# collect_listing_urls()). Two page caps, selected by FULL_SCAN: a small
# one for the normal daily run (new cards appear at the front of the
# listing, so a handful of pages is enough) and a large one, applied per
# partition, for an occasional full-catalogue sync.
FULL_SCAN = os.getenv("FUTGG_LISTING_FULL_SCAN", "false").strip().lower() in {"1", "true", "yes", "on"}
CONFIGURATION_ERROR = (
    "FUTGG_BACKFILL_RELEASE_METADATA=true cannot be combined with "
    "FUTGG_LISTING_FULL_SCAN=true. Backfill mode reads existing DB rows "
    "missing release metadata; full-scan mode crawls FUT.GG listing pages "
    "for new card data. For the FC27 card pull, unset "
    "FUTGG_BACKFILL_RELEASE_METADATA and FUTGG_BACKFILL_GAME_YEAR."
    if BACKFILL_RELEASE_METADATA and FULL_SCAN
    else ""
)
MAX_PAGES_DAILY = max(1, int(os.getenv("FUTGG_LISTING_MAX_PAGES", "50")))
MAX_PAGES_FULL = max(1, int(os.getenv("FUTGG_LISTING_MAX_PAGES_FULL", "400")))
MAX_PAGES = MAX_PAGES_FULL if FULL_SCAN else MAX_PAGES_DAILY
# Cross-check the main catalogue's front pages on every daily run too.
# Fresh special versions can surface there before (or instead of) the
# recency listing; this is intentionally a bounded second discovery pass.
DAILY_CATALOGUE_PAGES = max(0, min(10, int(os.getenv("FUTGG_DAILY_CATALOGUE_PAGES", "3"))))

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
DEFAULT_FULL_SCAN_PARTITIONS = (
    f"new:{GAME_PREFIX}/players/new/?page={{page}},"
    f"catalogue:{GAME_PREFIX}/players/?page={{page}}"
)


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
LISTING_STABILIZE_MAX_ATTEMPTS = max(1, int(os.getenv("FUTGG_LISTING_STABILIZE_ATTEMPTS", "10")))
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


@dataclass(frozen=True)
class ListingFooterMetadata:
    """Sanity fields from one tile footer; only gg_rating is persisted."""

    position: str
    gg_rating: Decimal
    price_text: str


@dataclass(frozen=True)
class ListingCardMetadata:
    image_url: str | None = None
    gg_rating: Decimal | None = None
    gg_rating_invalid: bool = False


@dataclass(frozen=True)
class FetchTarget:
    url: str
    existed: bool
    listing: ListingCardMetadata


@dataclass(frozen=True)
class ListingRatingUpdateResult:
    matched: int
    updated: int


BLOCKED_LISTING_STATUSES = {403, 429}


class ListingDiscoveryBlocked(RuntimeError):
    def __init__(self, partition_label: str, status: int) -> None:
        self.partition_label = partition_label
        self.status = status
        super().__init__(
            f"FUT.GG listing partition={partition_label} blocked with HTTP {status}"
        )


def _parse_gg_rating(value: str | None) -> Decimal | None:
    text = (value or "").strip()
    if not text or not re.fullmatch(r"\d{1,3}(?:\.\d)?", text):
        return None
    try:
        rating = Decimal(text)
    except InvalidOperation:
        return None
    if rating < 0 or rating > 100:
        return None
    return rating.quantize(Decimal("0.1"))


def parse_listing_footer(tile: Tag) -> ListingFooterMetadata | None:
    """Read position/rating/price from a single card tile structurally.

    A recognised footer status icon anchors the price/status group. Its sibling must contain a
    recognised FUT position plus exactly one broad-range numeric value;
    that prevents card OVR and listing price numbers elsewhere in the
    tile from being mistaken for GG Rating. Tradeable cards use Coin;
    live catalogue variants use the same structure with SBC/Objective.
    """
    for marker in tile.select('img[alt="Coin"], img[alt="SBC"], img[alt="Objective"]'):
        price_group = marker.parent
        if not isinstance(price_group, Tag) or not isinstance(price_group.parent, Tag):
            continue
        footer_row = price_group.parent
        sibling_groups = [child for child in footer_row.children if isinstance(child, Tag) and child is not price_group]
        for group in sibling_groups:
            texts = [span.get_text(" ", strip=True) for span in group.find_all("span")]
            positions = [text for text in texts if text.upper() in POSITIONS]
            ratings = [rating for text in texts if (rating := _parse_gg_rating(text)) is not None]
            if len(positions) != 1 or len(ratings) != 1:
                continue
            price_text = price_group.get_text(" ", strip=True)
            return ListingFooterMetadata(
                position=positions[0].upper(),
                gg_rating=ratings[0],
                price_text=price_text,
            )
    return None


def _has_invalid_listing_rating(tile: Tag) -> bool:
    """A position/price footer exists and contains a non-position span,
    but no valid GG Rating. This distinguishes malformed text from a tile
    where FUT.GG simply did not render the field."""
    for marker in tile.select('img[alt="Coin"], img[alt="SBC"], img[alt="Objective"]'):
        price_group = marker.parent
        if not isinstance(price_group, Tag) or not isinstance(price_group.parent, Tag):
            continue
        for group in (child for child in price_group.parent.children if isinstance(child, Tag) and child is not price_group):
            spans = group.find_all("span")
            texts = [span.get_text(" ", strip=True) for span in spans]
            if any(text.upper() in POSITIONS for text in texts) and any(
                text.upper() not in POSITIONS for text in texts
            ):
                return True
    return False


def _metadata_from_listing_node(node: dict[str, Any]) -> tuple[str, ListingCardMetadata] | None:
    href = node.get("href")
    if not href:
        return None
    absolute = urljoin(BASE_URL, href)
    parsed = urlparse(absolute)
    if not CARD_URL_RE.match(parsed.path):
        return None

    image = node.get("image")
    if image and "player-item-card/" in image:
        image = upsize_card_image(urljoin(BASE_URL, image))
    else:
        image = None

    gg_rating = None
    gg_rating_invalid = False
    tile_html = node.get("tile_html")
    if tile_html:
        soup = BeautifulSoup(tile_html, "html.parser")
        tile = soup.find("a") or soup
        footer = parse_listing_footer(tile)
        gg_rating = footer.gg_rating if footer else None
        gg_rating_invalid = footer is None and _has_invalid_listing_rating(tile)
    elif "gg_rating" in node:
        # Test/diagnostic callers may supply already-extracted fields.
        gg_rating = _parse_gg_rating(str(node.get("gg_rating") or ""))

    canonical = urljoin(BASE_URL, parsed.path)
    return canonical, ListingCardMetadata(
        image_url=image,
        gg_rating=gg_rating,
        gg_rating_invalid=gg_rating_invalid,
    )


def _merge_listing_metadata(
    existing: ListingCardMetadata | None,
    incoming: ListingCardMetadata,
) -> ListingCardMetadata:
    if existing is None:
        return incoming
    return ListingCardMetadata(
        image_url=incoming.image_url or existing.image_url,
        gg_rating=incoming.gg_rating if incoming.gg_rating is not None else existing.gg_rating,
        gg_rating_invalid=(
            False
            if incoming.gg_rating is not None or existing.gg_rating is not None
            else incoming.gg_rating_invalid or existing.gg_rating_invalid
        ),
    )


def _listing_metadata_ready(cards: dict[str, ListingCardMetadata]) -> bool:
    """Whether FUT.GG has hydrated every visible card footer.

    Card anchors and images can appear before the client-rendered footer.
    A parsed or explicitly invalid rating proves the footer is present;
    otherwise the href set is still an incomplete render. The caller's
    bounded attempt budget handles cards that genuinely have no footer.
    """
    return bool(cards) and all(
        metadata.gg_rating is not None or metadata.gg_rating_invalid
        for metadata in cards.values()
    )


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS futgg_players (
            source_card_id BIGINT NOT NULL,
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
            gg_rating NUMERIC(4,1),
            gg_rating_updated_at TIMESTAMPTZ,

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
            metadata_warnings TEXT[] NOT NULL DEFAULT '{}',
            PRIMARY KEY (game_year, source_card_id)
        )
        """
    )
    await conn.execute(
        """
        DO $$
        DECLARE
            pk_name text;
        BEGIN
            SELECT conname INTO pk_name
            FROM pg_constraint
            WHERE conrelid = 'futgg_players'::regclass
              AND contype = 'p'
              AND conkey = ARRAY[
                  (SELECT attnum FROM pg_attribute WHERE attrelid = 'futgg_players'::regclass AND attname = 'source_card_id')
              ];

            IF pk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE futgg_players DROP CONSTRAINT %I CASCADE', pk_name);
                ALTER TABLE futgg_players ADD PRIMARY KEY (game_year, source_card_id);
            END IF;
        END
        $$;
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
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS gg_rating NUMERIC(4,1)"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS gg_rating_updated_at TIMESTAMPTZ"
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
        "CREATE INDEX IF NOT EXISTS futgg_players_source_card_idx ON futgg_players (source_card_id)"
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
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS futgg_sales_priority_requests (
            id            BIGSERIAL PRIMARY KEY,
            card_id       BIGINT NOT NULL,
            requested_by  TEXT,
            requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            consumed_at   TIMESTAMPTZ
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_futgg_sales_priority_requests_unconsumed
        ON futgg_sales_priority_requests (requested_at)
        WHERE consumed_at IS NULL
        """
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


async def _read_card_hrefs(page) -> dict[str, ListingCardMetadata]:
    """Canonical visible card URLs mapped to tile-scoped metadata.

    Metadata contains the composited full-card image src if present (the single
    background+cutout+badges render FUT.GG itself composites - see
    futgg_common.py's "player-item-card/" branch) and the structurally
    parsed GG Rating from the position/price footer. This listing grid is
    the ONLY place that image can be captured from: a live production
    backfill reached 100% coverage on the per-card detail page's own
    cutout/background fields while the composited image stayed at 0%
    coverage, confirming FUT.GG does not render it on the individual
    card page at all, only here."""
    script = """
    nodes => nodes.map(node => ({
        href: node.getAttribute('href'),
        image: Array.from(node.querySelectorAll('img[src]'))
            .map(image => image.src)
            .find(src => src.includes('player-item-card/')) || null,
        tile_html: node.outerHTML,
    }))
    """
    for attempt in range(3):
        try:
            nodes = await page.locator("a[href]").evaluate_all(script)
            break
        except PlaywrightError as exc:
            if "Execution context was destroyed" not in str(exc) or attempt == 2:
                raise
            log.warning(
                "Listing href read hit a navigation race; retrying attempt=%d/3",
                attempt + 2,
            )
            await page.wait_for_timeout(1000)
    matched: dict[str, ListingCardMetadata] = {}
    for node in nodes:
        parsed = _metadata_from_listing_node(node)
        if parsed is None:
            continue
        canonical, metadata = parsed
        matched[canonical] = _merge_listing_metadata(matched.get(canonical), metadata)
    return matched



def _canonical_first_listing_url(url: str) -> str:
    """Use FUT.GG's canonical first listing page instead of `?page=1`.

    Live FC27 pages are exposed as `/players/` and `/players/new/`. The
    `?page=1` form can behave differently behind Cloudflare/client
    routing, while page 2+ still needs the explicit query parameter.
    """
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key == "page" and value == "1")
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def _alternate_first_listing_url(url: str) -> str:
    """Toggle page 1 between FUT.GG's canonical and explicit forms.

    FUT.GG has flipped which first-page shape hydrates reliably more than
    once. Page 2+ is stable with an explicit query parameter, but page 1
    can come back as an empty client-rendered grid while the same listing
    is visible at the other first-page URL. Treat that as recoverable.
    """
    parsed = urlparse(url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    has_page_one = any(key == "page" and value == "1" for key, value in query_pairs)
    if has_page_one:
        query = [(key, value) for key, value in query_pairs if not (key == "page" and value == "1")]
    else:
        query = query_pairs + [("page", "1")]
    return urlunparse(parsed._replace(query=urlencode(query)))


async def _paginate_listing(
    page,
    url_template: str,
    max_pages: int,
    partition_label: str,
    *,
    start_page: int = 1,
    canonical_first_page: bool = False,
    page_callback: Callable[[int, dict[str, ListingCardMetadata]], Awaitable[None]] | None = None,
    target_game_year: int | None = None,
) -> dict[str, ListingCardMetadata]:
    """Paginates a single FUT.GG listing partition (?page=1, ?page=2, ...
    against url_template, a path containing a literal '{page}'
    placeholder) - no window.scrollTo() or scroll-settle waits anywhere.
    Full-scan callers may canonicalise page 1 to `/players/` instead of
    `/players/?page=1`, matching FUT.GG's public URL shape.
    Each page is a direct navigation; max_pages caps how many pages are
    visited, and IDLE_ROUNDS consecutive pages with zero new cards stops
    this partition early regardless of the cap, since that's the real
    signal pagination has run past the end of it. This is the shared
    engine behind both daily discovery (a single partition) and full-scan
    discovery (one or more partitions, merged - see collect_listing_urls());
    partition_label only affects logging.

    target_game_year, when set, closes a real gap the "0 new URLs" idle
    check cannot see: /players/new/ genuinely is a recency feed, not a
    same-year feed - confirmed live 2026-09-15, a daily-mode run capped
    at 5 pages found exactly the FC27 Icons/previews then live, but
    raising that cap for a full-scan run walked past page 5 into ~10,000
    DISTINCT card URLs, every one of them still game_year=26, because
    FUT.GG keeps surfacing freshly-touched OLD-year cards in the same
    feed once the genuinely-new backlog is exhausted. Each of those pages
    has new_this_page > 0 (they are new URLs this run has not seen), so
    the existing idle check never fires and pagination only stops at
    FUT.GG's actual last page. When target_game_year is set, a page
    counts as idle unless at least one newly-discovered URL on it
    actually parses to that game_year - so daily discovery naturally
    stops once it runs past the current year's cards into old-year
    backlog, regardless of how high max_pages is set, instead of either
    missing cards past a too-low cap or wastefully walking the entire
    old-year catalogue past a too-high one.

    Returns card URL -> ListingCardMetadata. An optional callback runs
    after each page stabilises; the GG-rating backfill uses it to commit
    and checkpoint one listing page at a time."""
    seen: dict[str, ListingCardMetadata] = {}
    idle_pages = 0
    previous_page_hrefs: dict[str, ListingCardMetadata] = {}

    for page_number in range(start_page, start_page + max_pages):
        listing_url = f"{BASE_URL}{url_template.format(page=page_number, game_year=GAME_YEAR, game_prefix=GAME_PREFIX)}"
        if canonical_first_page and page_number == 1:
            listing_url = _canonical_first_listing_url(listing_url)
        response = await page.goto(listing_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        status = response.status if response else 0
        if status != 200:
            if page_number == 1:
                if status in BLOCKED_LISTING_STATUSES:
                    raise ListingDiscoveryBlocked(partition_label, status)
                raise RuntimeError(f"FUT.GG listing partition={partition_label} returned HTTP {status}")
            if page_callback is not None:
                raise RuntimeError(
                    f"FUT.GG listing partition={partition_label} page={page_number} returned HTTP {status}"
                )
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
            if (
                current_hrefs
                and current_hrefs != previous_page_hrefs
                and _listing_metadata_ready(current_hrefs)
            ):
                break
            await page.wait_for_timeout(LISTING_STABILIZE_POLL_MS)
            current_hrefs = await _read_card_hrefs(page)

        if page_number == 1 and not current_hrefs:
            alternate_url = _alternate_first_listing_url(listing_url)
            if alternate_url != listing_url:
                log.warning(
                    "Listing partition=%s page 1 returned an empty grid at %s; retrying alternate first-page URL %s",
                    partition_label,
                    listing_url,
                    alternate_url,
                )
                response = await page.goto(alternate_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                status = response.status if response else 0
                if status == 200:
                    await dismiss_cookie_banner(page)
                    try:
                        await page.locator("a[href*='/players/']").first.wait_for(state="attached", timeout=TIMEOUT_MS)
                    except PlaywrightTimeoutError:
                        current_hrefs = {}
                    else:
                        current_hrefs = await _read_card_hrefs(page)
                        for _ in range(LISTING_STABILIZE_MAX_ATTEMPTS - 1):
                            if (
                                current_hrefs
                                and current_hrefs != previous_page_hrefs
                                and _listing_metadata_ready(current_hrefs)
                            ):
                                break
                            await page.wait_for_timeout(LISTING_STABILIZE_POLL_MS)
                            current_hrefs = await _read_card_hrefs(page)
                    if current_hrefs:
                        listing_url = alternate_url
                else:
                    log.warning(
                        "Listing partition=%s alternate page 1 URL returned HTTP %d",
                        partition_label,
                        status,
                    )

        # Page 1 is where today's special cards appear. Never treat an
        # empty first page as successful discovery and silently proceed to
        # page 2: that produced runs with zero targets while fresh cards
        # remained feed-only placeholders in PostgreSQL.
        if page_number == 1 and not current_hrefs:
            for retry in range(2):
                retry_url = (
                    _canonical_first_listing_url(listing_url)
                    if retry % 2 == 0
                    else _alternate_first_listing_url(
                        _canonical_first_listing_url(listing_url)
                    )
                )
                log.warning(
                    "Listing partition=%s page 1 still empty; reloading %s "
                    "(retry %d/2)", partition_label, retry_url, retry + 1,
                )
                response = await page.goto(
                    retry_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS
                )
                if response is None or response.status != 200:
                    continue
                await dismiss_cookie_banner(page)
                for _ in range(LISTING_STABILIZE_MAX_ATTEMPTS):
                    current_hrefs = await _read_card_hrefs(page)
                    if current_hrefs:
                        break
                    await page.wait_for_timeout(LISTING_STABILIZE_POLL_MS)
                if current_hrefs:
                    break
            if not current_hrefs:
                raise RuntimeError(
                    f"FUT.GG listing partition={partition_label} page 1 "
                    "remained empty after canonical/alternate reloads; "
                    "discovery incomplete, do not silently skip new cards"
                )

        before = len(seen)
        for card_url, metadata in current_hrefs.items():
            seen[card_url] = _merge_listing_metadata(seen.get(card_url), metadata)
        new_this_page = len(seen) - before
        previous_page_hrefs = current_hrefs

        if page_callback is not None:
            await page_callback(page_number, current_hrefs)

        log.info(
            "Listing partition=%s page %d: %d new cards, %d unique total",
            partition_label, page_number, new_this_page, len(seen),
        )

        if PLAYER_LIMIT and len(seen) >= PLAYER_LIMIT:
            break

        page_matches_target_year = target_game_year is None or any(
            (match := CARD_URL_RE.match(urlparse(card_url).path)) and int(match.group("game_year")) == target_game_year
            for card_url in current_hrefs
        )
        if new_this_page == 0 or not page_matches_target_year:
            idle_pages += 1
            if idle_pages >= IDLE_ROUNDS:
                log.info(
                    "partition=%s: %d consecutive pages %s - stopping pagination",
                    partition_label, idle_pages,
                    "with no new cards" if new_this_page == 0 else f"with no game_year={target_game_year} cards",
                )
                break
        else:
            idle_pages = 0

        if page_number < start_page + max_pages - 1 and PAGE_DELAY:
            await asyncio.sleep(random.uniform(PAGE_DELAY * 0.7, PAGE_DELAY * 1.3))

    return seen


async def collect_listing_urls(page) -> dict[str, ListingCardMetadata]:
    """Top-level discovery entry point - see module docstring for the
    daily-vs-full-scan distinction and why full-scan walks more than
    /players/new/.

    Daily mode (FULL_SCAN=false): a single partition, the selected
    game year's /players/new/,
    capped at MAX_PAGES_DAILY pages.

    Full-scan mode: every partition in FULL_SCAN_PARTITIONS (each capped
    at MAX_PAGES_FULL pages), merged into one URL -> image dict,
    de-duplicating by canonical card URL using the same "a None image
    coalesces to a real one" rule _paginate_listing() applies across
    pages of a single partition - a card that surfaces under more than
    one partition (e.g. a genuinely new card that appears in both the
    "new" feed and the main catalogue browse) is only fetched once.

    Returns card URL -> ListingCardMetadata - see
    _read_card_hrefs' docstring for why this listing crawl is the only
    place that image can be captured. Callers that only need the URLs can
    still iterate/len() this like a plain list - dict keys behave
    identically for that."""
    if not FULL_SCAN:
        urls: dict[str, ListingCardMetadata] = {}
        sources_ok = 0
        for label, template, page_cap in (
            ("daily-new", f"{GAME_PREFIX}/players/new/?page={{page}}", MAX_PAGES),
            ("daily-catalogue", f"{GAME_PREFIX}/players/?page={{page}}", DAILY_CATALOGUE_PAGES),
        ):
            if page_cap == 0:
                continue
            try:
                discovered = await _paginate_listing(
                    page, template, page_cap, label,
                    canonical_first_page=True, target_game_year=GAME_YEAR,
                )
            except ListingDiscoveryBlocked as exc:
                log.warning("Discovery source=%s blocked HTTP %d", label, exc.status)
                continue
            except Exception as exc:
                log.warning("Discovery source=%s failed: %s", label, exc)
                continue
            sources_ok += 1
            before = len(urls)
            for card_url, metadata in discovered.items():
                urls[card_url] = _merge_listing_metadata(urls.get(card_url), metadata)
            log.info("Discovery source=%s found=%d added=%d merged=%d",
                     label, len(discovered), len(urls) - before, len(urls))
        if sources_ok == 0:
            raise RuntimeError("All daily FUT.GG card listing sources failed")
        log.info("Discovery mode=daily sources_ok=%d unique=%d", sources_ok, len(urls))
        if PLAYER_LIMIT:
            return dict(list(urls.items())[:PLAYER_LIMIT])
        return urls

    combined: dict[str, ListingCardMetadata] = {}
    partition_failures: list[str] = []
    for name, template in FULL_SCAN_PARTITIONS:
        try:
            partition_urls = await _paginate_listing(
                page, template, MAX_PAGES_FULL, name, canonical_first_page=True,
            )
        except Exception as exc:
            partition_failures.append(f"{name}: {exc}")
            log.warning(
                "Discovery mode=full source=partition:%s failed; continuing with remaining partitions: %s",
                name, exc,
            )
            continue
        before = len(combined)
        for card_url, metadata in partition_urls.items():
            combined[card_url] = _merge_listing_metadata(combined.get(card_url), metadata)
        log.info(
            "Discovery mode=full source=partition:%s partition_unique=%d new_to_combined=%d combined_total=%d",
            name, len(partition_urls), len(combined) - before, len(combined),
        )
        if PLAYER_LIMIT and len(combined) >= PLAYER_LIMIT:
            break

    if not combined and partition_failures:
        raise RuntimeError(
            "All FUT.GG full-scan listing partitions failed: "
            + "; ".join(partition_failures)
        )

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


# EA special card IDs encode the underlying base player ID in their low
# 24 bits. Recover only those for which we already have a real FC27 player
# URL; never crawl feed-only synthetic URLs or trust old FC26 metadata.
SPECIAL_CARD_ID_MODULUS = 1 << 24
PLACEHOLDER_RECOVERY_LIMIT = max(
    0, min(250, int(os.getenv("FUTGG_PLACEHOLDER_RECOVERY_LIMIT", "60")))
)


async def recover_placeholder_urls(
    conn: asyncpg.Connection, *, limit: int = PLACEHOLDER_RECOVERY_LIMIT,
) -> dict[str, ListingCardMetadata]:
    """Build verifiable real URLs for price-feed placeholders using FC27 base slugs.

    Resumable: upsert_card removes the placeholder warning on successful
    metadata import. Future runs therefore skip recovered IDs.
    """
    if limit <= 0:
        return {}
    rows = await conn.fetch(
        """
        SELECT pending.source_card_id AS card_id,
               base.source_player_id AS player_id,
               base.source_slug AS slug
        FROM futgg_players pending
        JOIN futgg_players base
          ON base.game_year = $1
         AND base.source_card_id = (pending.source_card_id % $2)
         AND base.source_player_id = base.source_card_id
         AND base.name IS NOT NULL
         AND base.rating IS NOT NULL
         AND base.source_slug IS NOT NULL
         AND base.source_slug <> ''
         AND NOT ('feed_only_placeholder' = ANY(base.metadata_warnings))
        WHERE pending.game_year = $1
          AND pending.source_card_id >= $2
          AND 'feed_only_placeholder' = ANY(pending.metadata_warnings)
        -- Prefer genuine lower-ID special variants (including TOTW), rather
        -- than the large FUT Champions red-card ranges that dominated
        -- previous recovery batches.
        ORDER BY CASE
                   WHEN pending.source_card_id BETWEEN 50000000 AND 83900000 THEN 0
                   ELSE 1
                 END,
                 pending.price_updated_at DESC NULLS LAST,
                 pending.source_card_id DESC
        LIMIT $3
        """,
        GAME_YEAR, SPECIAL_CARD_ID_MODULUS, limit,
    )
    return {
        f"https://www.fut.gg/players/{int(row['player_id'])}-{row['slug']}/"
        f"{GAME_YEAR}-{int(row['card_id'])}/": ListingCardMetadata()
        for row in rows
    }


async def existing_ids(conn: asyncpg.Connection, urls, game_year: int = GAME_YEAR) -> set[int]:
    """urls: any iterable of card URLs - a plain list, or (as
    collect_listing_urls now returns) a dict keyed by URL, iterated for
    its keys exactly the same way. Bulk-feed placeholders still need their
    first metadata scrape, so they are not treated as already discovered."""
    ids = []
    for url in urls:
        match = CARD_URL_RE.match(urlparse(url).path)
        if match:
            ids.append(int(match.group("card_id")))
    if not ids:
        return set()
    rows = await conn.fetch(
        """
        SELECT source_card_id
        FROM futgg_players
        WHERE source_card_id = ANY($1::bigint[])
          AND game_year = $2
          AND NOT ('feed_only_placeholder' = ANY(metadata_warnings))
          AND name IS NOT NULL
          AND rating IS NOT NULL
          AND primary_position IS NOT NULL
        """,
        ids, game_year,
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


async def update_listing_gg_ratings(
    conn: asyncpg.Connection,
    cards: dict[str, ListingCardMetadata],
    game_year: int,
    *,
    only_missing: bool,
) -> ListingRatingUpdateResult:
    """Persist valid listing observations for existing cards only.

    Both source_card_id and game_year are checked, so an FC26 backfill
    cannot write an FC27 row even if an upstream identifier is reused.
    The timestamp means last successfully observed, including an
    unchanged value. Missing/invalid parses never enter this statement.
    """
    observations: dict[int, Decimal] = {}
    for url, metadata in cards.items():
        match = CARD_URL_RE.match(urlparse(url).path)
        if not match or int(match.group("game_year")) != game_year or metadata.gg_rating is None:
            continue
        observations[int(match.group("card_id"))] = metadata.gg_rating
    if not observations:
        return ListingRatingUpdateResult(matched=0, updated=0)

    ordered_observations = sorted(observations.items())
    for attempt in range(4):
        try:
            row = await conn.fetchrow(
                """
                WITH input(source_card_id, gg_rating) AS (
                    SELECT * FROM unnest($1::bigint[], $2::numeric[])
                ),
                matched AS MATERIALIZED (
                    SELECT p.source_card_id, i.gg_rating
                    FROM futgg_players p
                    JOIN input i USING (source_card_id)
                    WHERE p.game_year = $3
                ),
                updated AS (
                    UPDATE futgg_players p
                       SET gg_rating = m.gg_rating,
                           gg_rating_updated_at = NOW()
                      FROM matched m
                     WHERE p.source_card_id = m.source_card_id
                       AND p.game_year = $3
                       AND (NOT $4::boolean OR p.gg_rating IS NULL)
                    RETURNING p.source_card_id
                )
                SELECT (SELECT COUNT(*) FROM matched) AS matched,
                       (SELECT COUNT(*) FROM updated) AS updated
                """,
                [card_id for card_id, _ in ordered_observations],
                [rating for _, rating in ordered_observations],
                game_year,
                only_missing,
            )
            break
        except asyncpg.exceptions.DeadlockDetectedError:
            if attempt == 3:
                raise
            delay = 0.25 * (2 ** attempt)
            log.warning(
                "GG Rating update deadlocked; retrying attempt=%d/4 in %.2fs",
                attempt + 2,
                delay,
            )
            await asyncio.sleep(delay)
    return ListingRatingUpdateResult(matched=int(row["matched"]), updated=int(row["updated"]))


def _card_ids_from_listing_urls(
    urls: dict[str, ListingCardMetadata],
    game_year: int,
) -> list[int]:
    card_ids: set[int] = set()
    for url in urls:
        match = CARD_URL_RE.match(urlparse(url).path)
        if match and int(match.group("game_year")) == game_year:
            card_ids.add(int(match.group("card_id")))
    return sorted(card_ids)


async def enqueue_discovered_sales_priority(
    conn: asyncpg.Connection,
    urls: dict[str, ListingCardMetadata],
    game_year: int,
) -> int:
    """Queue high-value discovered cards for sales collection, even when
    they are already known and therefore skipped by the metadata fetch.

    This closes the practical gap where a new promo/TOTW card is inserted
    on one run, then every later daily discovery says targets=0 and never
    touches upsert_card again. The request table is consumed by
    futgg_sales_triggers.py; this statement only adds cards that do not
    already have an unconsumed priority request.
    """
    card_ids = _card_ids_from_listing_urls(urls, game_year)
    if not card_ids:
        return 0
    sql = """
        WITH eligible AS (
            SELECT DISTINCT p.source_card_id
            FROM futgg_players p
            WHERE p.source_card_id = ANY($1::bigint[])
              AND p.game_year = $2
              AND p.is_active
              AND p.is_tradeable IS NOT FALSE
              AND p.rating >= 80
              AND COALESCE(p.price_tier, '') NOT IN ('bronze', 'silver')
              AND (
                  p.last_sales_status_at IS NULL
                  OR p.last_sales_status_at <= now() - interval '60 minutes'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM futgg_sales_priority_requests r
                  WHERE r.card_id = p.source_card_id
                    AND r.consumed_at IS NULL
              )
        ),
        inserted AS (
            INSERT INTO futgg_sales_priority_requests (card_id, requested_by)
            SELECT source_card_id, 'daily_new_discovery'
            FROM eligible
            RETURNING card_id
        )
        SELECT count(*) AS queued FROM inserted
    """
    try:
        row = await conn.fetchrow(sql, card_ids, game_year)
    except asyncpg.exceptions.UndefinedColumnError:
        # Older databases may not have last_sales_status_at until the
        # sales worker has bootstrapped. Still queue discovered cards; the
        # trigger consumer can dedupe by unconsumed request.
        row = await conn.fetchrow(
            """
            WITH eligible AS (
                SELECT DISTINCT p.source_card_id
                FROM futgg_players p
                WHERE p.source_card_id = ANY($1::bigint[])
                  AND p.game_year = $2
                  AND p.is_active
                  AND p.is_tradeable IS NOT FALSE
                  AND p.rating >= 80
                  AND COALESCE(p.price_tier, '') NOT IN ('bronze', 'silver')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM futgg_sales_priority_requests r
                      WHERE r.card_id = p.source_card_id
                        AND r.consumed_at IS NULL
                  )
            ),
            inserted AS (
                INSERT INTO futgg_sales_priority_requests (card_id, requested_by)
                SELECT source_card_id, 'daily_new_discovery'
                FROM eligible
                RETURNING card_id
            )
            SELECT count(*) AS queued FROM inserted
            """,
            card_ids,
            game_year,
        )
    return int(row["queued"] or 0)


def _build_targets(
    urls: dict[str, ListingCardMetadata], known: set[int], refresh_existing: bool,
) -> tuple[list[FetchTarget], int]:
    """Splits discovered URLs into (fetch targets, skipped-existing
    count). A card already in futgg_players (card_id in known) is skipped
    unless refresh_existing - same skip rule crawl_once has always
    applied, pulled out into its own pure function so it's unit-testable
    without a live DB/Playwright."""
    targets: list[FetchTarget] = []
    skipped = 0
    for url, listing in urls.items():
        match = CARD_URL_RE.match(urlparse(url).path)
        if not match:
            continue
        card_id = int(match.group("card_id"))
        existed = card_id in known
        if existed and not refresh_existing:
            skipped += 1
            continue
        targets.append(FetchTarget(url=url, existed=existed, listing=listing))
    return targets, skipped


def _should_fast_track_new_card(card: FutggCard) -> bool:
    """Whether a newly discovered card should be pushed straight into
    price/sales collection.

    The app can show any discovered card, but high-rated/current special
    cards are the ones users notice immediately. Leaving them to drift
    through normal cadences creates the "new card exists but has no data"
    experience.
    """
    rarity = (card.rarity or "").lower()
    return (card.rating or 0) >= 80 and "bronze" not in rarity and "silver" not in rarity


async def upsert_card(conn: asyncpg.Connection, card: FutggCard) -> bool:
    data = asdict(card)
    price_tier = classify_price_tier(card)
    sql = """
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
            league_image_url, nation_image_url, gg_rating, gg_rating_updated_at,
            last_seen_at, metadata_updated_at,
            next_price_due_at, metadata_warnings
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
            $19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,
            $35,$36,$37,$38,$39,$40,$41,$42,$43,$44,$45,$46,$47,$48,$49,$50,$51,
            CASE WHEN $51::numeric IS NULL THEN NULL ELSE NOW() END,
            NOW(),NOW(),NOW(),$52
        )
        ON CONFLICT (game_year, source_card_id) DO UPDATE SET
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
            gg_rating = COALESCE(EXCLUDED.gg_rating, futgg_players.gg_rating),
            gg_rating_updated_at = CASE
                WHEN EXCLUDED.gg_rating IS NOT NULL THEN NOW()
                ELSE futgg_players.gg_rating_updated_at
            END,
            is_active = TRUE,
            last_seen_at = NOW(),
            metadata_updated_at = NOW(),
            metadata_warnings = EXCLUDED.metadata_warnings
        RETURNING (xmax = 0) AS inserted
        """
    args = (
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
        data["gg_rating"],
        data["parse_warnings"],
    )
    if hasattr(conn, "fetchrow"):
        row = await conn.fetchrow(sql, *args)
        inserted = bool(dict(row).get("inserted")) if row is not None else False
    else:
        # Lightweight test doubles only need to capture the SQL text.
        await conn.execute(sql, *args)
        inserted = False
    if inserted and _should_fast_track_new_card(card):
        await conn.execute(
            """
            INSERT INTO futgg_sales_priority_requests (card_id, requested_by)
            VALUES ($1, 'new_card_discovery')
            """,
            card.source_card_id,
        )
    return bool(inserted)


async def _fetch_worker(
    worker_id: int,
    page,
    pool: asyncpg.Pool,
    queue: "asyncio.Queue[tuple[int, FetchTarget]]",
    total: int,
    stats: dict[str, int],
    breaker: CircuitBreaker,
) -> None:
    while True:
        if breaker.tripped:
            log.warning("worker=%d stopping: circuit breaker tripped (%s)", worker_id, breaker.trip_reason)
            return
        try:
            index, target = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        url, existed, listing = target.url, target.existed, target.listing

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
            # FUT.GG can redirect an unknown card to another version.
            # Never overwrite a placeholder with the wrong version's data.
            requested = CARD_URL_RE.match(urlparse(url).path)
            landed = CARD_URL_RE.match(urlparse(page.url).path)
            if requested and (not landed or
                requested.group("card_id") != landed.group("card_id") or
                requested.group("game_year") != landed.group("game_year")):
                raise RuntimeError(f"FUT.GG redirected card to another version: {page.url}")
            card = parse_futgg_card(await page.content(), url)
            if requested and int(card.source_card_id) != int(requested.group("card_id")):
                raise RuntimeError(f"Card mismatch: expected {requested.group('card_id')}, got {card.source_card_id}")
            if listing.image_url:
                # The composited full-card image only exists on the
                # listing grid tile, never on the card's own detail page
                # (confirmed live - see collect_listing_urls'/
                # _read_card_hrefs' docstrings) - parse_futgg_card() can
                # never set this itself, so apply what the listing crawl
                # already captured for this URL.
                card.card_image_url = listing.image_url
            card.gg_rating = listing.gg_rating
            async with pool.acquire() as conn:
                await upsert_card(conn, card)
            breaker.record_success()
            stats["updated" if existed else "new"] += 1
            log.info(
                "[%d/%d] worker=%d upserted id=%s name=%r rating=%s gg_rating=%s rarity=%r tier=%s",
                index, total, worker_id, card.source_card_id, card.name, card.rating,
                card.gg_rating, card.rarity, classify_price_tier(card),
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
    mode = "backfill_release_metadata" if BACKFILL_RELEASE_METADATA else ("full_scan" if FULL_SCAN else "daily_new")
    log.info(
        "crawl_once() starting mode=%s game_year=%s backfill_game_year=%s full_scan=%s refresh_existing=%s",
        mode, GAME_YEAR, BACKFILL_GAME_YEAR or "-", FULL_SCAN, REFRESH_EXISTING,
    )
    if CONFIGURATION_ERROR:
        raise RuntimeError(CONFIGURATION_ERROR)

    # Retried: this is the worker's first statement, and an unguarded
    # connect here turned a one-second TLS blip into a dead worker.
    lock_conn = await connect_with_retry(DATABASE_URL)
    got_lock = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", OVERLAP_LOCK_KEY)
    if not got_lock:
        holders = await lock_conn.fetch(
            """
            SELECT a.pid, a.application_name, a.client_addr, a.state,
                   now() - a.query_start AS query_age,
                   left(a.query, 180) AS query
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE l.locktype = 'advisory'
              AND (
                    l.objid = $1
                    OR ((l.classid::bigint << 32) + l.objid::bigint) = $1
                  )
            ORDER BY a.query_start NULLS LAST
            """,
            OVERLAP_LOCK_KEY,
        )
        if holders:
            log.info(
                "Previous FUT.GG player sync still running; skipping. lock_key=%s holders=%s",
                OVERLAP_LOCK_KEY,
                [
                    {
                        "pid": row["pid"],
                        "app": row["application_name"],
                        "client": str(row["client_addr"]) if row["client_addr"] else None,
                        "state": row["state"],
                        "query_age": str(row["query_age"]),
                        "query": row["query"],
                    }
                    for row in holders
                ],
            )
        else:
            log.info(
                "Previous FUT.GG player sync still running; skipping. lock_key=%s holder_not_visible",
                OVERLAP_LOCK_KEY,
            )
        await lock_conn.close()
        return

    pool = await create_pool_with_retry(
        DATABASE_URL, min_size=1, max_size=PLAYER_CONCURRENCY + 2
    )
    playwright = browser = context = None
    stats: dict[str, int] = {
        "discovered": 0, "new": 0, "updated": 0, "failed": 0,
        "skipped_existing": 0, "gg_ratings_found": 0,
        "gg_ratings_invalid": 0, "gg_rows_updated": 0,
    }
    breaker = CircuitBreaker(CIRCUIT_BREAKER_THRESHOLD)
    try:
        async with pool.acquire() as conn:
            await ensure_schema(conn)

        async with Stealth().use_async(async_playwright()) as patched_playwright:
            playwright = patched_playwright
            browser = await playwright.chromium.launch(
                headless=HEADLESS,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
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
                timezone_id=BROWSER_TIMEZONE,
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
                # ~0.7s per card measured against production (20 cards in
                # 14s at concurrency=3). An estimate based on REQUEST_DELAY
                # alone was ~6x optimistic because it ignored page fetch and
                # render time, which dominate.
                est_seconds = (len(backfill_rows) * 0.7) / max(PLAYER_CONCURRENCY, 1) * 3
                log.info(
                    "BACKFILL MODE: %d card(s) still need release metadata%s; "
                    "processing up to %d this run (~%.1f min at concurrency=%d delay=%.2fs). "
                    "Re-run to continue - the query is resumable and idempotent.",
                    remaining,
                    f" (game_year={BACKFILL_GAME_YEAR})" if BACKFILL_GAME_YEAR else "",
                    len(backfill_rows), est_seconds / 60.0, PLAYER_CONCURRENCY, REQUEST_DELAY,
                )
                urls = {
                    url: ListingCardMetadata(image_url=image)
                    for url, image in backfill_rows
                }
                stats["discovered"] = len(urls)
                known = set()          # force every backfill row to be fetched
                db_total = remaining   # skips the full-scan plausibility check below
            else:
                urls = await collect_listing_urls(listing_page)
                # Known currently released tradeable special: the FUT.GG
                # newest-listing page can be empty even when this detail
                # page is live. This remains subject to exact-card checks
                # in _fetch_worker and is a targeted recovery, not a
                # catalogue-wide manufactured set of player variants.
                if GAME_YEAR == 27:
                    urls.setdefault(
                        "https://www.fut.gg/players/256675-omar-marmoush/27-67365539/",
                        ListingCardMetadata(),
                    )

                # Supplement broken newest listings with FC27-only special-card
                # URLs reconstructed from the bulk feed's incomplete rows.
                async with pool.acquire() as conn:
                    recovered_urls = await recover_placeholder_urls(conn)
                added_recovery = 0
                for recovery_url, recovery_metadata in recovered_urls.items():
                    if recovery_url not in urls:
                        urls[recovery_url] = recovery_metadata
                        added_recovery += 1
                log.info(
                    "Placeholder recovery: candidates=%d added_to_discovery=%d limit=%d",
                    len(recovered_urls), added_recovery, PLACEHOLDER_RECOVERY_LIMIT,
                )
                stats["discovered"] = len(urls)
                stats["gg_ratings_found"] = sum(
                    metadata.gg_rating is not None for metadata in urls.values()
                )
                stats["gg_ratings_invalid"] = sum(
                    metadata.gg_rating_invalid for metadata in urls.values()
                )
                async with pool.acquire() as conn:
                    known = await existing_ids(conn, urls, GAME_YEAR)
                    unresolved = await conn.fetchval(
                        """SELECT count(*) FROM futgg_players
                           WHERE game_year = $1
                             AND (name IS NULL OR rating IS NULL
                                  OR 'feed_only_placeholder' = ANY(metadata_warnings))""",
                        GAME_YEAR,
                    )
                    log.info("FC%d incomplete catalogue rows before enrichment: %d", GAME_YEAR, unresolved)
                    db_total = await conn.fetchval("SELECT COUNT(*) FROM futgg_players")
                    listing_years = {
                        int(match.group("game_year"))
                        for url in urls
                        if (match := CARD_URL_RE.match(urlparse(url).path))
                    }
                    for listing_year in listing_years:
                        result = await update_listing_gg_ratings(
                            conn, urls, listing_year, only_missing=False,
                        )
                        stats["gg_rows_updated"] += result.updated
                    queued_sales = await enqueue_discovered_sales_priority(conn, urls, GAME_YEAR)
                    if queued_sales:
                        log.info(
                            "Queued %d discovered high-value card(s) for sales priority collection",
                            queued_sales,
                        )

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

            queue: "asyncio.Queue[tuple[int, FetchTarget]]" = asyncio.Queue()
            for index, target in enumerate(targets, start=1):
                queue.put_nowait((index, target))

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
        # Every cleanup step is independently fault-tolerant. Before this,
        # a single failure here aborted the rest of the teardown AND
        # crashed the process with exit(1) - reporting failure for a run
        # whose work had already completed successfully.
        #
        # That is not hypothetical: a ~3h backfill (15,834 cards) finished
        # all its upserts, then died on the pg_advisory_unlock below
        # because the long-idle lock connection had been recycled by the
        # pooler. The stats line said success; the exit code said failure.
        for label, close in (
            ("pool", pool.close),
        ):
            if close is None:
                continue
            try:
                await close()
            except Exception:
                log.warning("cleanup: failed to close %s", label, exc_info=True)

        # Advisory locks are SESSION-scoped: Postgres releases this one
        # automatically the moment the connection ends. So an unlock that
        # fails because the connection is already gone has nothing left to
        # do - the lock is released either way. Best-effort, never fatal.
        try:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", OVERLAP_LOCK_KEY)
        except Exception:
            log.info(
                "cleanup: advisory unlock skipped (lock connection already closed - "
                "the session-scoped lock is released automatically)"
            )
        try:
            await lock_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(crawl_once())
    except Exception as exc:
        log.exception("futgg_player_sync failed: %s", exc)
        sys.exit(1)
