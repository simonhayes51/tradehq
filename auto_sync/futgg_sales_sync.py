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
Production evidence (2026-08-04, Hetzner/Coolify) showed that injecting
fetch() calls against the signed price-access endpoint from an unrelated
anchor page - even though the fetch runs inside the live fut.gg origin -
gets served a Cloudflare JS challenge (`cf-mitigated: challenge`) on the
GET, while the exact same data loads fine when it is requested by a real
player page's own hydration. PriceSync has never hit this because it only
ever passively listens to a page's own organic requests; it never
constructs its own fetch(). This module now supports two transports:

  signed_fetch  - the original approach: one anchor page is loaded once
                  and reused; each batch calls page.evaluate() to run
                  POST /api/fut/price-access/sign/ then GET <signed url>
                  concurrently for N cards via Promise.all. Fast, but is
                  the transport Cloudflare is now seen to challenge.

  page_capture  - navigates to each card's own source_url (the real
                  player page), attaches a page.on("response", ...)
                  listener BEFORE navigation, and passively captures that
                  page's own naturally-triggered request to
                  /api/fut/player-prices/26/<card-id>/ - no injected
                  fetch(), no manual call to the sign endpoint. This is
                  structurally identical to how PriceSync obtains data,
                  and is the fallback when Cloudflare blocks signed_fetch.
                  One navigation per card costs more time than
                  signed_fetch's batched fetch(), so FUTGG_SALES_PAGE_CONCURRENCY
                  is kept conservative (default 2) to avoid tripping
                  Cloudflare further.

  auto (default) - starts on signed_fetch; if the circuit breaker sees a
                  run of challenge/403-shaped failures, the process
                  switches to page_capture for all subsequent work and
                  logs the switch. It does not retry signed_fetch every
                  cycle - only after FUTGG_SALES_SIGNED_REPROBE_MINUTES
                  has passed does it try a small signed_fetch sample
                  again, and only switches back if that sample succeeds.

Set FUTGG_SALES_TRANSPORT=signed_fetch|page_capture|auto to control this
directly; see COOLIFY.md for the recommended production setting and the
one-card diagnostic command for each transport.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
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

# ---------------------------------------------------------------------
# Transport: signed_fetch (the original design) vs page_capture (the
# Cloudflare-evidenced fallback) - see CLOUDFLARE_403_INVESTIGATION.md
# and PAGE_CAPTURE_TRANSPORT.md for the full evidence trail. Confirmed in
# production: signed_fetch's own injected fetch() to the signed data URL
# gets `cf-mitigated: challenge` + HTTP 403, while the SAME url, when
# requested by the player page's OWN hydration (exactly what
# page_capture does instead), succeeds - matching PriceSync's already-
# working passive-capture approach.
_VALID_TRANSPORTS = ("signed_fetch", "page_capture", "auto")
TRANSPORT_MODE = (os.getenv("FUTGG_SALES_TRANSPORT") or "auto").strip().lower()
if TRANSPORT_MODE not in _VALID_TRANSPORTS:
    log.warning(
        "Unknown FUTGG_SALES_TRANSPORT=%r (expected one of %s) - defaulting to auto",
        TRANSPORT_MODE, _VALID_TRANSPORTS,
    )
    TRANSPORT_MODE = "auto"

