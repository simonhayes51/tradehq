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

    r2.fut.gg/26/player-prices-index.v1.<hash>.json    (~67 KB)
    r2.fut.gg/26/player-prices-<platform>-dyn.v1.<hash>.json  (~167 KB)

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
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from db_connect import connect_with_retry, create_pool_with_retry

from futgg_instrumentation import StageTimers
from monitoring import heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("futgg_bulk_price_sync")

SCRIPT_VERSION = "futgg-bulk-price-sync-v1"

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
PLATFORM = os.getenv("FUTGG_BULK_PLATFORM", "ps5")
GAME_YEAR = os.getenv("FUTGG_GAME_YEAR", "26")
INTERVAL_SECONDS = env_int("FUTGG_BULK_INTERVAL_SECONDS", 300, minimum=60)
HEADLESS = env_bool("PLAYWRIGHT_HEADLESS", True)
TIMEOUT_MS = env_int("PLAYWRIGHT_TIMEOUT_MS", 45000, minimum=5000)
HYDRATION_WAIT_MS = env_int("FUTGG_BULK_HYDRATION_WAIT_MS", 6000, minimum=1000)
#: Below this coverage of our own catalogue, refuse to write. A sudden
#: collapse means the id space or the feed changed, and writing anyway
#: would overwrite good prices with nothing.
MIN_COVERAGE_PCT = float(os.getenv("FUTGG_BULK_MIN_COVERAGE_PCT", "50"))

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
#: bin_market_signal reads at most a 7-day window, so 14 days is already
#: double what anything consumes. Without a bound the table simply grows
#: forever, and futgg_market_snapshot's latest_bin CTE does a DISTINCT ON
#: over ALL of it every refresh - so an unbounded table makes every
#: snapshot refresh progressively slower for no benefit whatsoever.
BIN_HISTORY_RETENTION_DAYS = env_int("FUTGG_BIN_HISTORY_RETENTION_DAYS", 14, minimum=0)
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
_last_written: dict[int, tuple[int, datetime]] = {}
_last_written_seeded = False

#: A stable, operator-maintained "known good" FUT.GG player URL to fall
#: back to when the normal PLAYER_URL stops yielding the feed - e.g.
#: https://www.fut.gg/players/245037-eric-garcia/26-84131117/, confirmed
#: working in production. Not hardcoded: the worker refuses to guess a
#: URL on your behalf, so automatic recovery is a no-op (logged clearly,
#: not silently skipped) until this is set.
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

INDEX_MARKER = "player-prices-index"
DYN_MARKER = f"player-prices-{PLATFORM}-dyn"


