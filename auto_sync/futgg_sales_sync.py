#!/usr/bin/env python
"""FUT.GG sales-history sync via the signed JSON endpoint.

WHY THIS REPLACES PAGE SCRAPING
-------------------------------
The bulk feed (futgg_bulk_price_sync.py) now supplies BIN for the whole
catalogue in ~7 seconds, so scraping pages for prices is redundant. What
it does NOT carry is completed sales - and sales are 70% of fair value
and the gate on every signal the engine emits, so they still have to come
from somewhere.

Page scraping was a poor way to get them: a 275 KB render yielding ~50
sales, at ~2.0s per success with ~26% failing outright because the
price-access signing endpoint throttles under concurrency.

The same data is available directly:

    POST /api/fut/price-access/sign/   {"url": "/api/fut/player-prices/26/<eaId>/?platform=ps5"}
      -> {"data": {"url": "...?verify=<token>", "expiresIn": 120}}
    GET  <signed url>                  -> ~25 KB JSON

carrying `completedAuctions` with ~100 sales - twice the depth of the
table scrape - plus exact soldDate timestamps rather than "18 minutes
ago" text that has to be parsed and rounded.

TRANSPORT
---------
Requests are issued by fetch() INSIDE a live fut.gg page. That is the one
transport proven to work (see futgg_bulk_probe.py): same-origin, so no
CORS, with the browser supplying cookies, TLS and sec-fetch-* headers
exactly as the real app does. Crucially the page is loaded ONCE and then
reused - there is no navigation per card, which is the entire cost the
old scraper was paying.

Each batch runs its sign+fetch pairs concurrently inside a single
page.evaluate via Promise.all, so N cards cost one round trip to the
browser rather than N.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from futgg_instrumentation import StageTimers, redact_headers, redact_url, redact_value

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("futgg_sales_sync")

SCRIPT_VERSION = "futgg-sales-sync-v1"

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


def env_float(name: str, default: float, minimum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


GAME_YEAR = os.getenv("FUTGG_GAME_YEAR", "26")
PLATFORM = os.getenv("FUTGG_SALES_PLATFORM", "ps5")
HEADLESS = env_bool("PLAYWRIGHT_HEADLESS", True)
TIMEOUT_MS = env_int("PLAYWRIGHT_TIMEOUT_MS", 45000, minimum=5000)

#: Cards per browser round trip. Each runs sign+fetch concurrently inside
#: the page. Kept modest because the signing endpoint is exactly what
#: throttles - it reports `challengeRequired`, and hammering it is what
#: gave the old scraper a 26% failure rate.
BATCH_SIZE = env_int("FUTGG_SALES_BATCH_SIZE", 8, minimum=1)
#: Pause between batches, to stay under the signing throttle.
BATCH_DELAY = env_float("FUTGG_SALES_BATCH_DELAY", 0.5, minimum=0.0)
CYCLE_SLEEP = env_float("FUTGG_SALES_CYCLE_SLEEP", 5.0, minimum=0.0)
IDLE_SLEEP = env_float("FUTGG_SALES_IDLE_SLEEP", 30.0, minimum=5.0)
#: How many cards to pull per selection pass.
SELECT_LIMIT = env_int("FUTGG_SALES_SELECT_LIMIT", 200, minimum=1)

#: Write to the database. Default on - matches futgg_bulk_price_sync.py's
#: FUTGG_BULK_APPLY convention. Set to false for a dry run (e.g. alongside
#: FUTGG_SALES_DIAGNOSTIC_CARD_ID) where nothing should be written.
APPLY = env_bool("FUTGG_SALES_APPLY", True)

#: One card's eaId - if set, run a single-card diagnostic pass instead of
#: the normal queue-driven loop (see run_diagnostic_card()). Meant for
#: testing inside Coolify without touching the whole queue - combine with
#: FUTGG_SALES_APPLY=false to guarantee no database write either way.
DIAGNOSTIC_CARD_ID = (os.getenv("FUTGG_SALES_DIAGNOSTIC_CARD_ID") or "").strip()

#: A previously-read card whose refresh attempt fails gets retried again
#: soon rather than waiting out its full rating-based interval - but this
#: must never be confused with a successful read (see sales_interval_minutes,
#: always >= 5 minutes and rating-tiered) or applied to a NEVER-read card
#: (which must stay NULL/never_read on failure - see process_batch).
FAILURE_RETRY_BACKOFF_MINUTES = env_int("FUTGG_SALES_FAILURE_RETRY_BACKOFF_MINUTES", 2, minimum=1)

#: Circuit breaker: if a cycle's running failure ratio reaches this once at
#: least CIRCUIT_BREAKER_MIN_SAMPLE cards have been attempted, stop
#: selecting/marking more cards for the rest of the cycle and sleep
#: CIRCUIT_BREAKER_BACKOFF_SECONDS before the next one, instead of grinding
#: through the full SELECT_LIMIT queue against a broken endpoint (the
#: observed production failure: 200 cards/cycle, ~100% failed, every few
#: seconds).
CIRCUIT_BREAKER_THRESHOLD = env_float("FUTGG_SALES_CIRCUIT_BREAKER_THRESHOLD", 0.8, minimum=0.0)
CIRCUIT_BREAKER_MIN_SAMPLE = env_int("FUTGG_SALES_CIRCUIT_BREAKER_MIN_SAMPLE", 20, minimum=1)
CIRCUIT_BREAKER_BACKOFF_SECONDS = env_float(
    "FUTGG_SALES_CIRCUIT_BREAKER_BACKOFF_SECONDS", 300.0, minimum=0.0
)

#: Sales refresh cadence by rating.
#
# Deliberately much slower than the price cadence, for two reasons.
#
# Sales are CUMULATIVE: a sale already captured stays captured, so a
# refresh only adds what happened since the last read. The interval
# therefore controls how quickly new sales enter the window, not whether
# history exists at all. Reading hourly instead of half-hourly costs
# nothing but latency on the newest few sales.
#
# And the time-critical signal has moved. Trend and volatility now come
# from futgg_bin_history, which the bulk feed refreshes every 5 minutes
# for all 10,064 cards. Sales are needed for the fair-value ANCHOR
# (median, trimmed mean, dispersion, liquidity), which moves slowly.
#
# Sized against measured capacity: the previous 30/120/360/1440 spread
# demanded ~133 cards/min sustained, which is what produced 144 HTTP 429s
# in the first live run. Halving each band brings it to ~67/min.
SALES_INTERVALS = {
    "85_plus": env_int("FUTGG_SALES_85_PLUS_INTERVAL_MIN", 60, minimum=5),
    "80_84": env_int("FUTGG_SALES_80_84_INTERVAL_MIN", 240, minimum=10),
    "75_79": env_int("FUTGG_SALES_75_79_INTERVAL_MIN", 720, minimum=30),
    "under_75": env_int("FUTGG_SALES_UNDER_75_INTERVAL_MIN", 2880, minimum=60),
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)


class AdaptiveThrottle:
    """Self-tuning request rate, driven by observed 429s.

    The first live run attempted 200 cards in 15.4s and got 144 HTTP 429s
    on the data fetch - the transport itself is fast (89ms per batch of
    eight), so the only real constraint is FUT.GG's rate limit, and its
    exact threshold is unknown and liable to change.

    Rather than hard-code a guess, back off hard when throttled and ease
    back in when clean. Backing off on BOTH axes matters: a smaller batch
    reduces instantaneous concurrency, while a longer delay reduces
    sustained rate, and 429s can be triggered by either.

    Note what the required throughput actually is: ~3,272 cards at 85+ on
    a 30-minute cadence is ~109 cards/min, and the long tail adds maybe
    another 25. So roughly 2.2 cards/sec sustained is enough. The first
    run was already achieving 3.6/sec of successes - being slower and
    clean is strictly better than fast and mostly rejected, because
    rejected requests still cost quota and risk a harder block.
    """

    def __init__(self, batch_size: int, delay: float) -> None:
        self.batch_size = batch_size
        self.delay = delay
        self.max_batch = batch_size
        self.min_batch = 1
        self.min_delay = delay
        self.max_delay = max(delay * 40, 10.0)
        self._clean_streak = 0

    def record(self, attempted: int, throttled: int) -> None:
        if throttled:
            self._clean_streak = 0
            # Proportional to how badly we overshot: a batch that was
            # entirely rejected deserves a harder cut than one that lost a
            # single request.
            severity = throttled / max(attempted, 1)
            self.delay = min(self.max_delay, max(self.delay, 0.25) * (1.0 + 2.0 * severity))
            if severity > 0.25:
                self.batch_size = max(self.min_batch, self.batch_size - 1)
            return

        self._clean_streak += 1
        # Ease back in slowly, and only after sustained success - the
        # limiter is likely windowed, so one clean batch proves little.
        if self._clean_streak >= 5:
            self._clean_streak = 0
            self.delay = max(self.min_delay, self.delay * 0.8)
            self.batch_size = min(self.max_batch, self.batch_size + 1)

    def describe(self) -> str:
        return f"batch={self.batch_size} delay={self.delay:.2f}s"


def circuit_breaker_tripped(stats: dict[str, Any]) -> bool:
    """True once a cycle's running failure ratio reaches
    CIRCUIT_BREAKER_THRESHOLD, but only after at least
    CIRCUIT_BREAKER_MIN_SAMPLE cards have actually been attempted (item 6)
    - a single unlucky batch of 8 must not trip a 5-minute backoff; the
    production incident this guards against was ~100% failed over 200
    cards, not a blip."""
    attempted = stats["ok"] + stats["failed"]
    if attempted < CIRCUIT_BREAKER_MIN_SAMPLE:
        return False
    return (stats["failed"] / attempted) >= CIRCUIT_BREAKER_THRESHOLD


def sales_interval_minutes(rating: int | None) -> int:
    if rating is None:
        return SALES_INTERVALS["under_75"]
    if rating >= 85:
        return SALES_INTERVALS["85_plus"]
    if rating >= 80:
        return SALES_INTERVALS["80_84"]
    if rating >= 75:
        return SALES_INTERVALS["75_79"]
    return SALES_INTERVALS["under_75"]


# The signing + fetch pair, executed inside the page so it is same-origin
# and carries the browser's own cookies/TLS. Promise.all means a batch of
# N cards costs ONE round trip to the browser instead of N.
#
# Every result also carries a small set of SAFE diagnostic fields (never
# the signed URL itself, never cookies/tokens) so a live failure can be
# root-caused from logs instead of guessed at:
#   signStatus / dataStatus   - HTTP status of each leg
#   dataContentType           - what the data leg actually served (a WAF
#                                block page is usually text/html, not json)
#   expiresIn                 - the signed token's advertised lifetime
#   challengeRequired         - FUT.GG's own "you got a bot challenge" flag
#   signedUrlIsAbsolute / signedUrlOrigin / signedUrlPath / sameOriginAsPage
#                              - whether the signed URL points somewhere
#                                other than the current page's own origin
#                                (the query string, which carries the
#                                verify token, never leaves this scope -
#                                only origin+path are read out of it)
#   signedUrlHasVerifyParam   - sanity check that signing actually
#                                attached a token at all, without ever
#                                exposing its value
#   dataBodyPreview           - ONLY populated when `diagnostic` is true
#                                (the one-card diagnostic mode) - a short
#                                response-body prefix, further redacted by
#                                redact_value() on the Python side before
#                                it's ever logged.
_FETCH_SCRIPT = """
async ({ ids, gameYear, platform, diagnostic }) => {
    const one = async (eaId) => {
        const diag = {};
        try {
            const target = `/api/fut/player-prices/${gameYear}/${eaId}/?platform=${platform}`;
            const signRes = await fetch('/api/fut/price-access/sign/', {
                method: 'POST',
                headers: { 'content-type': 'application/json', 'accept': 'application/json' },
                body: JSON.stringify({ url: target }),
                credentials: 'include',
            });
            diag.signStatus = signRes.status;
            if (!signRes.ok) return { eaId, error: 'sign_' + signRes.status, ...diag };

            const signed = await signRes.json();
            const signedUrl = signed && signed.data && signed.data.url;
            diag.expiresIn = (signed && signed.data && signed.data.expiresIn != null)
                ? signed.data.expiresIn : null;
            diag.challengeRequired = !!(signed && signed.data && signed.data.challengeRequired);
            if (!signedUrl) return { eaId, error: 'no_signed_url', ...diag };

            diag.signedUrlIsAbsolute = /^https?:\\/\\//i.test(signedUrl);
            diag.signedUrlHasVerifyParam = signedUrl.indexOf('verify=') !== -1;
            try {
                const parsed = new URL(signedUrl, location.href);
                diag.signedUrlOrigin = parsed.origin;
                diag.signedUrlPath = parsed.pathname;
                diag.sameOriginAsPage = parsed.origin === location.origin;
            } catch (e) {
                diag.signedUrlOrigin = null;
                diag.signedUrlPath = null;
                diag.sameOriginAsPage = null;
            }

            if (diag.challengeRequired) return { eaId, error: 'challenge_required', ...diag };

            const dataRes = await fetch(signedUrl, {
                headers: { 'accept': 'application/json' },
                credentials: 'include',
            });
            diag.dataStatus = dataRes.status;
            diag.dataContentType = dataRes.headers.get('content-type') || '';

            if (!dataRes.ok) {
                if (diagnostic) {
                    try { diag.dataBodyPreview = (await dataRes.text()).slice(0, 500); } catch (e) {}
                }
                return { eaId, error: 'data_' + dataRes.status, ...diag };
            }

            let payload;
            try {
                payload = await dataRes.json();
            } catch (e) {
                if (diagnostic) {
                    try { diag.dataBodyPreview = (await dataRes.clone().text()).slice(0, 500); } catch (e2) {}
                }
                return { eaId, error: 'malformed_json', ...diag };
            }
            if (!payload || typeof payload !== 'object' || !('data' in payload)) {
                if (diagnostic) diag.dataBodyPreview = JSON.stringify(payload).slice(0, 500);
                return { eaId, error: 'malformed_payload', ...diag };
            }

            const d = payload.data || {};
            const completedAuctions = Array.isArray(d.completedAuctions) ? d.completedAuctions : [];
            return {
                eaId,
                completedAuctions,
                currentPrice: d.currentPrice != null ? d.currentPrice : null,
                ...diag,
            };
        } catch (e) {
            return { eaId, error: 'exception:' + String(e && e.message ? e.message : e), ...diag };
        }
    };
    return await Promise.all(ids.map(one));
}
"""


def _parse_sold_at(value: Any, now: datetime) -> datetime | None:
    """completedAuctions carry an exact timestamp, unlike the scraped
    table's relative 'N minutes ago' text. Accept ISO strings and epoch
    seconds/millis, since the exact shape is unverified."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def build_sale_rows(card_id: int, auctions: list[dict], captured_at: datetime) -> list[tuple]:
    """Map completedAuctions onto futgg_sales_history rows.

    The fingerprint keeps the existing scheme's shape so the unique index
    still dedupes, but is built from the EXACT soldDate rather than a
    rounded approximation - the API gives a real timestamp, so there is no
    need to bucket by minute and guess at collisions.
    """
    rows: list[tuple] = []
    seen: dict[tuple, int] = {}
    for position, auction in enumerate(auctions, start=1):
        if not isinstance(auction, dict):
            continue
        price = auction.get("soldPrice", auction.get("price"))
        sold_at = _parse_sold_at(
            auction.get("soldDate", auction.get("soldAt", auction.get("date"))), captured_at
        )
        if price is None or sold_at is None:
            continue
        try:
            price = int(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        key = (int(sold_at.timestamp()), price)
        occurrence = seen.get(key, 0) + 1
        seen[key] = occurrence
        raw = f"{card_id}|{sold_at.isoformat()}|{price}|{occurrence}"
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        age_seconds = max(0, int((captured_at - sold_at).total_seconds()))
        tax = int(price * 0.05)
        rows.append(
            (
                card_id, price, price, tax, price - tax, sold_at,
                f"{age_seconds // 60} minutes ago", age_seconds,
                position, occurrence, fingerprint, captured_at,
            )
        )
    return rows


_INSERT = """
INSERT INTO futgg_sales_history (
    source_card_id, listed_price, sold_price, ea_tax, net_price,
    approximate_sold_at, source_age_text, source_age_seconds,
    source_row_position, occurrence_index, source_fingerprint, captured_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
ON CONFLICT (source_fingerprint) DO NOTHING
"""


async def select_due(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Pick the next cards to read.

    Cards that have NEVER been read come first, regardless of rating.

    That ordering matters more than it looks. Sorting by rating band first
    starves the tail: the 85+ band alone needs ~55 cards/min at its
    cadence, so if throughput sits anywhere near that, lower-rated cards
    never reach the front of the queue and never get a FIRST read at all -
    they would sit permanently at zero sales, and a card with no sales
    returns insufficient_data forever no matter how fresh its BIN is.

    Backfill-first guarantees every card gets covered once, after which
    NULLs are exhausted and the ordering settles into normal
    band-priority maintenance.
    """
    return await conn.fetch(
        """
        SELECT source_card_id, rating, next_sales_due_at
        FROM futgg_players
        WHERE is_active
          AND is_tradeable IS DISTINCT FROM FALSE
          AND (next_sales_due_at IS NULL OR next_sales_due_at <= NOW())
        ORDER BY
            -- never-read cards first (backfill), best cards within that
            (next_sales_due_at IS NOT NULL),
            CASE WHEN rating >= 85 THEN 0
                 WHEN rating >= 80 THEN 1
                 WHEN rating >= 75 THEN 2
                 ELSE 3 END,
            next_sales_due_at ASC NULLS FIRST,
            rating DESC NULLS LAST
        LIMIT $1
        """,
        SELECT_LIMIT,
    )


async def report_coverage(conn: asyncpg.Connection) -> None:
    """How much of the catalogue actually has sales yet.

    The single number that says whether the intelligence layer can work:
    a card with fewer than MIN_SALES_FOR_SIGNAL sales produces no signal
    at all, however good its price data is.
    """
    row = await conn.fetchrow(
        """
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE s.n > 0) AS any_sales,
            count(*) FILTER (WHERE s.n >= 5) AS enough_sales,
            count(*) FILTER (WHERE p.next_sales_due_at IS NULL) AS never_read
        FROM futgg_players p
        LEFT JOIN LATERAL (
            SELECT count(*) AS n FROM futgg_sales_history h
            WHERE h.source_card_id = p.source_card_id
              AND h.approximate_sold_at >= now() - interval '14 days'
        ) s ON TRUE
        WHERE p.is_active AND p.is_tradeable IS DISTINCT FROM FALSE
        """
    )
    total = int(row["total"] or 0)
    if not total:
        return
    log.info(
        "sales coverage: %d/%d have any sales (%.1f%%) | %d/%d have >=5 and can "
        "produce a signal (%.1f%%) | %d never read",
        row["any_sales"], total, 100.0 * row["any_sales"] / total,
        row["enough_sales"], total, 100.0 * row["enough_sales"] / total,
        row["never_read"],
    )


async def ensure_schema(conn: asyncpg.Connection) -> None:
    # futgg_sales_history is owned by futgg_price_sync.ensure_schema, and
    # this worker is meant to REPLACE that one - so on any database where
    # price sync has been retired (or never ran) the table this worker
    # writes to would not exist. Own it here too; CREATE TABLE IF NOT
    # EXISTS keeps the two definitions safely idempotent while both
    # workers coexist during the changeover.
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS futgg_sales_history (
            id BIGSERIAL PRIMARY KEY,
            source_card_id BIGINT NOT NULL
                REFERENCES futgg_players(source_card_id),
            listed_price INTEGER NOT NULL,
            sold_price INTEGER NOT NULL,
            ea_tax INTEGER NOT NULL,
            net_price INTEGER NOT NULL,
            approximate_sold_at TIMESTAMPTZ NOT NULL,
            source_age_text TEXT NOT NULL,
            source_age_seconds INTEGER NOT NULL,
            source_row_position SMALLINT NOT NULL,
            occurrence_index SMALLINT NOT NULL,
            source_fingerprint TEXT NOT NULL UNIQUE,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Both the snapshot view and the coverage query filter sales by card
    # and recency; without this they degrade to sequential scans once the
    # table passes a few million rows.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_sales_history_card_sold_idx "
        "ON futgg_sales_history (source_card_id, approximate_sold_at DESC)"
    )

    # Sales get their own due-at column: the price cadence is now driven
    # by the bulk feed and is far shorter, so sharing next_price_due_at
    # would make the two workers fight over the same schedule.
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS next_sales_due_at TIMESTAMPTZ"
    )
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS last_sales_status TEXT"
    )
    # Added for incident recovery: without a timestamp of when
    # last_sales_status was actually written, a repair after a bad
    # deploy (like the 2026-08-04 data_403 incident - see COOLIFY.md's
    # "SalesSync incident repair" section) can only scope "which rows
    # were touched during the incident" by reverse-engineering it from
    # next_sales_due_at, which only works for as long as the backoff
    # constant that wrote it stays unchanged and no later write has
    # touched the row since. This column makes any future repair exact.
    await conn.execute(
        "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS last_sales_status_at TIMESTAMPTZ"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS futgg_players_sales_due_idx "
        "ON futgg_players (next_sales_due_at) "
        "WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE"
    )


#: Throttle-worthy error markers - anything that looks like FUT.GG
#: pushing back on request volume/origin (rate limit, WAF/Cloudflare
#: block, bot challenge, upstream failure) rather than a genuine
#: application-level bug (malformed payload, missing signed url). Drives
#: AdaptiveThrottle, which must react to more than just 429s (item 7) - a
#: 403 wall or a sign_5xx means "slow down" exactly as a 429 does.
_THROTTLE_ERROR_MARKERS = ("429", "403", "sign_5", "data_5")


def is_throttle_signal(error: str) -> bool:
    return error == "challenge_required" or any(marker in error for marker in _THROTTLE_ERROR_MARKERS)


#: Cap on how many individual failures get a full diagnostic log line per
#: batch - a 403-everything cycle can fail all 200 cards, and logging
#: each one in full would be pure noise. stats["errors"] already carries
#: the full per-cycle count breakdown; this is just enough samples to see
#: the actual HTTP/shape evidence without flooding the log.
MAX_FAILURE_SAMPLE_LOGS_PER_BATCH = 3


def log_failure_sample(card_id: int, result: dict[str, Any]) -> None:
    """Safe, redacted diagnostic logging for one failed fetch - status
    codes, content-type, origin/shape booleans, and (diagnostic mode
    only) a short, further-redacted body preview. Never the signed URL
    itself (the query string carrying the verify token never leaves the
    browser context - see _FETCH_SCRIPT), never cookies, never a raw
    auth/token value."""
    preview = result.get("dataBodyPreview")
    log.warning(
        "sales fetch failed card=%s error=%s signStatus=%s dataStatus=%s "
        "dataContentType=%r challengeRequired=%s expiresIn=%s "
        "signedUrlAbsolute=%s signedUrlOrigin=%s sameOriginAsPage=%s "
        "hasVerifyParam=%s%s",
        card_id, result.get("error"), result.get("signStatus"), result.get("dataStatus"),
        result.get("dataContentType"), result.get("challengeRequired"), result.get("expiresIn"),
        result.get("signedUrlIsAbsolute"), result.get("signedUrlOrigin"), result.get("sameOriginAsPage"),
        result.get("signedUrlHasVerifyParam"),
        f" bodyPreview={redact_value(preview)!r}" if preview else "",
    )


async def process_batch(
    page, pool: asyncpg.Pool, rows, stats, timers, throttle, diagnostic: bool = False
) -> None:
    ids = [int(r["source_card_id"]) for r in rows]

    with timers.track("fetch_batch"):
        try:
            results = await page.evaluate(
                _FETCH_SCRIPT,
                {"ids": ids, "gameYear": GAME_YEAR, "platform": PLATFORM, "diagnostic": diagnostic},
            )
        except Exception:
            log.warning("batch fetch failed", exc_info=True)
            stats["batch_errors"] += 1
            throttle.record(len(ids), len(ids))
            return

    await apply_batch_results(pool, rows, results, stats, timers, throttle)


async def apply_batch_results(
    pool: asyncpg.Pool, rows, results, stats, timers, throttle
) -> None:
    """The non-network half of a batch: given already-fetched `results`
    (from process_batch's page.evaluate, or a one-card diagnostic run),
    apply the data-integrity rules and write to the database. Split out
    so run_diagnostic_card() can reuse the EXACT same write logic - the
    never-advance-on-failure fix and the FUTGG_SALES_APPLY gate - without
    a second, redundant fetch of the same card."""
    by_id = {int(r["source_card_id"]): r for r in rows}
    captured_at = datetime.now(timezone.utc)

    to_insert: list[tuple] = []
    updates: list[tuple] = []
    throttled = 0
    logged_failures = 0
    for result in results or []:
        card_id = int(result.get("eaId"))
        row = by_id.get(card_id)
        rating = row["rating"] if row is not None else None
        # True only for a card that has NEVER had a successful (or even
        # attempted) read before this batch - see select_due()'s
        # never-read-first ordering and report_coverage()'s never_read
        # count, both keyed off next_sales_due_at IS NULL.
        was_never_read = row is not None and row["next_sales_due_at"] is None
        error = result.get("error")

        if error:
            stats["failed"] += 1
            stats.setdefault("errors", {})
            stats["errors"][error] = stats["errors"].get(error, 0) + 1
            if logged_failures < MAX_FAILURE_SAMPLE_LOGS_PER_BATCH:
                log_failure_sample(card_id, result)
                logged_failures += 1

            if is_throttle_signal(error):
                throttled += 1

            # DATA-INTEGRITY FIX (item 4): a failed fetch of ANY kind
            # (data_403, sign_502, challenge_required, network errors,
            # malformed payloads, ...) must never count as a successful
            # read. A never-read card (next_sales_due_at IS NULL) must
            # stay exactly that - NULL - so it stays first in the
            # backfill queue and the never_read coverage count doesn't
            # fall on a cycle that wrote zero real sales. This is the fix
            # for the production bug: every failed card used to get
            # `next_sales_due_at = NOW() + 5 minutes` regardless of
            # success, which silently converted "never read" into "read
            # (but failed)" - 200 cards/cycle, exactly matching the
            # observed never_read drop.
            #
            # An already-read card whose refresh attempt failed instead
            # gets a short, clearly-distinct retry backoff
            # (FAILURE_RETRY_BACKOFF_MINUTES, always far shorter than any
            # real sales_interval_minutes() tier) so it's retried soon
            # without masquerading as completed coverage - last_sales_status
            # is tagged "error:..." rather than "success" either way.
            if not was_never_read:
                updates.append((card_id, FAILURE_RETRY_BACKOFF_MINUTES, f"error:{error}"))
            continue

        sale_rows = build_sale_rows(card_id, result.get("completedAuctions") or [], captured_at)
        to_insert.extend(sale_rows)
        stats["ok"] += 1
        stats["sales_seen"] += len(sale_rows)
        # A genuinely successful fetch counts as read whether or not it
        # returned any sales this time - zero completedAuctions for a
        # well-formed response is a real observation ("checked, nothing
        # new"), not a failure (item 4).
        updates.append((card_id, sales_interval_minutes(rating), "success"))

    throttle.record(len(rows), throttled)

    if not APPLY:
        log.info(
            "FUTGG_SALES_APPLY=false - not writing to database. Would have "
            "inserted %d sale row(s) and updated %d card(s) (sample: %s)",
            len(to_insert), len(updates), updates[:10],
        )
        return

    with timers.track("db_write"):
        async with pool.acquire() as conn:
            async with conn.transaction():
                if to_insert:
                    # executemany: one round trip for the whole batch
                    # instead of one per sale. The old scraper issued up
                    # to 50 sequential inserts per card.
                    await conn.executemany(_INSERT, to_insert)
                for card_id, minutes, status in updates:
                    await conn.execute(
                        """
                        UPDATE futgg_players
                        SET next_sales_due_at = NOW() + ($2 * INTERVAL '1 minute'),
                            last_sales_status = $3,
                            last_sales_status_at = NOW()
                        WHERE source_card_id = $1
                        """,
                        card_id, minutes, status,
                    )


async def run_forever() -> None:
    from playwright.async_api import async_playwright

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4, command_timeout=120)
    async with pool.acquire() as conn:
        await ensure_schema(conn)
        anchor = await conn.fetchval(
            """
            SELECT source_url FROM futgg_players
            WHERE is_active AND source_url IS NOT NULL
            ORDER BY rating DESC NULLS LAST LIMIT 1
            """
        )
    anchor = anchor or "https://www.fut.gg/players/1114-roberto-baggio/26-1114/"

    log.info(
        "%s starting: platform=%s batch=%d select_limit=%d intervals=%s",
        SCRIPT_VERSION, PLATFORM, BATCH_SIZE, SELECT_LIMIT, SALES_INTERVALS,
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            context = await browser.new_context(user_agent=USER_AGENT, locale="en-GB")
            page = await context.new_page()
            # ONE navigation for the entire process lifetime. Everything
            # after this is fetch() from within the already-loaded page.
            await page.goto(anchor, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            log.info("anchor page loaded: %s", anchor)

            throttle = AdaptiveThrottle(BATCH_SIZE, BATCH_DELAY)

            while True:
                timers = StageTimers()
                stats = {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}
                started = time.perf_counter()

                async with pool.acquire() as conn:
                    due = await select_due(conn)
                    await report_coverage(conn)
                if not due:
                    await asyncio.sleep(IDLE_SLEEP)
                    continue

                offset = 0
                breaker_tripped = False
                while offset < len(due):
                    # Size is read per iteration, so a mid-cycle backoff
                    # takes effect immediately rather than at the next
                    # cycle.
                    size = throttle.batch_size
                    await process_batch(
                        page, pool, due[offset: offset + size], stats, timers, throttle
                    )
                    offset += size

                    if circuit_breaker_tripped(stats):
                        breaker_tripped = True
                        log.warning(
                            "circuit breaker TRIPPED: %d/%d failed (>=%.0f%% threshold) "
                            "after selecting %d/%d cards this cycle - stopping further "
                            "selection/marking, backing off %.0fs instead of grinding "
                            "through the rest of the queue",
                            stats["failed"], stats["ok"] + stats["failed"],
                            CIRCUIT_BREAKER_THRESHOLD * 100, offset, len(due),
                            CIRCUIT_BREAKER_BACKOFF_SECONDS,
                        )
                        break

                    if throttle.delay:
                        await asyncio.sleep(throttle.delay)

                elapsed = max(time.perf_counter() - started, 0.001)
                log.info(
                    "cycle: cards_ok=%d failed=%d sales=%d in %.1fs (%.0f cards/min) "
                    "throttle[%s] errors=%s%s",
                    stats["ok"], stats["failed"], stats["sales_seen"], elapsed,
                    (stats["ok"] / elapsed) * 60, throttle.describe(),
                    stats.get("errors", {}),
                    " CIRCUIT_BREAKER_TRIPPED" if breaker_tripped else "",
                )
                for line in timers.format_lines():
                    log.info(line)
                await asyncio.sleep(
                    CIRCUIT_BREAKER_BACKOFF_SECONDS if breaker_tripped else CYCLE_SLEEP
                )
        finally:
            await browser.close()
            await pool.close()


# =============================================================================
# Network diagnostics (evidence-gathering for the Cloudflare 403 investigation)
# =============================================================================
#
# Attached ONLY inside run_diagnostic_card() - never during the normal
# production loop, which must stay exactly as fast/quiet as it already
# is. Every field below answers one specific question asked of this
# investigation; nothing here is a fix, only evidence. Cookie VALUES,
# header VALUES for sensitive names, and the signed URL's query string
# are never logged in full - see futgg_instrumentation.redact_headers/
# redact_url/redact_value, the same redaction already proven for
# futgg_discover.py's network diagnostics.

_CF_HEADER_NAMES = (
    "cf-ray", "cf-cache-status", "cf-mitigated", "cf-apo-via",
    "server", "cf-request-id", "cf-worker", "cf-polished",
)
_CF_COOKIE_NAMES = ("__cf_bm", "cf_clearance", "__cflb", "__cfruid", "__cfwaitingroom")


def _is_cf_cookie(name: str) -> bool:
    lowered = name.lower()
    return lowered in {c.lower() for c in _CF_COOKIE_NAMES} or lowered.startswith("cf_") or lowered.startswith("__cf")


async def capture_context_diagnostics(context, page) -> dict[str, Any]:
    """One-time snapshot of everything about the browser/context that
    bot-detection could key off, independent of any single request -
    navigator.webdriver, the actual (not just configured) user agent,
    context launch options, and which Cloudflare-shaped cookies exist
    BEFORE any signed request is made."""
    try:
        webdriver_flag = await page.evaluate("() => navigator.webdriver")
    except Exception:
        webdriver_flag = "<page.evaluate failed>"
    try:
        runtime_ua = await page.evaluate("() => navigator.userAgent")
    except Exception:
        runtime_ua = "<page.evaluate failed>"

    cookies = await context.cookies()
    cf_cookies_before = sorted({c["name"] for c in cookies if _is_cf_cookie(c["name"])})

    return {
        "navigator_webdriver": webdriver_flag,
        "runtime_user_agent": runtime_ua,
        "configured_user_agent": USER_AGENT,
        "configured_locale": "en-GB",
        "headless": HEADLESS,
        "launch_args": ["--no-sandbox", "--disable-dev-shm-usage"],
        # Both currently-deployed workers (this one and
        # futgg_bulk_price_sync.py) are grep-confirmed to never call
        # page.route()/context.route() anywhere - no request is ever
        # intercepted, delayed, or rewritten. (The retired, undeployed
        # futgg_price_sync.py DOES use context.route() to block images/
        # fonts for speed - not a factor for either currently-running
        # worker.)
        "route_interception_active": False,
        "fetch_execution_context": "page (Playwright page.evaluate -> CDP Runtime.evaluate), NOT Python/aiohttp",
        "total_cookies_in_context": len(cookies),
        "all_cookie_names_in_context": sorted({c["name"] for c in cookies}),
        "cloudflare_cookies_present_before_request": cf_cookies_before,
    }


def _attach_network_diagnostics(page, label: str = "NETDIAG") -> list[asyncio.Task]:
    """Logs every request/response the page makes from here on: URL,
    method, ALL headers as the browser actually sent/received them
    (request.all_headers()/response.all_headers() - unlike the sync
    request.headers property, these include browser-computed headers
    Playwright doesn't expose any other way, e.g. cookie, sec-fetch-*,
    which is exactly what this investigation needs), Cloudflare-specific
    response headers pulled out separately for visibility, and whether a
    Cookie header was actually attached to that specific request. Returns
    the list of background logging tasks so the caller can await them
    before the page/browser closes."""
    pending: list[asyncio.Task] = []

    def on_request(request) -> None:
        async def _log() -> None:
            try:
                headers = await request.all_headers()
            except Exception:
                log.exception("%s: failed to read request headers for %s", label, redact_url(request.url))
                return
            sec_fetch = {k: v for k, v in headers.items() if k.lower().startswith("sec-fetch-")}
            log.info(
                "%s REQUEST method=%s url=%s resourceType=%s "
                "hasCookieHeader=%s secFetchHeaders=%s headers=%s",
                label, request.method, redact_url(request.url), request.resource_type,
                "cookie" in {k.lower() for k in headers}, sec_fetch, redact_headers(headers),
            )
        pending.append(asyncio.ensure_future(_log()))

    def on_response(response) -> None:
        async def _log() -> None:
            try:
                headers = await response.all_headers()
            except Exception:
                log.exception("%s: failed to read response headers for %s", label, redact_url(response.url))
                return
            cf_headers = {
                k: v for k, v in headers.items()
                if k.lower() in _CF_HEADER_NAMES
            }
            log.info(
                "%s RESPONSE status=%s url=%s contentType=%r cfHeaders=%s headers=%s",
                label, response.status, redact_url(response.url),
                headers.get("content-type"), cf_headers, redact_headers(headers),
            )
        pending.append(asyncio.ensure_future(_log()))

    page.on("request", on_request)
    page.on("response", on_response)
    return pending


async def run_diagnostic_card(card_id: int) -> bool:
    """One-card diagnostic pass (item 9) - test a single known card
    inside Coolify without touching the whole queue:

        FUTGG_SALES_DIAGNOSTIC_CARD_ID=<id>
        FUTGG_SALES_APPLY=false

    Logs full safe diagnostics (see log_failure_sample/_FETCH_SCRIPT's
    diag fields) regardless of outcome, then applies the exact same
    write path a real cycle would (apply_batch_results) - so this mode's
    database behaviour, including the never-advance-on-failure fix, is
    provably the same code as production, not a reimplementation that
    could quietly drift from it. With FUTGG_SALES_APPLY=false (the
    documented pairing above) nothing is written either way. Returns
    True on a successful fetch, False on a failure or an unknown card.
    """
    from playwright.async_api import async_playwright

    log.warning("=" * 72)
    log.warning(
        "FUTGG_SALES_DIAGNOSTIC_CARD_ID=%d - single-card diagnostic mode, apply=%s",
        card_id, APPLY,
    )
    log.warning("=" * 72)

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2, command_timeout=60)
    try:
        async with pool.acquire() as conn:
            await ensure_schema(conn)
            row = await conn.fetchrow(
                """
                SELECT source_card_id, rating, name, next_sales_due_at
                FROM futgg_players WHERE source_card_id = $1
                """,
                card_id,
            )
            anchor = await conn.fetchval(
                """
                SELECT source_url FROM futgg_players
                WHERE is_active AND source_url IS NOT NULL
                ORDER BY rating DESC NULLS LAST LIMIT 1
                """
            )

        if row is None:
            log.error(
                "DIAGNOSTIC: card_id=%d not found in futgg_players - nothing to test",
                card_id,
            )
            return False

        log.info(
            "DIAGNOSTIC: card=%d name=%r rating=%s next_sales_due_at=%s (never_read=%s)",
            card_id, row["name"], row["rating"], row["next_sales_due_at"],
            row["next_sales_due_at"] is None,
        )

        anchor = anchor or "https://www.fut.gg/players/1114-roberto-baggio/26-1114/"

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            try:
                context = await browser.new_context(user_agent=USER_AGENT, locale="en-GB")
                page = await context.new_page()
                await page.goto(anchor, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                log.info("DIAGNOSTIC: anchor page loaded: %s", anchor)

                context_diag = await capture_context_diagnostics(context, page)
                log.info("DIAGNOSTIC: browser/context state: %s", context_diag)

                pending_network_logs = _attach_network_diagnostics(page)
                results = await page.evaluate(
                    _FETCH_SCRIPT,
                    {
                        "ids": [card_id], "gameYear": GAME_YEAR, "platform": PLATFORM,
                        "diagnostic": True,
                    },
                )
                # Let every in-flight request/response logging task
                # finish before the browser (and the CDP connection they
                # depend on) closes underneath them.
                if pending_network_logs:
                    await asyncio.wait_for(
                        asyncio.gather(*pending_network_logs, return_exceptions=True), timeout=15
                    )

                cookies_after = await context.cookies()
                cf_cookies_after = sorted(
                    {c["name"] for c in cookies_after if _is_cf_cookie(c["name"])}
                )
                log.info(
                    "DIAGNOSTIC: cloudflare cookies AFTER request=%s (were %s BEFORE)",
                    cf_cookies_after, context_diag["cloudflare_cookies_present_before_request"],
                )
            finally:
                await browser.close()

        result = (results or [{}])[0]
        error = result.get("error")
        if error:
            log_failure_sample(card_id, result)
            log.error("DIAGNOSTIC: card=%d FAILED: error=%s", card_id, error)
            ok = False
        else:
            sales = result.get("completedAuctions") or []
            log.info(
                "DIAGNOSTIC: card=%d SUCCESS: %d completed auction(s) currentPrice=%s "
                "signStatus=%s dataStatus=%s dataContentType=%r expiresIn=%s "
                "signedUrlAbsolute=%s signedUrlOrigin=%s sameOriginAsPage=%s "
                "hasVerifyParam=%s",
                card_id, len(sales), result.get("currentPrice"),
                result.get("signStatus"), result.get("dataStatus"), result.get("dataContentType"),
                result.get("expiresIn"), result.get("signedUrlIsAbsolute"),
                result.get("signedUrlOrigin"), result.get("sameOriginAsPage"),
                result.get("signedUrlHasVerifyParam"),
            )
            ok = True

        stats = {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}
        throttle = AdaptiveThrottle(1, 0.0)
        await apply_batch_results(pool, [row], results, stats, StageTimers(), throttle)
        log.info(
            "DIAGNOSTIC: would-apply summary: ok=%d failed=%d sales_seen=%d "
            "(FUTGG_SALES_APPLY=%s - %s)",
            stats["ok"], stats["failed"], stats["sales_seen"], APPLY,
            "written to database" if APPLY else "NOT written - dry run",
        )

        log.warning("=" * 72)
        log.warning("DIAGNOSTIC MODE complete for card=%d: ok=%s", card_id, ok)
        log.warning("=" * 72)
        return ok
    finally:
        await pool.close()


async def main() -> bool | None:
    if DIAGNOSTIC_CARD_ID:
        try:
            return await run_diagnostic_card(int(DIAGNOSTIC_CARD_ID))
        except ValueError:
            log.error(
                "FUTGG_SALES_DIAGNOSTIC_CARD_ID=%r is not a valid integer card id",
                DIAGNOSTIC_CARD_ID,
            )
            return False
    await run_forever()
    return None


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
    except KeyboardInterrupt:
        log.info("stopped")
        sys.exit(0)
    except Exception:
        log.exception("futgg_sales_sync failed")
        sys.exit(0 if not DIAGNOSTIC_CARD_ID else 1)
    # run_forever() (result is None) never returns except via
    # KeyboardInterrupt/exception above, handled the same way as every
    # other continuous worker in this repo - always exit 0 so a
    # container-runtime restart policy doesn't treat a clean stop as a
    # crash. The one-card diagnostic mode DOES have a meaningful
    # success/failure result, and exits accordingly so it's scriptable.
    if result is None:
        sys.exit(0)
    sys.exit(0 if result else 1)
