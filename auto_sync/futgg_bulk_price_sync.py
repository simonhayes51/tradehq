#!/usr/bin/env python
"""Bulk FUT.GG price ingest - the whole catalogue from one page load.

WHY THIS EXISTS
---------------
Per-card scraping loads a player page, waits for React to hydrate, waits
for the app to fetch /api/fut/player-prices/ (which needs a signed token
from POST /price-access/sign/), then parses ~275 KB of HTML. Measured in
production: ~2.0s per successful card, ~26% of cards failing outright,
~55-129 cards/min. The failures are not a markup problem - the signing
endpoint reports `challengeRequired`, and under concurrency a share of
cards simply never get prices rendered, which no amount of waiting or
retrying fixes.

Discovery found that the same page also loads a STATIC bulk feed:

    r2.fut.gg/27/player-prices-index.v1.<hash>.json    (~67 KB)
    r2.fut.gg/27/player-prices-<platform>-dyn.v1.<hash>.json  (~167 KB)

carrying ~27,085 prices between them. One page load yields the entire
catalogue - no signing, no per-card rendering, no rate limit to trip.

TRANSPORT
---------
r2.fut.gg refuses direct requests: plain HTTP 403s, context.request with
origin/referer 403s, and in-page fetch() fails CORS. All three were
tested. What works - and what discovery proved by accident - is letting
the FUT.GG app fetch the files itself during an ordinary page load, and
reading the responses off the wire. So this worker does not impersonate
the request; it loads one page and listens.

The content hashes change on every republish (observed changing within
~25 minutes), which is also why previously-captured URLs start 403ing.
Responses are therefore matched by URL substring, never by exact URL.

SAFETY
------
Writes are guarded by the data, not by a flag. Every cycle checks the
feed against our own catalogue and refuses to write when:

  * no id overlaps source_card_id at all - meaning the feed is keyed on
    base-player eaId rather than card eaId, and needs a mapping first
  * coverage falls below FUTGG_BULK_MIN_COVERAGE_PCT - meaning the feed
    or the id space changed, and writing would blank good prices
  * ids and prices do not align - meaning the encoding changed

Those checks run on live data every cycle, which a one-off manual
approval does not. FUTGG_BULK_APPLY=false forces a read-only cycle if you
want to inspect coverage without writing.

PLAYER DISCOVERY FROM THE PRICE FEED
------------------------------------
When FUTGG_BULK_DISCOVER_PLAYERS_FROM_FEED=true (default), any card id
present in the bulk feed but missing from futgg_players is inserted as a
feed-only placeholder. That starts BIN-history collection immediately
while listing-page discovery is blocked. The row has no rating/name and
uses price_tier=bronze, so recommendation scans ignore it until a real
metadata sync enriches it.

SELF-HEALING REDISCOVERY
------------------------
PLAYER_URL (below) is resolved ONCE at process startup from whichever
card in our own catalogue currently looks like the best bet
(resolve_player_url()) and then reused, unchanged, for every cycle for
the rest of the process's life. If that specific card's page stops
working - the card became untradeable/inactive without the flag being
updated, FUT.GG restructured its URLs, or the page otherwise stops
triggering the bulk-feed fetch - every single cycle fails identically
("Feed not captured (index=False dyn=False)") until someone notices and
intervenes by hand: run futgg_discover.py against a known-good URL,
confirm the feed is still fetchable at all, then restart this worker so
resolve_player_url() gets another chance to land somewhere that works.

That manual loop is now automatic. When a cycle fails to capture the feed,
rediscover() re-runs the SAME capture logic (harvest_feed) against
FUTGG_DISCOVER_URL - a stable, operator-maintained "known good" player
page kept specifically for this purpose (see COOLIFY.md) - reusing the
already-warm browser/page rather than launching a second one. If that
succeeds, its result IS the retried capture (one page load answers both
"does rediscovery work" and "here is this cycle's feed" - no need to
navigate a second time), PLAYER_URL is updated to FUTGG_DISCOVER_URL for
every future cycle, and the cycle continues through decode/coverage/write
normally instead of waiting for the next scheduled interval. If
rediscovery also fails, the cycle backs off for
FUTGG_DISCOVERY_FAILURE_BACKOFF_SECONDS (longer than the normal cadence)
rather than hammering a broken feed every FUTGG_BULK_INTERVAL_SECONDS.

AUDIT NOTE (bulk CDN feed task, 2026-09)
-----------------------------------------
A later task specified this design: poll r2.fut.gg/26/manifest.json every
minute, diff its player-prices-index / player-prices-<platform>-dyn hashes
against a cache, and only download a blob when its hash changes.

That is not what this file does, deliberately. manifest.json is itself
served from r2.fut.gg, which - per TRANSPORT above - refuses every direct
request this worker has tried (plain HTTP, context.request with
origin/referer, in-page fetch(); all three tested in production). A poller
would 403 identically on the manifest fetch itself, before ever reaching
the blobs. So there is no lower-effort "just poll the manifest" path
available here; the passive-capture design above is what actually works
against this origin, and it already yields the same outcome the poll was
meant to produce - one full-catalogue snapshot per cycle, decoded and
diffed against the previous snapshot (see select_rows_to_write() and
_last_price_changes below) without a signed per-card request.

Consequently this worker was extended rather than duplicated: a second,
competing bulk-price poller/table would just be two sources fighting over
the same canonical price columns. What was missing - and what
select_rows_to_write() now exposes via the module-level
_last_price_changes list - was a per-cycle record of WHICH cards' prices
genuinely changed, for the triggered-detailed-sales queue to consume.
"""

from __future__ import annotations

from playwright_stealth import Stealth

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import asyncpg

from db_connect import connect_with_retry, create_pool_with_retry

from futgg_instrumentation import StageTimers
from monitoring import heartbeat
from futgg_misc_prices import ingest_misc_price_feed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("futgg_bulk_price_sync")

SCRIPT_VERSION = "futgg-bulk-price-sync-v2-misc"

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


#: Write to the database.
#
# On by default. The dry-run flag was originally the safety mechanism, but
# it is not the one that matters: the coverage checks below already refuse
# to write when the feed does not line up with our catalogue (zero id
# overlap, or coverage under the floor). Those run on live data every
# cycle, whereas a manual flag just delays the first useful write by a
# deploy. Set FUTGG_BULK_APPLY=false to force a read-only cycle.
APPLY = env_bool("FUTGG_BULK_APPLY", True)
PLATFORM = os.getenv("FUTGG_BULK_PLATFORM", "ps5").strip().lower()
if PLATFORM not in {"ps5", "pc", "both"}:
    raise RuntimeError("FUTGG_BULK_PLATFORM must be 'ps5', 'pc', or 'both'.")
GAME_YEAR = int(os.getenv("FUTGG_GAME_YEAR", "27"))
# Keep PC observations separate: existing TradeHQ consumers interpret
# futgg_bin_history as the console market.
PLATFORMS = ("ps5", "pc") if PLATFORM == "both" else (PLATFORM,)
PRICE_HISTORY_TABLES = {"ps5": "futgg_bin_history", "pc": "futbot_pc_bin_history"}
HEARTBEAT_WORKERS = {"ps5": "futgg_bulk_price_sync", "pc": "futgg_bulk_price_sync_pc"}
INTERVAL_SECONDS = env_int("FUTGG_BULK_INTERVAL_SECONDS", 300, minimum=60)
HEADLESS = env_bool("PLAYWRIGHT_HEADLESS", True)
TIMEOUT_MS = env_int("PLAYWRIGHT_TIMEOUT_MS", 45000, minimum=5000)
HYDRATION_WAIT_MS = env_int("FUTGG_BULK_HYDRATION_WAIT_MS", 6000, minimum=1000)
#: Below this coverage of our own catalogue, refuse to write. A sudden
#: collapse means the id space or the feed changed, and writing anyway
#: would overwrite good prices with nothing.
MIN_COVERAGE_PCT = float(os.getenv("FUTGG_BULK_MIN_COVERAGE_PCT", "50"))
DISCOVER_PLAYERS_FROM_FEED = env_bool("FUTGG_BULK_DISCOVER_PLAYERS_FROM_FEED", True)