#: How many player pages to have open (and navigating) at once in
#: page_capture mode. Conservative by design - each one is a real
#: navigation to a real page, the exact pattern PriceSync already proves
#: safe, but concurrency is the one axis that could still look automated
#: at volume. See COOLIFY.md's "page_capture throughput" section for
#: measured cards/min at 1/2/3 and backfill-time estimates.
PAGE_CONCURRENCY = env_int("FUTGG_SALES_PAGE_CONCURRENCY", 2, minimum=1)
#: Bounded timeout for one card's entire navigate+capture attempt.
#: Exceeding this counts as a failed fetch (page_capture_timeout) - it
#: does not hang the worker or the cycle.
PAGE_TIMEOUT_MS = env_int("FUTGG_SALES_PAGE_TIMEOUT_MS", 15000, minimum=2000)
#: How long to wait after domcontentloaded for the page's own hydration
#: to make (and complete) its player-prices request - the same "wait a
#: bit after the minimum navigation event, not full page load" idiom
#: PriceSync's HYDRATION_WAIT_MS already uses successfully.
PAGE_SETTLE_MS = env_int("FUTGG_SALES_PAGE_SETTLE_MS", 4000, minimum=500)
#: In auto mode, once the circuit breaker has forced a switch to
#: page_capture, how long to wait before trying a small signed_fetch
#: probe batch again - deliberately long, so a still-blocked signed
#: transport isn't re-attempted (and re-failed) every cycle. None of the
#: real per-cycle traffic uses signed_fetch again until a probe succeeds.
SIGNED_REPROBE_MINUTES = env_int("FUTGG_SALES_SIGNED_REPROBE_MINUTES", 60, minimum=5)
#: Sample size for a signed_fetch reprobe - small and cheap, not the
#: whole due queue.
SIGNED_REPROBE_SAMPLE = env_int("FUTGG_SALES_SIGNED_REPROBE_SAMPLE", 5, minimum=1)

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


# ---------------------------------------------------------------------
# Transport state (auto mode only)
# ---------------------------------------------------------------------
#
# Mutated only when TRANSPORT_MODE == "auto". Pinned modes
# (signed_fetch/page_capture) never touch this - _active_transport is
# set once at import and never switched. A plain module-level global
# rather than a class: this script already uses the same pattern for
# PriceSync's PLAYER_URL-equivalent state, and there is exactly one
# process-wide transport decision to track, not a collection of them.
_active_transport: str = "page_capture" if TRANSPORT_MODE == "page_capture" else "signed_fetch"
_next_signed_reprobe_at: datetime | None = None


def current_transport() -> str:
    return _active_transport


def switch_to_page_capture(reason: str) -> None:
    """Called when the circuit breaker trips on signed_fetch in auto
    mode (item 4). Schedules the next reprobe SIGNED_REPROBE_MINUTES out
    - deliberately not immediate, so a still-blocked signed transport
    isn't re-attempted (and re-failed) every cycle (item 4: "do not keep
    retrying the blocked signed transport every cycle")."""
    global _active_transport, _next_signed_reprobe_at
    if _active_transport != "page_capture":
        log.warning(
            "TRANSPORT SWITCH: signed_fetch -> page_capture (%s). Next "
            "signed_fetch reprobe in %d minutes.",
            reason, SIGNED_REPROBE_MINUTES,
        )
    _active_transport = "page_capture"
    _next_signed_reprobe_at = datetime.now(timezone.utc) + timedelta(minutes=SIGNED_REPROBE_MINUTES)


def switch_to_signed_fetch(reason: str) -> None:
    global _active_transport, _next_signed_reprobe_at
    if _active_transport != "signed_fetch":
        log.warning("TRANSPORT SWITCH: page_capture -> signed_fetch (%s).", reason)
    _active_transport = "signed_fetch"
    _next_signed_reprobe_at = None


def signed_reprobe_due() -> bool:
    """True once it's time to try a small signed_fetch probe batch again
    while parked on page_capture (auto mode only)."""
    if TRANSPORT_MODE != "auto" or _active_transport != "page_capture":
        return False
    if _next_signed_reprobe_at is None:
        return False
    return datetime.now(timezone.utc) >= _next_signed_reprobe_at


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


# =============================================================================
# page_capture transport - the Cloudflare-evidenced fallback
# =============================================================================
#
# signed_fetch (_FETCH_SCRIPT above) works by INJECTING our own fetch()
# calls for cards the page never actually navigated to - confirmed in
# production to draw a Cloudflare challenge (`cf-mitigated: challenge`,
# HTTP 403) on the signed data URL, even though POST /price-access/sign/
# itself succeeds. page_capture instead does exactly what PriceSync
# already proves works from the same server: navigate to the CARD'S OWN
# player page and passively capture whatever that page's own hydration
# naturally requests - no sign call issued by us, no fetch() injected by
# us, nothing here that isn't also true of a real user viewing that page.
#
# See CLOUDFLARE_403_INVESTIGATION.md for the full evidence trail.