async def seed_last_written(pool) -> int:
    """One-off: load the most recent observation per card.

    Deliberately once per process, at startup. This is the same
    DISTINCT ON shape futgg_market_snapshot uses and it is not cheap on a
    large table - running it every cycle to decide what to skip would
    cost more than the writes it saves.
    """
    global _last_written_seeded
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (source_card_id)
                   source_card_id, lowest_bin, captured_at
            FROM futgg_bin_history
            WHERE captured_at >= now() - make_interval(days => 2)
            ORDER BY source_card_id, captured_at DESC
            """
        )
    for row in rows:
        _last_written[int(row["source_card_id"])] = (
            int(row["lowest_bin"]), row["captured_at"],
        )
    _last_written_seeded = True
    return len(rows)


def select_rows_to_write(
    candidates: list[tuple[int, int]], now: datetime,
) -> tuple[list[tuple[int, int, datetime]], int, int]:
    """Filter (card_id, price) pairs down to what actually needs writing.

    Returns (records, changed, heartbeat) so the caller can report how
    much was skipped and why. A card is written when its price differs
    from what we last wrote, or when its last write is older than the
    heartbeat - the latter keeping observation counts and persistence
    meaningful for cards whose price simply is not moving.
    """
    if not CHANGE_ONLY_WRITES:
        return [(cid, price, now) for cid, price in candidates], len(candidates), 0

    records: list[tuple[int, int, datetime]] = []
    changed = heartbeat = 0
    for card_id, price in candidates:
        previous = _last_written.get(card_id)
        if previous is None:
            records.append((card_id, price, now))
            changed += 1
        elif previous[0] != price:
            records.append((card_id, price, now))
            changed += 1
        elif previous[1] <= now - timedelta(minutes=_heartbeat_minutes_for(card_id)):
            records.append((card_id, price, now))
            heartbeat += 1
        else:
            continue
        _last_written[card_id] = (price, now)
    return records, changed, heartbeat


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
        for _ in range(max_chunks):
            status = await conn.execute(
                """
                DELETE FROM futgg_bin_history
                WHERE id IN (
                    SELECT id FROM futgg_bin_history
                    WHERE captured_at < now() - make_interval(days => $1)
                    LIMIT $2
                )
                """,
                BIN_HISTORY_RETENTION_DAYS, PRUNE_BATCH_ROWS,
            )
            # asyncpg returns the command tag, e.g. "DELETE 50000".
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


async def harvest_feed(page, url: str) -> dict[str, Any]:
    """Load `url` and read the bulk feed off the wire.

    Not tied to PLAYER_URL - used both for the normal cycle (against the
    resolved PLAYER_URL) and for rediscovery (against FUTGG_DISCOVER_URL),
    on the same warm page/context either way."""
    captured: dict[str, Any] = {}
    pending: list[asyncio.Task] = []

    async def read(response, key: str) -> None:
        try:
            body = await response.body()
            captured[key] = json.loads(body)
            captured[f"{key}__url"] = response.url
            captured[f"{key}__bytes"] = len(body)
        except Exception:
            log.warning("could not read %s", key, exc_info=True)

    def on_response(response) -> None:
        if response.status != 200:
            return
        for marker in (INDEX_MARKER, DYN_MARKER):
            # Substring match: the content hash changes on every
            # republish, so an exact URL match would silently stop
            # working within the hour.
            if marker in response.url:
                pending.append(asyncio.ensure_future(read(response, marker)))
                return

    page.on("response", on_response)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    except Exception:
        log.warning("navigation failed for url=%s", url, exc_info=True)
    # The feed is requested during hydration, after domcontentloaded.
    await page.wait_for_timeout(HYDRATION_WAIT_MS)
    if pending:
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=30
            )
        except asyncio.TimeoutError:
            log.warning("timed out reading %d captured responses", len(pending))
    page.remove_listener("response", on_response)
    return captured


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
        dyn = captured.get(DYN_MARKER)
        if index is None or dyn is None:
            log.error(
                "rediscovery FAILED (attempt %d/%d) - FUTGG_DISCOVER_URL=%s "
                "did not yield the feed either (index=%s dyn=%s). Either "
                "FUT.GG changed its asset naming again, or this fallback "
                "URL itself is now stale and needs updating.",
                attempt, max_attempts, DISCOVER_URL,
                index is not None, dyn is not None,
            )
            return None

        log.info(
            "rediscovery SUCCEEDED (attempt %d/%d) - new index asset=%s "
            "(%d B) new dyn asset=%s (%d B)",
            attempt, max_attempts,
            captured.get(f"{INDEX_MARKER}__url", "?").rsplit("/", 1)[-1],
            captured.get(f"{INDEX_MARKER}__bytes", 0),
            captured.get(f"{DYN_MARKER}__url", "?").rsplit("/", 1)[-1],
            captured.get(f"{DYN_MARKER}__bytes", 0),
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
            ORDER BY rating DESC NULLS LAST
            LIMIT 1
            """
        )
    return url or "https://www.fut.gg/players/1114-roberto-baggio/26-1114/"