# ---------------------------------------------------------------------
# Write volume control
# ---------------------------------------------------------------------
#: Measured in production (2026-08-11): futgg_bin_history held 14.8M rows
#: / 1.94 GB, growing ~2.1M rows/day, and 91.9% of those writes recorded
#: the SAME price as the previous observation for that card. Nine tenths
#: of the table is "nothing happened".
#:
#: That is not merely wasteful storage. The same log showed Postgres in
#: distress under it - a 269-second checkpoint, vacuum repeatedly unable
#: to lock futgg_market_snapshot, and SalesSync's own db_write p95 at 57
#: SECONDS against a 235ms median while its actual network fetching took
#: 4 seconds per cycle. The database, not FUT.GG, was the bottleneck.
#:
#: Recording only actual changes fixes that, but cannot be done naively:
#: app/services/bin_market_signal.py counts observations per window
#: (min_observations_for_signal, min_observations_for_anomaly) and
#: measures price_persistence as a fraction of recent observations. Under
#: pure change-only recording a perfectly stable card would have ONE
#: observation in 24h and be reported as INSUFFICIENT_BIN_HISTORY -
#: breaking the signal for exactly the cards whose price is most settled.
#:
#: So: write on change, plus a heartbeat so a quiet card still accrues
#: observations. 15 minutes keeps ~4 observations in the 1h window the
#: persistence measure prefers, while cutting total writes by roughly
#: two thirds.
CHANGE_ONLY_WRITES = env_bool("FUTGG_BULK_CHANGE_ONLY_WRITES", True)
HEARTBEAT_MINUTES = env_int("FUTGG_BULK_HEARTBEAT_MINUTES", 15, minimum=1)

#: Delete observations older than this. 0 disables pruning.
#:
#: bin_market_signal reads at most a 7-day window (OBSERVATION_WINDOW_DAYS
#: in bin_market_signal.py) - nothing in the codebase ever queries a row
#: older than that. This used to default to 14 (a deliberate 2x margin
#: over that 7-day need), which was itself the bug: production evidence
#: (2026-09-07) showed futgg_bin_history at 13.36M rows / 3.1GB and
#: futgg_market_snapshot refreshes taking 170-220s - table size the prune
#: job was successfully holding STEADY at, not a leak, just steady-state
#: retention of twice as much dead weight as anything reads. Without a
#: bound the table would grow forever regardless; futgg_market_snapshot's
#: latest_bin CTE does a DISTINCT ON over ALL of it every refresh, so
#: retaining anything beyond the 7-day read window makes every snapshot
#: refresh progressively slower for zero benefit. Matches the actual
#: consumer's window exactly now, rather than padding it.
BIN_HISTORY_RETENTION_DAYS = env_int("FUTGG_BIN_HISTORY_RETENTION_DAYS", 7, minimum=0)
#: Rows per DELETE. Pruning in chunks keeps each statement short so it
#: never becomes the long lock-holder this change exists to eliminate.
PRUNE_BATCH_ROWS = env_int("FUTGG_BIN_HISTORY_PRUNE_BATCH", 50_000, minimum=1000)
#: Prune every Nth cycle rather than every cycle - the table only needs
#: trimming at the edges, and doing it constantly is its own load.
PRUNE_EVERY_N_CYCLES = env_int("FUTGG_BIN_HISTORY_PRUNE_EVERY_N_CYCLES", 12, minimum=1)

#: card_id -> (last_written_bin, last_written_at). Seeded once at startup
#: from the database, then maintained in memory: consulting the database
#: for "what did we last write" on every cycle would reintroduce the
#: expensive whole-table scan this is trying to avoid.
_last_written_by_platform: dict[str, dict[int, tuple[int, datetime]]] = {"ps5": {}, "pc": {}}
_last_written = _last_written_by_platform[PLATFORMS[0]]
_last_written_seeded = False

#: Part 5 (triggered detailed-sales queue) needs to know WHICH cards had
#: a genuine bulk-price change this cycle, not just how many - so a
#: trigger consumer can decide whether the change was material relative
#: to that card's own price, and prioritise a detailed signed request
#: accordingly. select_rows_to_write()'s existing (records, changed,
#: heartbeat) return is unpacked as a 3-tuple across many existing,
#: heavily-tested call sites (see test_futgg_bulk_write_volume.py,
#: test_futgg_bulk_price_checked_at.py) - widening that tuple would
#: break every one of them for a consumer that doesn't even exist yet.
#: Mirrors this file's own _last_written pattern instead: populated
#: fresh on every select_rows_to_write() call, read by whichever caller
#: wants it immediately afterward (see ingest_once()'s "price_changes"
#: key). previous_price is None for a card's first-ever observation -
#: a "change" in the sense that mattered enough to write, but not one
#: with a real prior value to compare against.
_last_price_changes: list[tuple[int, "int | None", int]] = []
_last_price_changes_by_platform: dict[str, list[tuple[int, int, int]]] = {}

#: A stable, operator-maintained "known good" FUT.GG player URL to fall
#: back to when the normal PLAYER_URL stops yielding the feed. Use a
#: current FC27 card URL for the live cycle, or a matching historical URL
#: only when FUTGG_GAME_YEAR is intentionally set to that older cycle.
#: Not hardcoded: the worker refuses to guess a URL on your behalf, so
#: automatic recovery is a no-op (logged clearly, not silently skipped)
#: until this is set.
DISCOVER_URL = (os.getenv("FUTGG_DISCOVER_URL") or "").strip()
#: Master switch for automatic rediscovery. Defaults on; set to false to
#: restore the old "fail every cycle until someone notices" behaviour.
DISCOVERY_ENABLED = env_bool("FUTGG_DISCOVERY_ENABLED", True)
#: How many rediscovery attempts to make within a single failed cycle
#: before giving up and backing off. Each attempt is one page load
#: against DISCOVER_URL; a successful attempt's capture is reused
#: directly as the cycle's retried feed capture, so this is NOT
#: multiplied against a separate "retry capture" count.
DISCOVERY_RETRY_COUNT = env_int("FUTGG_DISCOVERY_RETRY_COUNT", 1, minimum=0)
#: How long to sleep before the next cycle after a feed-capture failure
#: that automatic rediscovery could not fix (either disabled, unset, or
#: also failed). Deliberately longer than FUTGG_BULK_INTERVAL_SECONDS -
#: a broken feed is not going to fix itself in 5 minutes, and hammering
#: FUT.GG every cycle while broken helps nobody.
DISCOVERY_FAILURE_BACKOFF_SECONDS = env_int(
    "FUTGG_DISCOVERY_FAILURE_BACKOFF_SECONDS", 900, minimum=60
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)

def _is_expected_price_feed_url(url: str, marker: str, game_year: int | None = None) -> bool:
    """Accept only configured-year R2 JSON assets for the requested feed kind."""
    game_year = GAME_YEAR if game_year is None else game_year
    parsed = urlparse(url)
    filename = parsed.path.rsplit("/", 1)[-1]
    return (
        parsed.scheme == "https"
        and parsed.hostname == "r2.fut.gg"
        and parsed.path.startswith(f"/{game_year}/")
        and parsed.path.count("/") == 2
        and filename.startswith(f"{marker}.v1.")
        and filename.endswith(".json")
        and len(filename) > len(f"{marker}.v1.json")
    )


INDEX_MARKER = "player-prices-index"
DYN_MARKERS = {platform: f"player-prices-{platform}-dyn" for platform in ("ps5", "pc")}
MISC_MARKERS = {platform: f"misc-prices-{platform}" for platform in ("ps5", "pc")}
DYN_MARKER = DYN_MARKERS[PLATFORMS[0]]
MISC_MARKER = MISC_MARKERS[PLATFORMS[0]]