#: Matches .../api/fut/player-prices/<gameYear>/<cardId>/... - anchored
#: with the trailing slash after the id specifically so card 123's
#: response can never substring-match card 1234's URL.
_PLAYER_PRICES_URL_RE = re.compile(r"/api/fut/player-prices/(\d+)/(\d+)/")


def _card_id_from_player_prices_url(url: str) -> int | None:
    """Confirms a captured response actually belongs to the requested
    card (item 3: "confirm the captured response belongs to the exact
    requested card") - parses the real card id out of the URL path
    itself rather than trusting a substring match."""
    match = _PLAYER_PRICES_URL_RE.search(url)
    if not match:
        return None
    try:
        return int(match.group(2))
    except ValueError:
        return None


def _player_prices_url_marker(card_id: int) -> str:
    return f"/api/fut/player-prices/{GAME_YEAR}/{card_id}/"


async def fetch_via_page_capture(
    page, card_id: int, url: str, *, diagnostic: bool = False
) -> dict[str, Any]:
    """Navigate to `url` (the card's OWN player page) and passively
    capture whatever that page's own hydration naturally requests to
    /api/fut/player-prices/<gameYear>/<card_id>/ - never calls
    /api/fut/price-access/sign/ ourselves, never injects a fetch() for
    the sales endpoint (item 3). Returns the same result shape
    apply_batch_results() already expects from signed_fetch:
    {"eaId": ..., "completedAuctions": [...], "currentPrice": ...} on
    success, {"eaId": ..., "error": "..."} on any failure - so both
    transports feed the exact same data-integrity logic.

    The response listener is attached immediately before navigation and
    ALWAYS removed in a `finally` before returning (item 3: "remove
    listeners cleanly between cards so responses cannot leak across
    cards") - a page reused for the next card starts with zero
    listeners left over from this one.
    """
    marker = _player_prices_url_marker(card_id)
    captured: dict[str, Any] = {}
    pending: list[asyncio.Task] = []

    async def read(response) -> None:
        try:
            headers = await response.all_headers()
            captured["status"] = response.status
            captured["cf_mitigated"] = headers.get("cf-mitigated")
            captured["content_type"] = headers.get("content-type", "")
            body = await response.body()
            if response.status == 200:
                try:
                    captured["payload"] = json.loads(body)
                except Exception:
                    captured["parse_error"] = True
            elif diagnostic:
                try:
                    captured["body_preview"] = body[:500].decode("utf-8", errors="replace")
                except Exception:
                    pass
        except Exception:
            log.warning("page_capture: failed reading response for card=%s", card_id, exc_info=True)

    def on_response(response) -> None:
        if marker not in response.url:
            return
        # Belt-and-suspenders on top of the marker's own trailing-slash
        # anchoring: re-parse the id from the URL and reject anything
        # that doesn't match exactly (item 3).
        if _card_id_from_player_prices_url(response.url) != card_id:
            return
        pending.append(asyncio.ensure_future(read(response)))

    page.on("response", on_response)
    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except Exception as exc:
            return {"eaId": card_id, "error": f"navigation_failed:{type(exc).__name__}"}

        # Same idiom as PriceSync's HYDRATION_WAIT_MS: the price request
        # fires during hydration, after domcontentloaded - minimum wait,
        # not full page load (item 3).
        await page.wait_for_timeout(PAGE_SETTLE_MS)

        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=10
                )
            except asyncio.TimeoutError:
                pass
    finally:
        page.remove_listener("response", on_response)

    if "payload" not in captured:
        status = captured.get("status")
        if captured.get("cf_mitigated") == "challenge":
            error = "page_capture_challenge"
        elif status == 403:
            error = "page_capture_403"
        elif status is None:
            error = "page_capture_timeout"
        elif captured.get("parse_error"):
            error = "malformed_json"
        else:
            error = f"page_capture_status_{status}"
        result: dict[str, Any] = {"eaId": card_id, "error": error}
        if diagnostic:
            result["dataStatus"] = status
            result["cfMitigated"] = captured.get("cf_mitigated")
            result["dataContentType"] = captured.get("content_type")
            if captured.get("body_preview"):
                result["dataBodyPreview"] = captured["body_preview"]
        return result

    payload = captured["payload"]
    if not isinstance(payload, dict) or "data" not in payload:
        return {"eaId": card_id, "error": "malformed_payload"}

    d = payload.get("data") or {}
    completed = d.get("completedAuctions")
    if not isinstance(completed, list):
        return {"eaId": card_id, "error": "malformed_payload"}

    return {
        "eaId": card_id,
        "completedAuctions": completed,
        "currentPrice": d.get("currentPrice"),
        "dataStatus": captured.get("status"),
        "dataContentType": captured.get("content_type"),
    }


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
        SELECT source_card_id, rating, next_sales_due_at, source_url
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
#: 403 wall or a sign_5xx means "slow down" exactly as a 429 does. Also
#: what the circuit breaker's auto-transport-switch (item 4) looks for -
#: "challenge" covers both signed_fetch's challenge_required and
#: page_capture's page_capture_challenge uniformly.
_THROTTLE_ERROR_MARKERS = ("429", "403", "sign_5", "data_5", "challenge")