async def ingest_once(
    pool: asyncpg.Pool,
    page,
    timers: StageTimers,
    shutdown_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    with timers.track("harvest"):
        captured = await harvest_feed(page, PLAYER_URL)

    index = captured.get(INDEX_MARKER)
    dyn = captured.get(DYN_MARKER)
    if index is None or dyn is None:
        log.warning(
            "Feed not captured (index=%s dyn=%s) on PLAYER_URL=%s.",
            index is not None, dyn is not None, PLAYER_URL,
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
                    index = captured.get(INDEX_MARKER)
                    dyn = captured.get(DYN_MARKER)
                    break

        if index is None or dyn is None:
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

    log.info(
        "captured index=%s (%d B) dyn=%s (%d B)",
        captured.get(f"{INDEX_MARKER}__url", "?").rsplit("/", 1)[-1],
        captured.get(f"{INDEX_MARKER}__bytes", 0),
        captured.get(f"{DYN_MARKER}__url", "?").rsplit("/", 1)[-1],
        captured.get(f"{DYN_MARKER}__bytes", 0),
    )

    with timers.track("decode"):
        ids = decode_ids(index)
        prices = dyn.get("p") or []
        statuses = dyn.get("s") or []

    if not ids or len(ids) != len(prices):
        log.error(
            "Feed misaligned: ids=%d prices=%d. Refusing to use it.",
            len(ids), len(prices),
        )
        return {"ok": False}

    by_id = {i: p for i, p in zip(ids, prices)}
    status_by_id = dict(zip(ids, statuses)) if len(statuses) == len(ids) else {}
    log.info("feed decoded: %d ids, range %d..%d", len(ids), min(ids), max(ids))

    # ---- Coverage -----------------------------------------------------
    with timers.track("coverage"):
        async with pool.acquire() as conn:
            ours = await conn.fetch(
                """
                SELECT source_card_id, rating FROM futgg_players
                WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE
                """
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

    # ---- Write --------------------------------------------------------
    now = datetime.now(timezone.utc)
    candidates = [
        (int(r["source_card_id"]), int(by_id[int(r["source_card_id"])]))
        for r in matched
        if by_id.get(int(r["source_card_id"]))
    ]
    # Skip observations that record nothing new. See CHANGE_ONLY_WRITES -
    # 91.9% of writes to this table were repeats of the previous price,
    # and the resulting volume was measurably starving the database.
    records, changed_count, heartbeat_count = select_rows_to_write(candidates, now)
    written_ids = {r[0] for r in records}
    skipped = len(candidates) - len(records)

    # Cards the feed matched but priced at zero: it has told us they have
    # no active market right now. That is an OBSERVATION, and until now it
    # was discarded - the card simply fell out of `records`, got no
    # bin_history row, and had its last_price_status left untouched.
    #
    # The consequence was a silent lie. last_price_status is sticky, so a
    # card that stopped having a market kept advertising however it was
    # last priced, and its BIN aged indefinitely while this worker ran
    # every few minutes. Measured on the special tier: 98 cards still
    # labelled 'bulk_feed' with a median BIN age of 2.9 hours, plus 97
    # more carrying stale scraper statuses for the same reason - all of
    # them cards the feed currently reports at zero.
    #
    # Recording it makes "we checked and there is no market" distinct from
    # "we have not checked", which is the distinction every freshness
    # number downstream was missing.
    no_market_ids = [
        int(r["source_card_id"])
        for r in matched
        if not by_id.get(int(r["source_card_id"]))
    ]
    with timers.track("write"):
        async with pool.acquire() as conn:
            async with conn.transaction():
                # One COPY beats tens of thousands of INSERTs. The whole
                # point of this worker is that the catalogue arrives at
                # once; writing it row-by-row would reintroduce exactly
                # the round-trip cost the per-card path suffered from.
                await conn.copy_records_to_table(
                    "futgg_bin_history",
                    records=records,
                    columns=["source_card_id", "lowest_bin", "captured_at"],
                )
                # price_checked_at covers EVERY card the feed priced this
                # cycle, including the ones whose price was unchanged and
                # therefore got no bin_history row. That distinction is the
                # entire point of the column: with change-only writes,
                # bin_captured_at means "the price last moved", which for a
                # stable card drifts arbitrarily far from "we checked".
                # The engine's freshness gate needs the latter, and reading
                # the former is what produced hours of
                # `evaluated=1344 recommendations=0` in production - a
                # special-tier card is rejected past 20 minutes, while a
                # quiet card's bin_captured_at can legitimately be 29
                # minutes old under the heartbeat schedule alone.
                #
                # last_price_status stays on `records` only. It describes
                # how we obtained the price we hold, and skipping a write
                # obtained nothing new to describe.
                await conn.execute(
                    """
                    UPDATE futgg_players p
                    SET price_updated_at = $2,
                        price_checked_at = $2,
                        last_price_status = 'bulk_feed'
                    WHERE p.source_card_id = ANY($1::bigint[])
                    """,
                    [r[0] for r in records], now,
                )
                skipped_ids = [cid for cid, _ in candidates if cid not in written_ids]
                if skipped_ids:
                    await conn.execute(
                        """
                        UPDATE futgg_players p
                        SET price_checked_at = $2
                        WHERE p.source_card_id = ANY($1::bigint[])
                        """,
                        skipped_ids, now,
                    )
                if no_market_ids:
                    # Deliberately NOT writing a bin_history row here.
                    # lowest_bin is INTEGER NOT NULL, and inventing a 0
                    # would be worse than saying nothing - it would read
                    # as a real price of zero to every consumer. The
                    # observation lands on futgg_players instead, and
                    # current_bin keeps showing the last real price until
                    # the snapshot learns to suppress it (see below).
                    #
                    # price_updated_at is safe to bump: the engine's
                    # price-age gate reads bin_checked_at, not this
                    # column, so a no-market card cannot be made to look
                    # freshly priced by it.
                    #
                    # price_checked_at is deliberately NOT bumped here,
                    # for exactly that reason - it is the column the gate
                    # now reads, and "the feed says this card has no
                    # active listings" is not a confirmation of the price
                    # we still hold. Leaving it alone lets such a card
                    # age out of the gate on its own, which is correct.
                    await conn.execute(
                        """
                        UPDATE futgg_players p
                        SET price_updated_at = $2,
                            last_price_status = 'bulk_no_market'
                        WHERE p.source_card_id = ANY($1::bigint[])
                        """,
                        no_market_ids, now,
                    )
    log.info(
        "WROTE %d prices in one COPY (%d changed, %d heartbeat, %d unchanged and "
        "skipped); %d matched cards have no active market",
        len(records), changed_count, heartbeat_count, skipped, len(no_market_ids),
    )
    return {
        "ok": True, "coverage_pct": coverage_pct, "applied": True,
        "written": len(records), "no_market": len(no_market_ids),
    }


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
    PLAYER_URL = await resolve_player_url(pool)
    log.info(
        "%s starting: platform=%s interval=%ss apply=%s url=%s "
        "discovery_enabled=%s discover_url=%s discovery_retry_count=%d "
        "discovery_failure_backoff=%ds",
        SCRIPT_VERSION, PLATFORM, INTERVAL_SECONDS, APPLY, PLAYER_URL,
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
        seeded = await seed_last_written(pool)
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

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            context = await browser.new_context(user_agent=USER_AGENT, locale="en-GB")
            page = await context.new_page()
            while not shutdown_event.is_set():
                timers = StageTimers()
                started = time.perf_counter()
                cycle_number += 1
                result: dict[str, Any] = {}
                try:
                    result = await ingest_once(pool, page, timers, shutdown_event=shutdown_event)
                except Exception:
                    log.exception("ingest failed")

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
                await heartbeat(
                    pool, "futgg_bulk_price_sync", ok=bool(result.get("ok")),
                    detail=(
                        f"cycle={cycle_number} coverage_pct={result.get('coverage_pct')} "
                        f"written={result.get('written')} no_market={result.get('no_market')} "
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
            log.info("shutting down: closing browser and database pool")
            await browser.close()
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