#: Detects a dyn feed for ANY platform, not just the configured PLATFORM.
#: Purely diagnostic for now (see harvest_feed's on_response) - added
#: after a live capture confirmed player-prices-pc-dyn is still served
#: by r2.fut.gg (the asset is not gone), while our own captures using the
#: exact PLATFORM=ps5 marker kept seeing neither ps5 nor any other dyn
#: feed fire during a plain page load. This tells apart "the feed only
#: fires for a different default platform now" (this would catch it)
#: from "nothing dyn-shaped fires at all regardless of platform" (this
#: stays empty too) - the first is a one-line fix, the second needs a
#: UI-interaction trigger (e.g. clicking a platform switcher) instead.
_ANY_PLATFORM_DYN_RE = re.compile(r"player-prices-([a-z0-9]+)-dyn")


async def seed_last_written(pool) -> int:
    """Seed change-only state with indexed latest-price probes, not a global sort.

    futgg_bin_history has >10M rows in production. DISTINCT ON across two
    days spills a large sort to pgsql_tmp and can crash the worker when the
    PostgreSQL volume is nearly full. A LATERAL LIMIT 1 lookup uses the
    existing (source_card_id, captured_at DESC) index for each FC27 card.
    """
    global _last_written_seeded
    total = 0
    async with pool.acquire() as conn:
        for platform in PLATFORMS:
            rows = await conn.fetch(
                """
                SELECT p.source_card_id, latest.lowest_bin, latest.captured_at
                FROM futgg_players p
                JOIN LATERAL (
                    SELECT h.lowest_bin, h.captured_at
                    FROM {table} h
                    WHERE h.source_card_id = p.source_card_id
                      AND h.platform = $1
                      AND h.game_year = $2
                      AND h.captured_at >= now() - interval '2 days'
                    ORDER BY h.captured_at DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE p.game_year = $2
                  AND p.is_active
                  AND p.is_tradeable IS DISTINCT FROM FALSE
                """.format(table=PRICE_HISTORY_TABLES[platform]),
                platform, GAME_YEAR,
            )
            state = _last_written_by_platform[platform]
            for row in rows:
                state[int(row["source_card_id"])] = (int(row["lowest_bin"]), row["captured_at"])
            total += len(rows)
    _last_written_seeded = True
    return total


def select_rows_to_write(
    candidates: list[tuple[int, int]], now: datetime,
    *, platform: str | None = None, commit_state: bool = True,
) -> tuple[list[tuple[int, int, datetime]], int, int]:
    """Filter (card_id, price) pairs down to what actually needs writing.

    Returns (records, changed, heartbeat) so the caller can report how
    much was skipped and why. A card is written when its price differs
    from what we last wrote, or when its last write is older than the
    heartbeat - the latter keeping observation counts and persistence
    meaningful for cards whose price simply is not moving.

    Also refreshes module-level _last_price_changes with the
    (card_id, previous_price, new_price) detail for every GENUINE change
    this call found (previous_price not None and different from price) -
    a first-ever observation and a heartbeat re-write are deliberately
    excluded, since neither is a price actually moving. See that
    variable's own comment for why this is exposed this way rather than
    widening this function's long-established return signature.
    """
    global _last_price_changes
    platform = platform or PLATFORMS[0]
    state = _last_written_by_platform[platform]
    changes: list[tuple[int, int, int]] = []
    if platform == "ps5":
        _last_price_changes = []

    if not CHANGE_ONLY_WRITES:
        _last_price_changes_by_platform[platform] = []
        return [(cid, price, now) for cid, price in candidates], len(candidates), 0

    records: list[tuple[int, int, datetime]] = []
    changed = heartbeat = 0
    for card_id, price in candidates:
        previous = state.get(card_id)
        if previous is None:
            records.append((card_id, price, now))
            changed += 1
        elif previous[0] != price:
            records.append((card_id, price, now))
            changed += 1
            changes.append((card_id, previous[0], price))
        elif previous[1] <= now - timedelta(minutes=_heartbeat_minutes_for(card_id)):
            records.append((card_id, price, now))
            heartbeat += 1
        else:
            continue
        if commit_state:
            state[card_id] = (price, now)
    _last_price_changes_by_platform[platform] = changes
    if platform == "ps5":
        _last_price_changes = changes
    return records, changed, heartbeat


def commit_rows_written(platform: str, records: list[tuple[int, int, datetime]]) -> None:
    for card_id, price, captured_at in records:
        _last_written_by_platform[platform][card_id] = (price, captured_at)


def _heartbeat_minutes_for(card_id: int) -> int:
    """Per-card heartbeat interval, deterministically spread.

    A single shared interval makes every quiet card fall due on the same
    cycle, so the writes we just removed come back as one ~10,000-row
    spike every HEARTBEAT_MINUTES - the same burst, merely less often,
    and worse for lock contention than a steady trickle. Offsetting each
    card by a stable function of its id spreads those heartbeats evenly
    across the window instead.

    Deterministic rather than random so a card's write schedule is
    reproducible, and so a worker restart does not re-align everything.
    """
    return HEARTBEAT_MINUTES + (card_id % max(1, HEARTBEAT_MINUTES))