def is_throttle_signal(error: str) -> bool:
    return any(marker in error for marker in _THROTTLE_ERROR_MARKERS)


#: Cap on how many individual failures get a full diagnostic log line per
#: batch - a 403-everything cycle can fail all 200 cards, and logging
#: each one in full would be pure noise. stats["errors"] already carries
#: the full per-cycle count breakdown; this is just enough samples to see
#: the actual HTTP/shape evidence without flooding the log.
MAX_FAILURE_SAMPLE_LOGS_PER_BATCH = 3


def log_failure_sample(card_id: int, result: dict[str, Any], transport: str = "signed_fetch") -> None:
    """Safe, redacted diagnostic logging for one failed fetch - status
    codes, content-type, origin/shape booleans, and (diagnostic mode
    only) a short, further-redacted body preview. Never the signed URL
    itself (the query string carrying the verify token never leaves the
    browser context - see _FETCH_SCRIPT), never cookies, never a raw
    auth/token value."""
    preview = result.get("dataBodyPreview")
    if transport == "page_capture":
        log.warning(
            "sales fetch failed [page_capture] card=%s error=%s dataStatus=%s "
            "dataContentType=%r cfMitigated=%s%s",
            card_id, result.get("error"), result.get("dataStatus"),
            result.get("dataContentType"), result.get("cfMitigated"),
            f" bodyPreview={redact_value(preview)!r}" if preview else "",
        )
        return
    log.warning(
        "sales fetch failed [signed_fetch] card=%s error=%s signStatus=%s dataStatus=%s "
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
    pool: asyncpg.Pool, rows, results, stats, timers,
    throttle: "AdaptiveThrottle | None" = None, transport: str = "signed_fetch",
) -> None:
    """The non-network half of a batch: given already-fetched `results`
    (from process_batch's page.evaluate, a page_capture batch, or a
    one-card diagnostic run), apply the data-integrity rules and write to
    the database. `throttle` is optional - page_capture's concurrency is
    controlled by a fixed worker-pool size (PAGE_CONCURRENCY), not
    AdaptiveThrottle's dynamic batch/delay tuning, so it has nothing to
    record here. Split out
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
                log_failure_sample(card_id, result, transport=transport)
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

    if throttle is not None:
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


async def process_batch_page_capture(
    pages: list, pool: asyncpg.Pool, rows, stats, timers,
    shutdown_event: "asyncio.Event | None" = None, diagnostic: bool = False,
) -> None:
    """page_capture's equivalent of process_batch: distributes `rows`
    across `pages` (PAGE_CONCURRENCY workers, each a real Playwright Page
    reused across many cards - "reuse one browser/context/page where
    safe", item 3) via a shared queue, each worker doing sequential
    navigate+capture+detach through fetch_via_page_capture(). Cards
    missing a source_url can't be page-captured at all - counted as
    failed, never advanced, exactly like any other failure."""
    queue: asyncio.Queue = asyncio.Queue()
    for row in rows:
        queue.put_nowait(row)

    results: list[dict[str, Any]] = []

    async def worker(page) -> None:
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                return
            try:
                row = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            card_id = int(row["source_card_id"])
            url = row["source_url"]
            if not url:
                results.append({"eaId": card_id, "error": "no_source_url"})
                continue
            with timers.track("page_capture_fetch"):
                result = await fetch_via_page_capture(page, card_id, url, diagnostic=diagnostic)
            results.append(result)

    await asyncio.gather(*(worker(page) for page in pages))
    await apply_batch_results(pool, rows, results, stats, timers, transport="page_capture")


def _mostly_challenge_failures(stats: dict[str, Any]) -> bool:
    """Whether a cycle's failures look like Cloudflare pushing back
    (challenge/403/5xx) rather than an unrelated application bug -
    switching transport only makes sense for the former (item 4)."""
    errors = stats.get("errors", {})
    total_failed = stats["failed"]
    if total_failed == 0:
        return False
    challenge_like = sum(count for err, count in errors.items() if is_throttle_signal(err))
    return (challenge_like / total_failed) >= 0.5


async def _reprobe_signed_fetch(
    signed_page, pool: asyncpg.Pool, sample: list
) -> None:
    """A small, cheap signed_fetch sample while parked on page_capture
    (auto mode only) - not the full due queue, so a still-blocked
    transport costs almost nothing to keep checking on. Reuses
    process_batch/apply_batch_results directly, so a successful probe's
    reads are real, useful work, not thrown away."""
    if not sample:
        return
    log.warning(
        "auto mode: reprobing signed_fetch with a %d-card sample "
        "(currently parked on page_capture)", len(sample),
    )
    probe_stats = {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}
    probe_throttle = AdaptiveThrottle(len(sample), 0.5)
    await process_batch(signed_page, pool, sample, probe_stats, StageTimers(), probe_throttle)

    attempted = probe_stats["ok"] + probe_stats["failed"]
    failure_ratio = (probe_stats["failed"] / attempted) if attempted else 0.0
    if attempted and failure_ratio < CIRCUIT_BREAKER_THRESHOLD:
        switch_to_signed_fetch(
            f"reprobe succeeded: {probe_stats['ok']} ok / {probe_stats['failed']} failed"
        )
    else:
        log.warning(
            "auto mode: signed_fetch reprobe still failing (%d ok / %d failed) - "
            "staying on page_capture, next reprobe in %d minutes",
            probe_stats["ok"], probe_stats["failed"], SIGNED_REPROBE_MINUTES,
        )
        switch_to_page_capture("reprobe still failing")


async def sleep_or_shutdown(seconds: float, shutdown_event: asyncio.Event) -> bool:
    """Sleep up to `seconds`, waking immediately if shutdown_event is
    set. Returns True if shutdown was requested during the wait."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return shutdown_event.is_set()


def _install_shutdown_handlers(shutdown_event: asyncio.Event) -> None:
    """SIGTERM/SIGINT both request a graceful stop: the current step
    (one navigation, one batch) finishes - bounded by PAGE_TIMEOUT_MS/
    TIMEOUT_MS either way - then the loop exits instead of sleeping out
    the cycle interval or getting killed mid-write. POSIX-only -
    falls back to the interpreter's default SIGINT->KeyboardInterrupt
    handling (still caught in __main__) where unavailable."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _on_shutdown_signal(s, shutdown_event))
        except NotImplementedError:
            pass


def _on_shutdown_signal(sig: "signal.Signals", shutdown_event: asyncio.Event) -> None:
    log.warning("received %s - shutting down after the current step", sig.name)
    shutdown_event.set()


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
        "%s starting: transport=%s platform=%s batch=%d page_concurrency=%d "
        "select_limit=%d intervals=%s",
        SCRIPT_VERSION, TRANSPORT_MODE, PLATFORM, BATCH_SIZE, PAGE_CONCURRENCY,
        SELECT_LIMIT, SALES_INTERVALS,
    )

    shutdown_event = asyncio.Event()
    _install_shutdown_handlers(shutdown_event)

    need_signed = TRANSPORT_MODE in ("signed_fetch", "auto")
    need_page_capture = TRANSPORT_MODE in ("page_capture", "auto")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            context = await browser.new_context(user_agent=USER_AGENT, locale="en-GB")

            signed_page = None
            if need_signed:
                signed_page = await context.new_page()
                # ONE navigation for the entire process lifetime. Everything
                # after this is fetch() from within the already-loaded page.
                await signed_page.goto(anchor, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                log.info("anchor page loaded (signed_fetch transport): %s", anchor)

            capture_pages: list = []
            if need_page_capture:
                # One Page per PAGE_CONCURRENCY worker slot, opened once
                # and reused across every card that worker ever handles -
                # "reuse one browser/context/page where safe" (item 3).
                # Same shared browser+context as signed_fetch either way,
                # so a mid-run transport switch (auto mode) never needs a
                # fresh browser.
                for _ in range(PAGE_CONCURRENCY):
                    capture_pages.append(await context.new_page())
                log.info(
                    "page_capture transport ready: %d page(s) (FUTGG_SALES_PAGE_CONCURRENCY)",
                    len(capture_pages),
                )

            throttle = AdaptiveThrottle(BATCH_SIZE, BATCH_DELAY)

            while not shutdown_event.is_set():
                timers = StageTimers()
                stats = {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}
                started = time.perf_counter()

                async with pool.acquire() as conn:
                    due = await select_due(conn)
                    await report_coverage(conn)
                if not due:
                    if await sleep_or_shutdown(IDLE_SLEEP, shutdown_event):
                        break
                    continue

                if signed_reprobe_due() and signed_page is not None:
                    sample, due = due[:SIGNED_REPROBE_SAMPLE], due[SIGNED_REPROBE_SAMPLE:]
                    await _reprobe_signed_fetch(signed_page, pool, sample)
                    if not due:
                        await sleep_or_shutdown(CYCLE_SLEEP, shutdown_event)
                        continue

                transport = current_transport() if TRANSPORT_MODE == "auto" else TRANSPORT_MODE
                breaker_tripped = False

                if transport == "signed_fetch":
                    offset = 0
                    while offset < len(due) and not shutdown_event.is_set():
                        # Size is read per iteration, so a mid-cycle
                        # backoff takes effect immediately rather than at
                        # the next cycle.
                        size = throttle.batch_size
                        await process_batch(
                            signed_page, pool, due[offset: offset + size], stats, timers, throttle
                        )
                        offset += size

                        if circuit_breaker_tripped(stats):
                            breaker_tripped = True
                            log.warning(
                                "circuit breaker TRIPPED on signed_fetch: %d/%d failed "
                                "(>=%.0f%% threshold) after selecting %d/%d cards this "
                                "cycle - stopping further selection/marking, backing off %.0fs",
                                stats["failed"], stats["ok"] + stats["failed"],
                                CIRCUIT_BREAKER_THRESHOLD * 100, offset, len(due),
                                CIRCUIT_BREAKER_BACKOFF_SECONDS,
                            )
                            if TRANSPORT_MODE == "auto" and _mostly_challenge_failures(stats):
                                switch_to_page_capture(
                                    f"circuit breaker tripped on signed_fetch "
                                    f"({stats['failed']}/{stats['ok'] + stats['failed']} "
                                    f"failed, predominantly Cloudflare-challenge-shaped)"
                                )
                            break

                        if throttle.delay:
                            if await sleep_or_shutdown(throttle.delay, shutdown_event):
                                break
                else:
                    # page_capture - chunked the same way, so the circuit
                    # breaker can still stop mid-cycle instead of
                    # grinding through the whole due list (item 6:
                    # "prevent repeated 200-card failed cycles" applies
                    # here too, not only to signed_fetch).
                    chunk_size = max(PAGE_CONCURRENCY, min(CIRCUIT_BREAKER_MIN_SAMPLE, len(due)))
                    offset = 0
                    while offset < len(due) and not shutdown_event.is_set():
                        chunk = due[offset: offset + chunk_size]
                        await process_batch_page_capture(
                            capture_pages, pool, chunk, stats, timers,
                            shutdown_event=shutdown_event,
                        )
                        offset += chunk_size

                        if circuit_breaker_tripped(stats):
                            breaker_tripped = True
                            log.warning(
                                "circuit breaker TRIPPED on page_capture: %d/%d failed "
                                "(>=%.0f%% threshold) after selecting %d/%d cards this "
                                "cycle - stopping further selection/marking, backing off %.0fs",
                                stats["failed"], stats["ok"] + stats["failed"],
                                CIRCUIT_BREAKER_THRESHOLD * 100, offset, len(due),
                                CIRCUIT_BREAKER_BACKOFF_SECONDS,
                            )
                            break

                elapsed = max(time.perf_counter() - started, 0.001)
                log.info(
                    "cycle[%s]: cards_ok=%d failed=%d sales=%d in %.1fs (%.0f cards/min) "
                    "throttle[%s] errors=%s%s",
                    transport, stats["ok"], stats["failed"], stats["sales_seen"], elapsed,
                    (stats["ok"] / elapsed) * 60, throttle.describe(),
                    stats.get("errors", {}),
                    " CIRCUIT_BREAKER_TRIPPED" if breaker_tripped else "",
                )
                for line in timers.format_lines():
                    log.info(line)

                if shutdown_event.is_set():
                    break
                await sleep_or_shutdown(
                    CIRCUIT_BREAKER_BACKOFF_SECONDS if breaker_tripped else CYCLE_SLEEP,
                    shutdown_event,
                )
        finally:
            log.info("shutting down: closing browser and database pool")
            await browser.close()
            await pool.close()
    log.info("shutdown complete")


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


async def _run_diagnostic_signed_fetch(pool: asyncpg.Pool, row, card_id: int, anchor: str) -> tuple[bool, dict]:
    """The original signed_fetch diagnostic path - anchor page + injected
    fetch() against the sign/data endpoints, with full network
    diagnostics attached."""
    from playwright.async_api import async_playwright

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
            # Let every in-flight request/response logging task finish
            # before the browser (and the CDP connection they depend on)
            # closes underneath them.
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
        log_failure_sample(card_id, result, transport="signed_fetch")
        log.error("DIAGNOSTIC: card=%d FAILED [signed_fetch]: error=%s", card_id, error)
        ok = False
    else:
        sales = result.get("completedAuctions") or []
        log.info(
            "DIAGNOSTIC: card=%d SUCCESS [signed_fetch]: %d completed auction(s) currentPrice=%s "
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
    return ok, results


async def _run_diagnostic_page_capture(pool: asyncpg.Pool, row, card_id: int) -> tuple[bool, list]:
    """page_capture diagnostic path (item 7): navigate to the card's OWN
    source_url and passively capture its organic player-prices response -
    no sign call, no injected fetch(). Reports status, auction count,
    elapsed time, and whether Cloudflare challenged it, exactly as
    required."""
    from playwright.async_api import async_playwright

    url = row["source_url"] if row is not None else None
    if not url:
        log.error(
            "DIAGNOSTIC: card=%d has no source_url in futgg_players - "
            "page_capture needs the card's own player page URL to navigate to",
            card_id,
        )
        return False, [{"eaId": card_id, "error": "no_source_url"}]

    log.info("DIAGNOSTIC: navigating to card's own source_url: %s", url)
    started = time.perf_counter()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            context = await browser.new_context(user_agent=USER_AGENT, locale="en-GB")
            page = await context.new_page()
            context_diag = await capture_context_diagnostics(context, page)
            log.info("DIAGNOSTIC: browser/context state: %s", context_diag)

            pending_network_logs = _attach_network_diagnostics(page)
            result = await fetch_via_page_capture(page, card_id, url, diagnostic=True)
            if pending_network_logs:
                await asyncio.wait_for(
                    asyncio.gather(*pending_network_logs, return_exceptions=True), timeout=15
                )
        finally:
            await browser.close()

    elapsed = time.perf_counter() - started
    error = result.get("error")
    if error:
        log_failure_sample(card_id, result, transport="page_capture")
        log.error(
            "DIAGNOSTIC: card=%d FAILED [page_capture]: error=%s dataStatus=%s "
            "cfMitigated=%s elapsed=%.2fs (cloudflare_challenged=%s)",
            card_id, error, result.get("dataStatus"), result.get("cfMitigated"), elapsed,
            result.get("cfMitigated") == "challenge" or error == "page_capture_challenge",
        )
        ok = False
    else:
        sales = result.get("completedAuctions") or []
        log.info(
            "DIAGNOSTIC: card=%d SUCCESS [page_capture]: %d completed auction(s) "
            "currentPrice=%s dataStatus=%s dataContentType=%r elapsed=%.2fs "
            "(cloudflare_challenged=False)",
            card_id, len(sales), result.get("currentPrice"), result.get("dataStatus"),
            result.get("dataContentType"), elapsed,
        )
        ok = True
    return ok, [result]


async def run_diagnostic_card(card_id: int) -> bool:
    """One-card diagnostic pass (item 9/7) - test a single known card
    inside Coolify without touching the whole queue:

        FUTGG_SALES_DIAGNOSTIC_CARD_ID=<id>
        FUTGG_SALES_APPLY=false
        FUTGG_SALES_TRANSPORT=page_capture   # or signed_fetch/auto

    Logs full safe diagnostics regardless of outcome, then applies the
    exact same write path a real cycle would (apply_batch_results) - so
    this mode's database behaviour, including the never-advance-on-
    failure fix, is provably the same code as production, not a
    reimplementation that could quietly drift from it. With
    FUTGG_SALES_APPLY=false (the documented pairing above) nothing is
    written either way. Returns True on a successful fetch, False on a
    failure or an unknown card.
    """
    log.warning("=" * 72)
    log.warning(
        "FUTGG_SALES_DIAGNOSTIC_CARD_ID=%d - single-card diagnostic mode, "
        "transport=%s apply=%s",
        card_id, TRANSPORT_MODE, APPLY,
    )
    log.warning("=" * 72)

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2, command_timeout=60)
    try:
        async with pool.acquire() as conn:
            await ensure_schema(conn)
            row = await conn.fetchrow(
                """
                SELECT source_card_id, rating, name, next_sales_due_at, source_url
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
            "DIAGNOSTIC: card=%d name=%r rating=%s next_sales_due_at=%s (never_read=%s) source_url=%s",
            card_id, row["name"], row["rating"], row["next_sales_due_at"],
            row["next_sales_due_at"] is None, row["source_url"],
        )

        anchor = anchor or "https://www.fut.gg/players/1114-roberto-baggio/26-1114/"

        if TRANSPORT_MODE == "page_capture":
            ok, results = await _run_diagnostic_page_capture(pool, row, card_id)
        else:
            ok, results = await _run_diagnostic_signed_fetch(pool, row, card_id, anchor)

        stats = {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}
        await apply_batch_results(
            pool, [row], results, stats, StageTimers(),
            transport="page_capture" if TRANSPORT_MODE == "page_capture" else "signed_fetch",
        )
        log.info(
            "DIAGNOSTIC: would-apply summary: ok=%d failed=%d sales_seen=%d "
            "(FUTGG_SALES_APPLY=%s - %s)",
            stats["ok"], stats["failed"], stats["sales_seen"], APPLY,
            "written to database" if APPLY else "NOT written - dry run",
        )

        log.warning("=" * 72)
        log.warning(
            "DIAGNOSTIC MODE complete for card=%d: ok=%s transport=%s",
            card_id, ok, TRANSPORT_MODE,
        )
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