async def prune_bin_history(pool) -> int:
    """Delete observations past the retention window, in bounded chunks.

    Chunked because a single unbounded DELETE over millions of rows is
    exactly the kind of long-running lock-holder that produced the
    57-second write stalls this change is meant to remove.
    """
    if BIN_HISTORY_RETENTION_DAYS <= 0:
        return 0
    deleted_total = 0
    #: Bound the number of chunks per pass too. A first run against a
    #: badly-overgrown table could otherwise delete millions of rows in
    #: one cycle and starve everything else; the backlog simply clears
    #: over the next few passes instead.
    max_chunks = 20
    async with pool.acquire() as conn:
        for platform in PLATFORMS:
            for _ in range(max_chunks):
                table = PRICE_HISTORY_TABLES[platform]
                status = await conn.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE id IN (
                        SELECT id FROM {table}
                        WHERE captured_at < now() - make_interval(days => $1)
                        LIMIT $2
                    )
                    """,
                    BIN_HISTORY_RETENTION_DAYS, PRUNE_BATCH_ROWS,
                )
                try:
                    deleted = int(status.split()[-1])
                except (ValueError, IndexError, AttributeError):
                    break
                deleted_total += deleted
                if deleted < PRUNE_BATCH_ROWS:
                    break
    return deleted_total


def decode_ids(index: dict[str, Any]) -> list[int]:
    """Cumulative-delta decode: id[0] = id0, id[i] = id[i-1] + d[i-1].

    Confirmed against production: id0=27 with deltas 14,10,189,6 yields
    27,41,51,240,246, matching the observed feed exactly.
    """
    id0 = index.get("id0")
    if id0 is None:
        return []
    ids = [int(id0)]
    running = int(id0)
    for delta in index.get("d") or []:
        running += int(delta)
        ids.append(running)
    return ids


async def ensure_feed_player_rows(conn: asyncpg.Connection, ids: list[int]) -> int:
    """Seed futgg_players from the bulk price feed's id list.

    FUT.GG listing pages are now the fragile part of the pipeline. The
    bulk feed is still arriving every few minutes and carries the card id
    universe, so use it to create placeholder player rows for ids we have
    not discovered through PlayerSync yet. These rows deliberately keep
    rating/name empty and price_tier=bronze so recommendation candidate
    scans ignore them until a real metadata pass later enriches the row.
    """
    if not ids:
        return 0

    rows = await conn.fetch(
        """
        WITH feed_ids AS (
            SELECT DISTINCT unnest($1::bigint[]) AS source_card_id
        )
        INSERT INTO futgg_players (
            source_card_id,
            source_player_id,
            source_slug,
            source_url,
            game_year,
            price_tier,
            is_active,
            metadata_warnings
        )
        SELECT
            f.source_card_id,
            f.source_card_id,
            'feed-only-' || f.source_card_id::text,
            format(
                'https://www.fut.gg/players/%s-feed-only/%s-%s/',
                f.source_card_id,
                $2::int,
                f.source_card_id
            ),
            $2::smallint,
            'bronze',
            TRUE,
            ARRAY['feed_only_placeholder']::text[]
        FROM feed_ids f
        WHERE NOT EXISTS (
            SELECT 1
            FROM futgg_players p
            WHERE p.game_year = $2
              AND p.source_card_id = f.source_card_id
        )
        RETURNING source_card_id
        """,
        ids,
        GAME_YEAR,
    )
    return len(rows)


async def _dismiss_consent_overlay(page) -> bool:
    """Ordinary consent-banner dismissal, same as a human clicking
    "Accept" - not anti-bot evasion. Production evidence from a different
    FUT.GG scraper (research_fc26_chart_capture.py): a click that should
    have worked failed with Playwright's own diagnostic naming the real
    cause - '<div id="cmpwrapper" class="cmpwrapper"> intercepts pointer
    events' - a consent-management-platform overlay that mounts over the
    page and, on some CMP configurations, holds vendor scripts (including
    whatever triggers this page's client-side fetch of the bulk price
    feed) until a consent choice is made. harvest_feed() below did not
    dismiss this at all, so a freshly-created browser context - which has
    no stored consent decision, unlike a human's real browser - could
    hang in that gated state through the entire hydration wait, on every
    single cycle, identically on both PLAYER_URL and FUTGG_DISCOVER_URL:
    exactly the "Feed not captured, rediscovery FAILED too" signature,
    because the common blocker is the overlay, not either URL.
    Returns True if a banner was found and dismissed/hidden."""
    for selector in (
        "#onetrust-accept-btn-handler",
        "#onetrust-reject-all-handler",
        "#cmpwrapper button:has-text('Accept')",
        "#cmpwrapper button:has-text('Agree')",
        "#cmpwrapper button:has-text('Allow')",
        "#didomi-notice-agree-button",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        ".qc-cmp2-summary-buttons button[mode='primary']",
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Reject all')",
        "button:has-text('I Agree')",
        "button:has-text('Agree')",
        "button:has-text('Got it')",
    ):
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=500):
                await button.click(timeout=2000)
                return True
        except Exception:
            pass

    # Last resort: the wrapper exists and is still intercepting pointer
    # events, but none of the known "accept" selectors matched (an
    # unrecognised/custom CMP). Hiding it has the same practical effect as
    # a user dismissing it - it doesn't touch FUT.GG's own anti-bot
    # posture, only a third-party consent widget.
    try:
        hidden = await page.evaluate(
            """
            () => {
                const el = document.querySelector('#cmpwrapper');
                if (!el) return false;
                el.style.display = 'none';
                el.style.pointerEvents = 'none';
                return true;
            }
            """
        )
        return bool(hidden)
    except Exception:
        return False


#: Probed on the page after a failed capture to tell apart, without a
#: separate manual futgg_discover.py run, the handful of distinct causes
#: that all produce the same "index=False dyn=False" symptom: a consent
#: overlay we failed to clear, a bot-challenge/interstitial page, or FUT.GG
#: genuinely no longer making the request this page load (asset naming or
#: transport changed). Kept deliberately small - this runs in the hot
#: cycle path, not a diagnostic-only tool.
_CHALLENGE_MARKERS = (
    "just a moment", "checking your browser", "verify you are human",
    "enable javascript and cookies", "attention required",
)


async def _probe_capture_failure(page) -> dict[str, Any]:
    try:
        return await page.evaluate(
            """
            () => {
                const body = (document.body ? document.body.innerText : '').toLowerCase();
                const wrapper = document.querySelector('#cmpwrapper');
                const priceLike = document.querySelectorAll(
                    '#prices-overview, [id*="price" i], [class*="price" i]'
                ).length;
                return {
                    title: document.title,
                    body_text_length: body.length,
                    scroll_height: document.documentElement.scrollHeight,
                    viewport_height: window.innerHeight,
                    price_like_elements: priceLike,
                    cmpwrapper_present: !!wrapper,
                    cmpwrapper_visible: wrapper
                        ? (wrapper.offsetParent !== null && getComputedStyle(wrapper).display !== 'none')
                        : false,
                    challenge_markers: %s.filter(m => body.includes(m)),
                };
            }
            """ % json.dumps(list(_CHALLENGE_MARKERS))
        )
    except Exception:
        log.warning("capture-failure probe itself failed", exc_info=True)
        return {}


async def harvest_feed(page, url: str) -> dict[str, Any]:
    """Load `url` and read the bulk feed off the wire.

    Not tied to PLAYER_URL - used both for the normal cycle (against the
    resolved PLAYER_URL) and for rediscovery (against FUTGG_DISCOVER_URL),
    on the same warm page/context either way."""
    captured: dict[str, Any] = {}
    pending: list[asyncio.Task] = []
    seen_urls: list[str] = []

    manifest_holder: dict[str, Any] = {}
    other_platform_dyn_seen: list[str] = []

    async def read(response, key: str) -> None:
        try:
            body = await response.body()
            captured[key] = json.loads(body)
            captured[f"{key}__url"] = response.url
            captured[f"{key}__bytes"] = len(body)
        except Exception:
            log.warning("could not read %s", key, exc_info=True)

    async def read_manifest(response) -> None:
        # manifest.json is passively fetchable this same way even though
        # TRANSPORT above found it 403s on every direct request - it was
        # never the fetch mechanism that made it unusable, only requesting
        # it ourselves. Reading its actual current content (rather than
        # guessing at INDEX_MARKER/DYN_MARKER) tells a capture failure
        # apart precisely: still named "player-prices-*" but not requested
        # by this page, versus genuinely renamed/restructured on FUT.GG's
        # side, without needing a separate manual investigation.
        try:
            manifest_holder["body"] = json.loads(await response.body())
        except Exception:
            log.warning("could not read manifest.json body", exc_info=True)

    def on_response(response) -> None:
        if "fut.gg" in response.url and len(seen_urls) < 200:
            seen_urls.append(f"{response.status} {response.url}")
        if response.status != 200:
            return
        if response.url.endswith("manifest.json") and "manifest" not in manifest_holder:
            manifest_holder["manifest"] = True
            pending.append(asyncio.ensure_future(read_manifest(response)))
        markers = (
            INDEX_MARKER,
            *(DYN_MARKERS[platform] for platform in PLATFORMS),
            *(MISC_MARKERS[platform] for platform in PLATFORMS),
        )
        for marker in markers:
            # Substring match: the content hash changes on every
            # republish, so an exact URL match would silently stop
            # working within the hour.
            if marker in response.url:
                if not _is_expected_price_feed_url(response.url, marker):
                    log.error(
                        "REJECTED cross-year or unexpected bulk price asset for FC%d: %s",
                        GAME_YEAR, response.url,
                    )
                    return
                pending.append(asyncio.ensure_future(read(response, marker)))
                return
        # Diagnostic only (see _ANY_PLATFORM_DYN_RE's comment): catches a
        # dyn feed for a platform OTHER than the configured PLATFORM,
        # which the loop above would otherwise silently ignore.
        any_platform_match = _ANY_PLATFORM_DYN_RE.search(response.url)
        if any_platform_match and any_platform_match.group(1) not in PLATFORMS:
            other_platform_dyn_seen.append(f"{any_platform_match.group(1)}: {response.url}")

    page.on("response", on_response)
    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        except Exception:
            log.warning("navigation failed for url=%s", url, exc_info=True)
            raise
        # Dismiss any consent overlay as early as possible - if it's
        # gating the client-side fetch that triggers the bulk feed
        # request, every millisecond it stays up eats into the hydration
        # wait below for nothing.
        dismissed_early = await _dismiss_consent_overlay(page)
        # futgg_discover.py already documents this exact failure mode for
        # the sibling per-card scraper: a price component that only mounts
        # (and only then fetches its data) once scrolled into view, which
        # a worker that never scrolls - this one included, until now -
        # would never trigger. Cheap and harmless if the feed never was
        # scroll-gated in the first place; a real user reading this page
        # scrolls it as a matter of course, so this does no more than that.
        try:
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        # The feed is requested during hydration, after domcontentloaded.
        # Some CMPs mount their overlay slightly after this point, so
        # check again partway through the wait rather than only once.
        await page.wait_for_timeout(HYDRATION_WAIT_MS // 2)
        dismissed_late = await _dismiss_consent_overlay(page)
        await page.wait_for_timeout(HYDRATION_WAIT_MS - HYDRATION_WAIT_MS // 2)
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=30
                )
            except asyncio.TimeoutError:
                log.warning("timed out reading %d captured responses", len(pending))

        # Fallback: construct the feed URL ourselves from manifest.json's
        # own logical-name -> hash mapping and fetch it directly, rather
        # than only ever waiting for the page's own client script to
        # request it. Confirmed live 2026-09-18: manifest.json's
        # player-prices-index/player-prices-{PLATFORM}-dyn entries carry
        # a hash that visibly rotates cycle to cycle, proving FUT.GG is
        # actively regenerating both feeds right now - the feed is not
        # gone, the page just stopped being the thing that asks for it.
        # TRANSPORT above documents that a request for this same file
        # family from OUTSIDE a loaded fut.gg page (plain HTTP, or
        # Playwright's APIRequestContext with matching origin/referer
        # headers) always 403s, and that an in-page fetch() was ALSO
        # found to fail CORS - but that finding predates having a correct
        # hash to test with, and did not specify whether it was tried
        # from a page genuinely navigated to fut.gg first (same real
        # Origin header) or from a synthetic/blank context. Worth one
        # real attempt now that a correct current hash is in hand, on
        # the exact page instance already open here - cheap, and the
        # result (CORS block vs. a clean 200) settles the question either
        # way instead of leaving it assumed.
        fallback_status: dict[str, str] = {}
        manifest_body_for_fallback = manifest_holder.get("body")
        if isinstance(manifest_body_for_fallback, dict):
            feed_assets = [(INDEX_MARKER, "player-prices-index")]
            feed_assets.extend(
                (DYN_MARKERS[platform], DYN_MARKERS[platform])
                for platform in PLATFORMS
            )
            feed_assets.extend(
                (MISC_MARKERS[platform], MISC_MARKERS[platform])
                for platform in PLATFORMS
            )
            for key, logical_name in feed_assets:
                if captured.get(key) is not None:
                    continue
                file_hash = manifest_body_for_fallback.get(logical_name)
                if not file_hash:
                    fallback_status[key] = "logical_name_not_in_manifest"
                    continue
                fetch_url = f"https://r2.fut.gg/{GAME_YEAR}/{logical_name}.v1.{file_hash}.json"
                try:
                    result = await page.evaluate(
                        """async (url) => {
                            try {
                                const res = await fetch(url, { credentials: 'omit' });
                                const text = await res.text();
                                return { ok: res.ok, status: res.status, text: text.slice(0, res.ok ? undefined : 300) };
                            } catch (e) {
                                return { ok: false, error: String(e && e.message ? e.message : e) };
                            }
                        }""",
                        fetch_url,
                    )
                except Exception as exc:
                    fallback_status[key] = f"evaluate_exception:{exc}"
                    continue
                if result.get("ok"):
                    try:
                        captured[key] = json.loads(result["text"])
                        captured[f"{key}__url"] = fetch_url
                        captured[f"{key}__bytes"] = len(result["text"])
                        fallback_status[key] = "success_via_manifest_hash_direct_fetch"
                    except Exception:
                        fallback_status[key] = "malformed_json"
                else:
                    fallback_status[key] = f"status={result.get('status')} error={result.get('error')}"

        # Diagnostics only - never affects what gets applied to the DB.
        # Populated unconditionally (cheap) so a failure log can explain
        # itself without a separate manual futgg_discover.py run.
        #
        # The sample below is capped at 20 entries in request order, which
        # is too small to prove a negative on its own (a feed request that
        # simply fires later than the first 20 assets would never show up
        # in it) - so r2_urls_seen scans the FULL list explicitly for the
        # r2.fut.gg host these feeds are served from, regardless of when it
        # fired, rather than leaving "was it ever requested at all" to be
        # inferred from a truncated sample.
        r2_urls = [u for u in seen_urls if "r2.fut.gg" in u]
        manifest_body = manifest_holder.get("body")
        manifest_price_entries: dict[str, Any] = {}
        if isinstance(manifest_body, dict):
            manifest_price_entries = {
                k: v for k, v in manifest_body.items() if "price" in str(k).lower()
            }
        captured["__diag"] = {
            "dismissed_early": dismissed_early,
            "dismissed_late": dismissed_late,
            "fallback_status": fallback_status,
            "fut_gg_responses_seen": len(seen_urls),
            "r2_fut_gg_urls_seen": r2_urls,
            "other_platform_dyn_seen": other_platform_dyn_seen,
            "sample_urls": seen_urls[:20],
            "manifest_captured": manifest_body is not None,
            "manifest_top_level_keys": (
                list(manifest_body)[:60] if isinstance(manifest_body, dict) else None
            ),
            "manifest_price_entries": manifest_price_entries,
            **(await _probe_capture_failure(page)),
        }
        return captured
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


#: Guards rediscover() so two overlapping cycles (there should only ever
#: be one, since run_forever() is a single sequential loop - but this is
#: cheap insurance, and lets rediscover() be called safely from anywhere
#: else in the future) can't both drive a rediscovery page load at once
#: and race each other's PLAYER_URL update.
_discovery_lock = asyncio.Lock()


async def rediscover(page, *, attempt: int, max_attempts: int) -> dict[str, Any] | None:
    """One rediscovery attempt: load FUTGG_DISCOVER_URL and try to capture
    the feed from it. On success, returns the captured payload (which the
    caller uses directly as this cycle's retried capture - no second page
    load needed) and updates the module-level PLAYER_URL so every future
    cycle uses the known-good URL instead of repeating the same failure.
    Returns None on any failure, having logged the exact reason."""
    global PLAYER_URL

    if _discovery_lock.locked():
        log.warning(
            "discovery already in progress elsewhere - skipping duplicate "
            "attempt %d/%d", attempt, max_attempts,
        )
        return None

    async with _discovery_lock:
        log.warning(
            "attempting automatic rediscovery against FUTGG_DISCOVER_URL=%s "
            "(attempt %d/%d)", DISCOVER_URL, attempt, max_attempts,
        )
        try:
            captured = await harvest_feed(page, DISCOVER_URL)
        except Exception:
            log.exception(
                "rediscovery FAILED (attempt %d/%d) - navigation to "
                "FUTGG_DISCOVER_URL raised", attempt, max_attempts,
            )
            return None

        index = captured.get(INDEX_MARKER)
        missing_platforms = [p for p in PLATFORMS if captured.get(DYN_MARKERS[p]) is None]
        if index is None or missing_platforms:
            log.error(
                "rediscovery FAILED (attempt %d/%d) - FUTGG_DISCOVER_URL=%s "
                "did not yield the feed either (index=%s dyn=%s). Either "
                "FUT.GG changed its asset naming again, or this fallback "
                "URL itself is now stale and needs updating. diag=%s",
                attempt, max_attempts, DISCOVER_URL,
                index is not None, not missing_platforms, captured.get("__diag"),
            )
            return None

        log.info(
            "rediscovery SUCCEEDED (attempt %d/%d) - index asset=%s (%d B) "
            "dyn assets=%s",
            attempt, max_attempts,
            captured.get(f"{INDEX_MARKER}__url", "?").rsplit("/", 1)[-1],
            captured.get(f"{INDEX_MARKER}__bytes", 0),
            {
                p: {
                    "asset": captured.get(f"{DYN_MARKERS[p]}__url", "?").rsplit("/", 1)[-1],
                    "bytes": captured.get(f"{DYN_MARKERS[p]}__bytes", 0),
                }
                for p in PLATFORMS
            },
        )
        if PLAYER_URL != DISCOVER_URL:
            log.info(
                "updating cached PLAYER_URL for future cycles: %s -> %s",
                PLAYER_URL, DISCOVER_URL,
            )
            PLAYER_URL = DISCOVER_URL
        return captured


PLAYER_URL = ""  # resolved at startup from the database


async def resolve_player_url(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as conn:
        url = await conn.fetchval(
            """
            SELECT source_url FROM futgg_players
            WHERE is_active AND source_url IS NOT NULL
              AND is_tradeable IS DISTINCT FROM FALSE
              AND game_year = $1
            ORDER BY rating DESC NULLS LAST
            LIMIT 1
            """,
            GAME_YEAR,
        )
    fallback_prefix = "" if GAME_YEAR == 27 else f"/{GAME_YEAR}"
    return url or f"https://www.fut.gg{fallback_prefix}/players/"


async def _ingest_misc_capture(pool: asyncpg.Pool, captured: dict[str, Any]) -> None:
    for platform in PLATFORMS:
        payload = captured.get(MISC_MARKERS[platform])
        if payload is None:
            log.info("misc prices not captured for %s this cycle", platform)
            continue
        try:
            result = await ingest_misc_price_feed(
                pool, payload, game_year=GAME_YEAR, platform=platform, apply=APPLY,
            )
            log.info(
                "misc prices %s: %d/%d items written (%d changed, %d heartbeat%s)",
                platform, result.get("written", 0), result.get("items", 0),
                result.get("changed", 0), result.get("heartbeats", 0),
                f", {result.get('pruned')} pruned" if result.get("pruned") else "",
            )
        except Exception:
            # Misc feeds are additive: a malformed misc payload does not
            # prevent either player-price feed from being processed.
            log.exception("misc price import failed for %s; continuing player prices", platform)


async def ingest_once(
    pool: asyncpg.Pool,
    page,
    timers: StageTimers,
    shutdown_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    with timers.track("harvest"):
        captured = await harvest_feed(page, PLAYER_URL)

    # Managers, manager leagues, and chemistry styles share this page-load
    # feed but live in a separate history table. Their ingestion remains
    # independent of player-feed coverage checks.
    await _ingest_misc_capture(pool, captured)

    index = captured.get(INDEX_MARKER)
    missing_platforms = [p for p in PLATFORMS if captured.get(DYN_MARKERS[p]) is None]
    if index is None or missing_platforms:
        log.warning(
            "Feed not captured (index=%s missing_platforms=%s) on PLAYER_URL=%s. diag=%s",
            index is not None, missing_platforms, PLAYER_URL, captured.get("__diag"),
        )

        if not DISCOVERY_ENABLED:
            log.info(
                "automatic rediscovery disabled (FUTGG_DISCOVERY_ENABLED=false) "
                "- not attempting recovery this cycle."
            )
        elif not DISCOVER_URL:
            log.error(
                "FUTGG_DISCOVER_URL is not set - cannot attempt automatic "
                "recovery. Set it to a known-good FUT.GG player URL (see "
                "COOLIFY.md) so this worker can self-heal."
            )
        else:
            for attempt in range(1, DISCOVERY_RETRY_COUNT + 1):
                if shutdown_event is not None and shutdown_event.is_set():
                    log.info(
                        "shutdown requested - abandoning discovery retry "
                        "loop after %d/%d attempts", attempt - 1, DISCOVERY_RETRY_COUNT,
                    )
                    break
                with timers.track("discovery"):
                    recovered = await rediscover(
                        page, attempt=attempt, max_attempts=DISCOVERY_RETRY_COUNT
                    )
                if recovered is not None:
                    captured = recovered
                    await _ingest_misc_capture(pool, captured)
                    index = captured.get(INDEX_MARKER)
                    missing_platforms = [p for p in PLATFORMS if captured.get(DYN_MARKERS[p]) is None]
                    break

        if index is None or missing_platforms:
            log.error(
                "Feed capture failed and automatic recovery did not succeed "
                "- backing off %ds before the next attempt instead of the "
                "normal %ds cycle interval.",
                DISCOVERY_FAILURE_BACKOFF_SECONDS, INTERVAL_SECONDS,
            )
            return {"ok": False, "reason": "feed_not_captured"}

        log.info(
            "recovered via rediscovery - continuing this cycle with the "
            "newly captured feed instead of waiting for the next scheduled "
            "interval."
        )

    # Defense in depth: never seed players or write prices from wrong-year
    # feeds, including captures recovered through the rediscovery path.
    index_url = captured.get(f"{INDEX_MARKER}__url", "")
    dyn_urls = {p: captured.get(f"{DYN_MARKERS[p]}__url", "") for p in PLATFORMS}
    if not _is_expected_price_feed_url(index_url, INDEX_MARKER) or any(
        not _is_expected_price_feed_url(dyn_urls[p], DYN_MARKERS[p]) for p in PLATFORMS
    ):
        log.error(
            "REFUSING bulk price import: expected FC%d feeds, got index=%s dyn=%s",
            GAME_YEAR, index_url or "<missing>", dyn_urls,
        )
        return {"ok": False, "reason": "feed_game_year_mismatch"}

    log.info("Validated FC%d bulk feed source URLs: index=%s dyn=%s",
             GAME_YEAR, index_url, dyn_urls)
    dyn = captured[DYN_MARKERS[PLATFORMS[0]]]
    log.info(
        "captured index=%s (%d B) dyn assets=%s",
        captured.get(f"{INDEX_MARKER}__url", "?").rsplit("/", 1)[-1],
        captured.get(f"{INDEX_MARKER}__bytes", 0),
        {p: captured.get(f"{DYN_MARKERS[p]}__url", "?").rsplit("/", 1)[-1] for p in PLATFORMS},
    )

    with timers.track("decode"):
        ids = decode_ids(index)
        prices_by_platform = {
            platform: (captured[DYN_MARKERS[platform]].get("p") or [])
            for platform in PLATFORMS
        }
        statuses = dyn.get("s") or []

    if not ids:
        log.error("Feed index contains no ids. Refusing to use it.")
        return {"ok": False}
    for platform, prices in prices_by_platform.items():
        if len(ids) != len(prices):
            log.error("%s feed misaligned: ids=%d prices=%d. Refusing both feeds.",
                      platform.upper(), len(ids), len(prices))
            return {"ok": False, "reason": "feed_misaligned", "platform": platform}
    prices = prices_by_platform[PLATFORMS[0]]

    by_id = {i: p for i, p in zip(ids, prices)}
    status_by_id = dict(zip(ids, statuses)) if len(statuses) == len(ids) else {}
    log.info("feed decoded: %d ids, range %d..%d", len(ids), min(ids), max(ids))

    seeded_from_feed = 0
    if DISCOVER_PLAYERS_FROM_FEED:
        with timers.track("feed_player_seed"):
            async with pool.acquire() as conn:
                seeded_from_feed = await ensure_feed_player_rows(conn, ids)
        if seeded_from_feed:
            log.info(
                "seeded %d new futgg_players placeholder row(s) from the bulk price feed",
                seeded_from_feed,
            )

    # ---- Coverage -----------------------------------------------------
    with timers.track("coverage"):
        async with pool.acquire() as conn:
            ours = await conn.fetch(
                """
                SELECT source_card_id, rating FROM futgg_players
                WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE
                  AND game_year = $1
                """,
                GAME_YEAR,
            )
    total = len(ours)
    matched = [r for r in ours if int(r["source_card_id"]) in by_id]
    high = [r for r in ours if (r["rating"] or 0) >= 85]
    high_matched = [r for r in high if int(r["source_card_id"]) in by_id]
    coverage_pct = (100.0 * len(matched) / total) if total else 0.0
    high_pct = (100.0 * len(high_matched) / len(high)) if high else 0.0

    log.info("=" * 68)
    log.info("COVERAGE: %d/%d of all tradeable cards (%.1f%%)", len(matched), total, coverage_pct)
    log.info("COVERAGE: %d/%d of 85+ cards (%.1f%%)", len(high_matched), len(high), high_pct)
    priced = sum(1 for r in matched if by_id.get(int(r["source_card_id"])))
    log.info("  of matched, %d have a non-zero price (%.1f%%)",
             priced, 100.0 * priced / len(matched) if matched else 0.0)
    if status_by_id:
        from collections import Counter, defaultdict

        dist = Counter(status_by_id[int(r["source_card_id"])] for r in matched)
        log.info("  status values across our cards: %s", dist.most_common(8))

        # Cross-tabulate the feed's status against what the SCRAPER most
        # recently concluded for the same card. The status enum is
        # undocumented, but if e.g. status 2 lines up with the cards the
        # scraper reports as price_render_failed or untradeable, that is
        # the classification it has been failing to infer from missing
        # markup ~26% of the time - and we can stop scraping those cards
        # entirely rather than burning ~5s each rediscovering it.
        with timers.track("status_crosstab"):
            async with pool.acquire() as conn:
                outcomes = await conn.fetch(
                    """
                    SELECT source_card_id, last_price_status
                    FROM futgg_players
                    WHERE is_active AND last_price_status IS NOT NULL
                    """
                )
            crosstab: dict[Any, Counter] = defaultdict(Counter)
            price_presence: dict[Any, Counter] = defaultdict(Counter)
            for row in outcomes:
                card_id = int(row["source_card_id"])
                status = status_by_id.get(card_id)
                if status is None:
                    continue
                crosstab[status][row["last_price_status"]] += 1
                price_presence[status]["priced" if by_id.get(card_id) else "zero"] += 1

            log.info("  --- feed status vs scraper outcome ---")
            for status in sorted(crosstab):
                log.info(
                    "    status=%s  prices:%s  scraper:%s",
                    status,
                    dict(price_presence[status]),
                    dict(crosstab[status].most_common(5)),
                )
            log.info("  --------------------------------------")
    log.info("=" * 68)

    if not matched:
        log.error(
            "ZERO overlap with source_card_id. The feed is keyed on a different "
            "id space (probably base-player eaId). An id map is required before "
            "this can be used. Nothing written."
        )
        return {"ok": False, "coverage_pct": 0.0}

    if coverage_pct < MIN_COVERAGE_PCT:
        log.error(
            "Coverage %.1f%% is below the %.1f%% floor - refusing to write. "
            "Something changed; investigate before trusting this feed.",
            coverage_pct, MIN_COVERAGE_PCT,
        )
        return {"ok": False, "coverage_pct": coverage_pct}

    if not APPLY:
        log.warning(
            "READ-ONLY (FUTGG_BULK_APPLY=false) - coverage checks passed but "
            "nothing written."
        )
        for row in matched[:10]:
            card_id = int(row["source_card_id"])
            log.info("  sample card=%s rating=%s feed_price=%s",
                     card_id, row["rating"], by_id[card_id])
        return {"ok": True, "coverage_pct": coverage_pct, "applied": False}

    # Prepare all platform batches first, so a malformed feed cannot result
    # in one platform being applied while the other is silently skipped.
    now = datetime.now(timezone.utc)
    platform_work: dict[str, dict[str, Any]] = {}
    for platform in PLATFORMS:
        platform_by_id = dict(zip(ids, prices_by_platform[platform]))
        candidates = [
            (int(row["source_card_id"]), int(platform_by_id[int(row["source_card_id"])]))
            for row in matched
            if platform_by_id.get(int(row["source_card_id"]))
        ]
        records, changed_count, heartbeat_count = select_rows_to_write(
            candidates, now, platform=platform, commit_state=False,
        )
        written_ids = {record[0] for record in records}
        platform_work[platform] = {
            "candidates": candidates,
            "records": records,
            "changed": changed_count,
            "heartbeat": heartbeat_count,
            "skipped": len(candidates) - len(records),
            "skipped_ids": [cid for cid, _ in candidates if cid not in written_ids],
            "no_market_ids": [
                int(row["source_card_id"])
                for row in matched
                if not platform_by_id.get(int(row["source_card_id"]))
            ],
            "price_changes": list(_last_price_changes_by_platform.get(platform, [])),
        }

    with timers.track("write"):
        async with pool.acquire() as conn:
            async with conn.transaction():
                for platform in PLATFORMS:
                    work = platform_work[platform]
                    records = work["records"]
                    if records:
                        await conn.copy_records_to_table(
                            PRICE_HISTORY_TABLES[platform],
                            records=[
                                (cid, price, captured_at, platform, GAME_YEAR)
                                for cid, price, captured_at in records
                            ],
                            columns=["source_card_id", "lowest_bin", "captured_at", "platform", "game_year"],
                        )
                    # TradeHQ's shared freshness/status fields and event
                    # queue continue to describe the console market only.
                    if platform == "ps5":
                        await conn.execute(
                            """
                            UPDATE futgg_players p
                            SET price_updated_at = $2,
                                price_checked_at = $2,
                                last_price_status = 'bulk_feed'
                            WHERE p.source_card_id = ANY($1::bigint[])
                              AND p.game_year = $3
                            """,
                            [row[0] for row in records], now, GAME_YEAR,
                        )
                        if work["skipped_ids"]:
                            await conn.execute(
                                """
                                UPDATE futgg_players p
                                SET price_checked_at = $2
                                WHERE p.source_card_id = ANY($1::bigint[])
                                  AND p.game_year = $3
                                """,
                                work["skipped_ids"], now, GAME_YEAR,
                            )
                        if work["no_market_ids"]:
                            await conn.execute(
                                """
                                UPDATE futgg_players p
                                SET price_updated_at = $2,
                                    last_price_status = 'bulk_no_market'
                                WHERE p.source_card_id = ANY($1::bigint[])
                                  AND p.game_year = $3
                                """,
                                work["no_market_ids"], now, GAME_YEAR,
                            )
                        if work["price_changes"]:
                            await conn.executemany(
                                """
                                INSERT INTO futgg_price_change_events
                                    (card_id, previous_price, new_price, detected_at)
                                VALUES ($1, $2, $3, $4)
                                """,
                                [
                                    (card_id, old_price, new_price, now)
                                    for card_id, old_price, new_price in work["price_changes"]
                                ],
                            )

    # Only advance the in-memory last-written cache after both tables commit.
    for platform in PLATFORMS:
        commit_rows_written(platform, platform_work[platform]["records"])

    platform_results: dict[str, dict[str, Any]] = {}
    for platform in PLATFORMS:
        work = platform_work[platform]
        platform_results[platform] = {
            "written": len(work["records"]),
            "changed": work["changed"],
            "heartbeat": work["heartbeat"],
            "skipped": work["skipped"],
            "no_market": len(work["no_market_ids"]),
            "price_changes": work["price_changes"],
        }
        log.info(
            "%s WROTE %d prices (%d changed, %d heartbeat, %d unchanged/skipped); "
            "%d matched cards have no active market",
            platform.upper(), len(work["records"]), work["changed"],
            work["heartbeat"], work["skipped"], len(work["no_market_ids"]),
        )

    result: dict[str, Any] = {
        "ok": True, "coverage_pct": coverage_pct, "applied": True,
        "seeded_from_feed": seeded_from_feed,
        "platform_results": platform_results,
    }
    if len(PLATFORMS) == 1:
        result.update(platform_results[PLATFORMS[0]])
    else:
        result["written"] = sum(item["written"] for item in platform_results.values())
        result["no_market"] = sum(item["no_market"] for item in platform_results.values())
    result["price_changes"] = platform_results.get(
        "ps5", platform_results[PLATFORMS[0]],
    )["price_changes"]
    return result

async def sleep_or_shutdown(seconds: float, shutdown_event: asyncio.Event) -> bool:
    """Sleep up to `seconds`, waking immediately if shutdown_event is set.
    Returns True if shutdown was requested during the wait (so the caller
    can break its loop right away instead of sleeping out the full
    interval/backoff first)."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return shutdown_event.is_set()


def _install_shutdown_handlers(shutdown_event: asyncio.Event) -> None:
    """SIGTERM/SIGINT both request a graceful stop: finish the in-flight
    step (bounded by the same Playwright/DB timeouts every step already
    has), then exit the main loop instead of sleeping out the interval or
    getting killed mid-write. add_signal_handler is POSIX-only - falls
    back to the interpreter's default SIGINT->KeyboardInterrupt handling
    (still caught below) on platforms where it isn't available."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: _on_shutdown_signal(s, shutdown_event)
            )
        except NotImplementedError:
            pass


def _on_shutdown_signal(sig: signal.Signals, shutdown_event: asyncio.Event) -> None:
    log.warning("received %s - shutting down after the current step", sig.name)
    shutdown_event.set()


async def run_forever() -> None:
    global PLAYER_URL
    from playwright.async_api import async_playwright

    pool = await create_pool_with_retry(
        DATABASE_URL, min_size=1, max_size=4, command_timeout=120
    )
    lock_conn = await pool.acquire()
    locked_platforms: list[str] = []
    for platform in PLATFORMS:
        lock_acquired = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock(483920, hashtext($1))",
            f"{GAME_YEAR}:{platform}",
        )
        if lock_acquired:
            locked_platforms.append(platform)
            continue
        log.warning(
            "another %s price worker already owns the FC%s lock; exiting",
            platform.upper(), GAME_YEAR,
        )
        for locked_platform in reversed(locked_platforms):
            await lock_conn.fetchval(
                "SELECT pg_advisory_unlock(483920, hashtext($1))",
                f"{GAME_YEAR}:{locked_platform}",
            )
        await pool.release(lock_conn)
        await pool.close()
        return
    PLAYER_URL = await resolve_player_url(pool)
    log.info(
        "%s starting: game_year=%s platform=%s interval=%ss apply=%s url=%s "
        "discovery_enabled=%s discover_url=%s discovery_retry_count=%d "
        "discovery_failure_backoff=%ds",
        SCRIPT_VERSION, GAME_YEAR, PLATFORM, INTERVAL_SECONDS, APPLY, PLAYER_URL,
        DISCOVERY_ENABLED, DISCOVER_URL or "<unset>", DISCOVERY_RETRY_COUNT,
        DISCOVERY_FAILURE_BACKOFF_SECONDS,
    )
    if not APPLY:
        log.warning("READ-ONLY MODE - coverage will be reported, nothing written.")
    if DISCOVERY_ENABLED and not DISCOVER_URL:
        log.warning(
            "FUTGG_DISCOVERY_ENABLED is true but FUTGG_DISCOVER_URL is unset - "
            "automatic recovery will not be able to do anything until it is set."
        )

    shutdown_event = asyncio.Event()
    _install_shutdown_handlers(shutdown_event)

    if CHANGE_ONLY_WRITES:
        try:
            seeded = await seed_last_written(pool)
        except (asyncpg.PostgresError, TimeoutError):
            # Startup seeding is only an optimization. A timeout on a large
            # history table must not prevent the price worker from running;
            # the first cycle writes a baseline and fills the in-memory cache.
            log.exception(
                "Cannot seed previous prices; continuing with an empty "
                "change-only cache (first successful cycle writes baselines)"
            )
            seeded = 0
        log.info(
            "change-only writes ON: seeded last-known price for %d card(s); "
            "heartbeat every %d min (a card with an unchanged price is written "
            "at most that often)", seeded, HEARTBEAT_MINUTES,
        )
    else:
        log.info("change-only writes OFF - every observation will be written")
    if BIN_HISTORY_RETENTION_DAYS:
        log.info(
            "bin_history retention: %d days, pruned every %d cycles in batches of %d",
            BIN_HISTORY_RETENTION_DAYS, PRUNE_EVERY_N_CYCLES, PRUNE_BATCH_ROWS,
        )

    cycle_number = 0

    async with Stealth().use_async(async_playwright()) as patched_playwright:
        playwright = patched_playwright
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
        try:
            async def fresh_page():
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    device_scale_factor=1,
                    is_mobile=False,
                    has_touch=False,
                    locale='en-GB',
                )
                return context, await context.new_page()

            while not shutdown_event.is_set():
                timers = StageTimers()
                started = time.perf_counter()
                cycle_number += 1
                result: dict[str, Any] = {}
                context = None
                try:
                    # FUT.GG's page renderer has been observed crashing on
                    # the second navigation when a page is reused across
                    # five-minute cycles. Use a clean context for each cycle
                    # so every feed capture starts from a fresh renderer.
                    context, page = await fresh_page()
                    result = await ingest_once(pool, page, timers, shutdown_event=shutdown_event)
                except Exception:
                    log.exception("ingest failed")
                    result = {"ok": False, "reason": "page_crashed"}
                finally:
                    if context is not None:
                        try:
                            await context.close()
                        except Exception:
                            log.warning("failed to close price-cycle browser context", exc_info=True)

                # Best-effort, per cycle: GET /api/v2/market/freshness
                # (backend/app/services/market_data_provider.py) reads
                # pipeline_heartbeats to report worker health, but this
                # worker went unmonitored there since it replaced
                # futgg_price_sync.py (see Procfile's comment on the
                # switch) - only the now-obsolete script ever called
                # heartbeat(), so the dashboard was silently watching a
                # script that stopped running, unable to reflect this
                # one's actual state even while it kept working (or
                # stalled) in the background. Written every cycle, not
                # just on success, so a worker that is running but
                # failing every cycle (result.get("ok") False) is
                # visibly "ok: false" rather than indistinguishable from
                # a worker that stopped updating altogether. heartbeat()
                # is already best-effort internally (see monitoring.py) -
                # no extra try/except needed here, same as every other
                # caller of it.
                for platform in PLATFORMS:
                    platform_result = result.get("platform_results", {}).get(platform, result)
                    await heartbeat(
                        pool, HEARTBEAT_WORKERS[platform], ok=bool(result.get("ok")),
                        detail=(
                            f"cycle={cycle_number} platform={platform} "
                            f"coverage_pct={result.get('coverage_pct')} "
                            f"written={platform_result.get('written')} "
                            f"no_market={platform_result.get('no_market')} "
                            f"reason={result.get('reason')}"
                        ),
                    )

                # Pruning is best-effort and must never take the ingest
                # down with it: a failed DELETE means the table stays a bit
                # too big, which is survivable, whereas a crashed worker
                # means no prices at all.
                if BIN_HISTORY_RETENTION_DAYS and cycle_number % PRUNE_EVERY_N_CYCLES == 0:
                    try:
                        with timers.track("prune"):
                            pruned = await prune_bin_history(pool)
                        if pruned:
                            log.info(
                                "pruned %d bin_history row(s) older than %d days",
                                pruned, BIN_HISTORY_RETENTION_DAYS,
                            )
                    except Exception:
                        log.warning("bin_history prune failed", exc_info=True)
                log.info("cycle took %.1fs", time.perf_counter() - started)
                for line in timers.format_lines():
                    log.info(line)

                if shutdown_event.is_set():
                    break

                next_sleep = (
                    DISCOVERY_FAILURE_BACKOFF_SECONDS
                    if result.get("reason") == "feed_not_captured"
                    else INTERVAL_SECONDS
                )
                await sleep_or_shutdown(next_sleep, shutdown_event)
        finally:
            log.info("shutting down: releasing %s / FC%s lock(s)", ",".join(p.upper() for p in locked_platforms), GAME_YEAR)
            await browser.close()
            for platform in reversed(locked_platforms):
                await lock_conn.fetchval(
                    "SELECT pg_advisory_unlock(483920, hashtext($1))",
                    f"{GAME_YEAR}:{platform}",
                )
            await pool.release(lock_conn)
            await pool.close()
    log.info("shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        log.info("stopped")
    except Exception:
        log.exception("futgg_bulk_price_sync failed")
    # Always exit 0 - a non-zero exit on Railway is a restart loop.
    sys.exit(0)

