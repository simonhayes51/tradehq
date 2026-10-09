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

    POST /api/fut/price-access/sign/   {"url": "/api/fut/player-prices/27/<eaId>/?platform=ps5"}
      -> {"data": {"url": "...?verify=<token>", "expiresIn": 120}}
    GET  <signed url>                  -> ~25 KB JSON

carrying `completedAuctions` with ~100 sales - twice the depth of the
table scrape - plus exact soldDate timestamps rather than "18 minutes
ago" text that has to be parsed and rounded.

TRANSPORT
---------
Production evidence (2026-08-04, Hetzner/Coolify) first showed that
injecting fetch() calls against the signed price-access endpoint from an
unrelated anchor page - even though the fetch runs inside the live
fut.gg origin - gets served a Cloudflare JS challenge
(`cf-mitigated: challenge`) on the GET. The initial hypothesis was that a
fully organic page navigation (matching how PriceSync has always worked)
would bypass this. A LATER diagnostic run disproved that: a page_capture
navigation against the exact same card got challenged identically. The
actual differentiator, confirmed from that same run's own outbound
Google Analytics traffic (`navigator.userAgentData` leaking
`HeadlessChrome` regardless of the spoofed User-Agent header), is the
browser running headless - see Dockerfile.sales/docker-entrypoint-
sales.sh for the actual fix (headed Chromium via Xvfb) and COOLIFY.md's
"Headed Chromium via Xvfb" section for the full evidence trail. The two
transports below are kept regardless - page_capture/auto remain a real
reliability safeguard against throttling/blocking independent of the
headless finding - but neither transport by itself was the fix:

  signed_fetch  - the original approach: one anchor page is loaded once
                  and reused; each batch calls page.evaluate() to run
                  POST /api/fut/price-access/sign/ then GET <signed url>
                  concurrently for N cards via Promise.all. Fast, but is
                  the transport Cloudflare is now seen to challenge.

  page_capture  - navigates to each card's own source_url (the real
                  player page), attaches a page.on("response", ...)
                  listener BEFORE navigation, and passively captures that
                  page's own naturally-triggered request to
                  /api/fut/player-prices/27/<card-id>/ - no injected
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

from playwright_stealth import Stealth

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import signal
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import asyncpg

from db_connect import connect_with_retry, create_pool_with_retry

from futgg_instrumentation import StageTimers, redact_headers, redact_url, redact_value
from futgg_price_access import build_batch_script

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


GAME_YEAR = os.getenv("FUTGG_GAME_YEAR", "27")
PLATFORM = os.getenv("FUTGG_SALES_PLATFORM", "ps5")
MIN_SALES_RATING = env_int("FUTGG_SALES_MIN_RATING", 80, minimum=0)
HEADLESS = env_bool("PLAYWRIGHT_HEADLESS", True)
TIMEOUT_MS = env_int("PLAYWRIGHT_TIMEOUT_MS", 45000, minimum=5000)


def sales_catalogue_filter(alias: str = "p") -> str:
    """Return the shared SQL scope for cards eligible for sales history."""
    alias = alias.strip() or "p"
    return (
        f"COALESCE({alias}.is_active, TRUE) = TRUE "
        f"AND COALESCE({alias}.is_tradeable, TRUE) = TRUE "
        f"AND {alias}.game_year = {int(GAME_YEAR)} "
        f"AND {alias}.rating >= {int(MIN_SALES_RATING)} "
        f"AND ({alias}.rarity IS NULL OR {alias}.rarity IN ('Gold Rare', 'Icon'))"
    )

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

# Defence in depth for Railway env drift: the entrypoint caps the old
# production FUTGG_SALES_SELECT_LIMIT=400 down before starting this
# worker, but logs from 2026-09-06 still showed cycles selecting 117/120.
# If the entrypoint is bypassed, an old image is half-rolled, or
# FUTGG_SALES_SELECT_LIMIT_MAX is overridden, keep the signed endpoint
# from running above the safe per-cycle cap in the worker itself.
SELECT_LIMIT_HARD_CAP = env_int("FUTGG_SALES_SELECT_LIMIT_HARD_CAP", 90, minimum=1)
if SELECT_LIMIT > SELECT_LIMIT_HARD_CAP:
    log.warning(
        "Capping FUTGG_SALES_SELECT_LIMIT from %d to hard cap %d inside worker",
        SELECT_LIMIT, SELECT_LIMIT_HARD_CAP,
    )
    SELECT_LIMIT = SELECT_LIMIT_HARD_CAP

#: Minimum share of SELECT_LIMIT reserved for each adaptive-priority
#: tier every cycle, so a fast tier cannot starve a slow one
#: indefinitely.
#:
#: select_due() used to band cards by RATING (95+/85+/80-84/75-79/
#: everything else), which starved the ratings-based tail for exactly
#: the reason this floor mechanism was first built to fix (see this
#: file's git history for that incident). But rating was never the
#: right axis: card_priority (migration 044's adaptive-priority engine,
#: see backend/app/services/priority_recompute.py, refreshed every 15
#: minutes) already tiers every active card as CRITICAL/HIGH/NORMAL/
#: LOW/DORMANT by liquidity/volatility/opportunity - and the customer-
#: facing "sales data is behind" signal (market_data_provider.py's
#: stale_sales_by_priority_tier) is judged against THAT tiering, not
#: rating. A rating-band scheduler could run perfectly and still leave
#: most of CRITICAL/HIGH permanently "stale" by the metric actually
#: shown to users, simply because nothing ever tried to hit their
#: interval - confirmed live: CRITICAL/HIGH accounted for most of a
#: stale-sum that stayed over 50% of priced cards through a 6-hour
#: stretch where coverage was otherwise climbing steadily. Banding by
#: effective_tier instead means "due" and "reported stale" finally
#: agree on every card, not just the ones a rating cutoff happened to
#: also prioritise.
#:
#: Shares, not fixed counts, so this scales if SELECT_LIMIT is tuned.
#: They sum to 0.75 of SELECT_LIMIT, leaving the remaining 25% as
#: genuine overflow capacity in select_due() that still follows the
#: original priority order - a quiet cycle for a fast tier lets that
#: overflow reach further into whichever tier actually has the
#: backlog, rather than being rigidly fixed to a per-tier count.
#:
#: CORRECTED after live evidence: the first version of these shares
#: (CRITICAL 0.05 .. DORMANT 0.25) ported the OLD rating-band
#: allocation directly - biggest floor for the slowest/largest tier,
#: on the theory that it needed protecting from starvation. That
#: reasoning made sense for rating bands, where the slow tail also had
#: the loosest interval AND got crowded out by faster bands. It does
#: NOT hold for priority tiers: DORMANT's interval (SALES_INTERVALS,
#: 4000min) is now so loose that a small floor keeps it satisfied on
#: its own, while CRITICAL/HIGH's much tighter intervals (180/720min)
#: for populations in the thousands genuinely need real guaranteed
#: throughput to have any chance of being met. Confirmed live: with the
#: old shares, DORMANT's stale count fell every reading (3783->2984)
#: while CRITICAL/HIGH/NORMAL's kept RISING (1777->1857, 4639->4976,
#: 1439->1708) despite already-loosened, achievable interval targets -
#: a 20-card CRITICAL floor and a 40-card HIGH floor per cycle were
#: simply never going to keep pace with populations in the thousands,
#: no matter how achievable the interval was. DORMANT and LOW now get
#: just enough floor to avoid the original starvation failure mode,
#: not the largest share in the file.
_TIER_MIN_SHARE: dict[str, float] = {
    "CRITICAL": 0.25,
    "HIGH": 0.25,
    "NORMAL": 0.10,
    "LOW": 0.05,
    "DORMANT": 0.10,
}

#: SQL condition per tier, matching select_due()'s own CASE expression.
#: COALESCE to NORMAL mirrors market_data_provider.py's
#: DEFAULT_SALES_TIER: a card card_priority has not scored yet (or a
#: database migration 044 predates) defaults to NORMAL on both the
#: scheduling side here and the staleness-reporting side there, so the
#: two never quietly disagree about an unscored card.
_TIER_WHERE: dict[str, str] = {
    "CRITICAL": "COALESCE(cp.effective_tier, 'NORMAL') = 'CRITICAL'",
    "HIGH": "COALESCE(cp.effective_tier, 'NORMAL') = 'HIGH'",
    "NORMAL": "COALESCE(cp.effective_tier, 'NORMAL') = 'NORMAL'",
    "LOW": "COALESCE(cp.effective_tier, 'NORMAL') = 'LOW'",
    "DORMANT": "COALESCE(cp.effective_tier, 'NORMAL') = 'DORMANT'",
}

#: Bounded run: exit cleanly after this many minutes. 0 = run forever
#: (the existing, unchanged default).
#:
#: WHY THIS EXISTS. Reducing how many cards this worker reads does not by
#: itself reduce what it costs, because the bill is for a container
#: running a headed Chromium 24 hours a day - an idle container costs the
#: same as a busy one. The only way the compute bill actually falls is for
#: the process to finish and exit, so the container stops.
#:
#: With this set, SalesSync can be deployed the way PlayerSync and SBCSync
#: already are: a Coolify Scheduled Task that starts on a cron trigger,
#: works through whatever is due, and exits. Running four 45-minute passes
#: a day is ~3 hours of compute instead of 24.
#:
#: MUST be paired with the Scheduled Task resource type. A normal service
#: with a restart policy turns a clean exit into a restart loop, which is
#: the exact footgun COOLIFY.md documents - it would relaunch a browser
#: every few seconds and cost far MORE than running continuously.
MAX_RUNTIME_MINUTES = env_int("FUTGG_SALES_MAX_RUNTIME_MINUTES", 0, minimum=0)

#: Alternative bound: exit after this many completed cycles. 0 = unlimited.
#: Useful when you would rather cap work than wall-clock time.
MAX_CYCLES = env_int("FUTGG_SALES_MAX_CYCLES", 0, minimum=0)

#: Close and relaunch the whole browser+context (not just a crashed page -
#: see SignedPageHandle.recreate()) after this many minutes of continuous
#: use. 0 = never.
#:
#: Production incident (2026-09-07): the sales-sync container's CPU
#: climbed steadily for ~2 hours, pinned at its ceiling, then the process
#: was killed outright with no exception, no shutdown log line, nothing -
#: Coolify's own CPU/memory graphs for the service showed a vertical
#: cliff to 0 at the exact moment the log output stopped, the signature
#: of an external SIGKILL rather than anything this script decided to do.
#: It did not recover on its own; a manual restart was required.
#:
#: This worker deliberately keeps ONE headed Chromium browser, context,
#: and (for signed_fetch) one anchor page alive for the entire process
#: lifetime - by design, since re-navigating per card would be far more
#: expensive and more exposed to Cloudflare's challenge (see
#: SignedPageHandle's own docstring: "ONE navigation for the entire
#: process lifetime"). SignedPageHandle.recreate() already replaces a
#: single crashed/closed PAGE, but nothing before this ever recycled the
#: browser/context itself - a slow accumulation inside one long-lived
#: renderer (unclosed listeners, retained fetch() closures, general GC
#: pressure from thousands of evaluate() calls) has no other way to be
#: released short of a full relaunch. A scheduled recycle between cycles
#: (never mid-batch) bounds how long any one browser instance runs,
#: turning an unbounded slow leak into a periodic, cheap, and observable
#: reset instead of an unplanned kill hours later.
BROWSER_RECYCLE_MINUTES = env_int("FUTGG_SALES_BROWSER_RECYCLE_MINUTES", 90, minimum=0)

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

#: Escalating retry backoff, indexed by CONSECUTIVE failure count.
#:
#: THE BUG THIS FIXES. Until now a never-read card that failed was given
#: no next_sales_due_at at all - deliberately, so it stayed NULL and kept
#: its place at the front of select_due()'s backfill ordering. The
#: unnoticed consequence is that "stays at the front of the queue" and
#: "is due right now" were the same condition, so a card that fails
#: permanently (delisted, extinct, untradeable-but-still-flagged, a bad
#: source_url) was re-selected on EVERY cycle, forever. At CYCLE_SLEEP=5s
#: that is a hot loop of full Chromium page navigations costing real money
#: and returning nothing, and it is invisible in any interval arithmetic
#: because the card has no interval.
#:
#: Fixed by separating the two concepts that were conflated:
#:   * "has never been successfully read" is now futgg_players
#:     .sales_first_read_at IS NULL - a fact about history, which a failed
#:     attempt does not change, so backfill preference survives;
#:   * "may be attempted again now" stays next_sales_due_at, which every
#:     failure now sets, so nothing can retry unboundedly.
#:
#: The first step is FAILURE_RETRY_BACKOFF_MINUTES so the existing,
#: deliberate "retry a transient failure soon" behaviour is preserved
#: exactly for the common case (see that constant's own note). Only
#: REPEATED failure escalates, which is the case that was pathological.
#: The ladder is capped, so a permanently-broken card settles at one
#: cheap attempt per FUTGG_SALES_FAILURE_BACKOFF_MAX_MIN rather than
#: thousands per day, and a success resets it to zero so a card that
#: recovers is not punished for an outage.
def _parse_backoff_ladder(raw: str) -> list[int]:
    steps: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0:
            steps.append(value)
    return steps


FAILURE_BACKOFF_LADDER = _parse_backoff_ladder(
    os.getenv("FUTGG_SALES_FAILURE_BACKOFF_LADDER_MIN", "")
) or [FAILURE_RETRY_BACKOFF_MINUTES, 15, 30, 60, 180, 720]
FAILURE_BACKOFF_MAX_MINUTES = env_int(
    "FUTGG_SALES_FAILURE_BACKOFF_MAX_MIN", 1440, minimum=1
)


def failure_backoff_minutes(consecutive_failures: int) -> int:
    """Minutes to wait before retrying after `consecutive_failures`.

    Pure and deterministic - the whole point is that "why is this card due
    then?" is answerable from a single integer. `consecutive_failures` is
    the count INCLUDING the failure just recorded, so 1 is the first.
    """
    if consecutive_failures < 1:
        consecutive_failures = 1
    index = min(consecutive_failures, len(FAILURE_BACKOFF_LADDER)) - 1
    return min(FAILURE_BACKOFF_LADDER[index], FAILURE_BACKOFF_MAX_MINUTES)


# A FUT.GG 404 is usually a persistent missing card-data resource, not a
# short network blip. Keep it visibly failed and retryable, but avoid
# spending a fresh request on the same missing resource every cycle.
DATA_404_RETRY_BACKOFF_MINUTES = env_int(
    "FUTGG_SALES_DATA_404_RETRY_BACKOFF_MINUTES", 180, minimum=1
)
RETRY_AFTER_MAX_SECONDS = env_int(
    "FUTGG_SALES_RETRY_AFTER_MAX_SECONDS", 21600, minimum=1
)


def parse_retry_after_seconds(value: Any, *, now: datetime | None = None) -> float | None:
    """Parse Retry-After as delta-seconds or an HTTP date.

    Browsers expose the header as text. Invalid values are ignored, while
    valid delays are bounded to six hours so a malformed upstream value
    cannot park a long-running collector indefinitely.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            reference = now or datetime.now(timezone.utc)
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            seconds = (retry_at - reference).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds <= 0:
        return None
    return min(float(RETRY_AFTER_MAX_SECONDS), seconds)


def failure_backoff_minutes_for_error(
    error: str, consecutive_failures: int, retry_after_seconds: float | None = None,
) -> int:
    """Choose a per-card retry that preserves errors without wasting slots.

    404s stay errors (never empty-sales successes), but start with a
    slower retry because the referenced data resource is absent. A 429's
    own Retry-After is a minimum, in addition to the ordinary escalating
    failure backoff.
    """
    delay = failure_backoff_minutes(consecutive_failures)
    if error == "data_404":
        delay = max(delay, DATA_404_RETRY_BACKOFF_MINUTES)
    if "429" in error and retry_after_seconds is not None:
        delay = max(delay, int((retry_after_seconds + 59) // 60))
    return min(delay, FAILURE_BACKOFF_MAX_MINUTES)

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
    "FUTGG_SALES_CIRCUIT_BREAKER_BACKOFF_SECONDS", 900.0, minimum=0.0
)
#: Production evidence: a cycle can trip again within seconds of
#: resuming from a full CIRCUIT_BREAKER_BACKOFF_SECONDS wait - a fixed
#: 300s is not reliably long enough for FUT.GG's own rate-limit window
#: to clear, and retrying straight into a still-active limit only spends
#: more requests against it for nothing (the same "a rejected request
#: still costs quota" reasoning AdaptiveThrottle already backs off
#: within a single cycle for - see its docstring). See
#: next_backoff_seconds() - the wait doubles on each consecutive trip,
#: capped here, and resets the moment a cycle actually completes clean.
CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS = env_float(
    "FUTGG_SALES_CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS", 3600.0, minimum=0.0
)
CONSECUTIVE_FULL_THROTTLE_BATCHES = env_int(
    "FUTGG_SALES_FULL_THROTTLE_BATCHES_BEFORE_BREAKER", 2, minimum=1
)

#: AdaptiveThrottle recovery tuning (see AdaptiveThrottle.record()).
#:
#: THE PROBLEM THIS FIXES. Recovery originally required
#: THROTTLE_RECOVERY_STREAK (was hardcoded 5) *consecutive*,
#: zero-tolerance clean batches - any single throttled request, however
#: isolated, reset that count to zero. Production evidence (2026-09-03
#: 21:00 UTC through 2026-09-04) showed this is not survivable against
#: real traffic: throughput collapsed to ~2-3 cards/hour and took
#: roughly 20 hours of wall-clock time to climb back to a healthy
#: batch=8, because a persistent low-rate trickle of 429s from the
#: low-priority tail kept resetting the streak before it could reach 5,
#: even while the great majority of requests were succeeding cleanly.
#:
#: THE FIX. A rolling window of the last THROTTLE_ROLLING_WINDOW_BATCHES
#: record() calls tracks (attempted, throttled) totals. A throttled
#: batch only resets the recovery streak if the window's aggregate
#: ratio is ALREADY unhealthy (> THROTTLE_ROLLING_TOLERANCE) or there
#: isn't yet enough history to judge (< THROTTLE_ROLLING_MIN_ATTEMPTED
#: attempted) - otherwise it is forgiven as an isolated blip and the
#: streak is left alone (not reset, not advanced). The streak threshold
#: itself also drops to THROTTLE_RECOVERY_STREAK (3): still a real
#: sustained-health requirement, just not one that needs to be perfect.
#:
#: What does NOT change: the instant per-batch backoff (delay increase,
#: batch shrink above 25% severity) still fires on every single
#: throttled batch exactly as before - a genuine 429 is never ignored,
#: only forgiven for the purpose of recovery bookkeeping. Sustained
#: throttling (ratio above tolerance) still fully blocks recovery, same
#: as today. No new concurrency, no change to max_delay/min_batch
#: bounds, no change to the recovery step's own magnitude (0.8x delay,
#: +1 batch).
THROTTLE_ROLLING_WINDOW_BATCHES = env_int(
    "FUTGG_SALES_THROTTLE_ROLLING_WINDOW_BATCHES", 8, minimum=1
)
THROTTLE_ROLLING_MIN_ATTEMPTED = env_int(
    "FUTGG_SALES_THROTTLE_ROLLING_MIN_ATTEMPTED", 16, minimum=1
)
THROTTLE_ROLLING_TOLERANCE = env_float(
    "FUTGG_SALES_THROTTLE_ROLLING_TOLERANCE", 0.10, minimum=0.0
)
THROTTLE_RECOVERY_STREAK = env_int(
    "FUTGG_SALES_THROTTLE_RECOVERY_STREAK", 3, minimum=1
)
DB_WRITE_MAX_ATTEMPTS = env_int("FUTGG_SALES_DB_WRITE_MAX_ATTEMPTS", 4, minimum=1)
DB_WRITE_RETRY_BASE_SECONDS = env_float(
    "FUTGG_SALES_DB_WRITE_RETRY_BASE_SECONDS", 0.25, minimum=0.0
)
PROGRESS_LOG_EVERY = env_int("FUTGG_SALES_PROGRESS_LOG_EVERY", 25, minimum=1)
SALES_SYNC_ADVISORY_LOCK_KEY = env_int(
    "FUTGG_SALES_ADVISORY_LOCK_KEY", 7741013
)
#: A rolling deploy starts the new container while the old one is still
#: up, so the new container's very first pg_try_advisory_lock() attempt
#: is expected to lose - the old instance normally releases the lock
#: within seconds of its own SIGTERM handling. Retrying for this long
#: covers that handoff; only a lock genuinely held by a second, unrelated
#: instance survives the whole window.
LOCK_ACQUIRE_RETRY_SECONDS = env_float(
    "FUTGG_SALES_LOCK_ACQUIRE_RETRY_SECONDS", 90.0, minimum=0.0
)
LOCK_ACQUIRE_POLL_SECONDS = env_float(
    "FUTGG_SALES_LOCK_ACQUIRE_POLL_SECONDS", 5.0, minimum=0.5
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
#: A normal player page with no price panel is not a transport failure:
#: FUT.GG uses that shape for cards which have no market (most commonly
#: untradeable SBC/objective rewards). Quarantine rather than permanently
#: changing is_tradeable, because FUT.GG can later add a market or briefly
#: fail to render the panel. The default rechecks once a week.
NO_PRICE_SECTION_RECHECK_MINUTES = env_int(
    "FUTGG_SALES_NO_PRICE_SECTION_RECHECK_MIN", 10080, minimum=60
)
#: In auto mode, once the circuit breaker has forced a switch to
#: page_capture, how long to wait before trying a small signed_fetch
#: probe batch again - deliberately long, so a still-blocked signed
#: transport isn't re-attempted (and re-failed) every cycle. None of the
#: real per-cycle traffic uses signed_fetch again until a probe succeeds.
SIGNED_REPROBE_MINUTES = env_int("FUTGG_SALES_SIGNED_REPROBE_MINUTES", 60, minimum=5)
#: Sample size for a signed_fetch reprobe - small and cheap, not the
#: whole due queue.
SIGNED_REPROBE_SAMPLE = env_int("FUTGG_SALES_SIGNED_REPROBE_SAMPLE", 5, minimum=1)

#: Sales refresh cadence by adaptive-priority tier (card_priority.
#: effective_tier, migration 044) - NOT by rating. Was rating-banded
#: (95_plus/85_plus/80_84/75_79/under_75) until production evidence
#: showed that axis structurally could not agree with the
#: customer-facing staleness signal, which has always been judged
#: against these same CRITICAL/HIGH/NORMAL/LOW/DORMANT tiers (see
#: market_data_provider.py's stale_sales_by_priority_tier and
#: _TIER_MIN_SHARE's own comment for the full incident). The env var
#: names below changed with it - FUTGG_SALES_95_PLUS_INTERVAL_MIN etc.
#: are no longer read.
#:
#: Deliberately much slower than the price cadence, for two reasons.
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
# Defaults mirror backend PRIORITY_ENGINE_CONFIG.tier_sales_interval_
# minutes (priority_config.py). The original 15/60/240-minute CRITICAL/
# HIGH/NORMAL defaults were aspirational, set before this worker
# actually scheduled by tier at all - live evidence once it did:
# CRITICAL/HIGH's stale counts stayed flat-to-rising for hours even as
# overall coverage climbed steadily, because 15 minutes for ~1,700
# CRITICAL cards needs ~118 cards/min sustained for that tier alone,
# against a real FUT.GG-rate-limited ceiling of roughly 12-15 cards/min
# even during a healthy stretch. See priority_config.py's own comment
# for the full incident and the reasoning behind these specific
# replacement values (CRITICAL matches the old rating-band system's
# proven-sustainable 95_plus cadence; LOW matches its proven under_75
# cadence). DORMANT's default is 4000, not that config's 4320, to stay
# strictly under ENGINE_REJECT_SALES_OLDER_THAN_MINUTES below (see
# cadence_gate_conflicts() - exactly at the limit still counts as a
# conflict).
SALES_INTERVALS = {
    "CRITICAL": env_int("FUTGG_SALES_CRITICAL_INTERVAL_MIN", 180, minimum=5),
    "HIGH": env_int("FUTGG_SALES_HIGH_INTERVAL_MIN", 720, minimum=5),
    "NORMAL": env_int("FUTGG_SALES_NORMAL_INTERVAL_MIN", 1440, minimum=10),
    "LOW": env_int("FUTGG_SALES_LOW_INTERVAL_MIN", 2880, minimum=30),
    "DORMANT": env_int("FUTGG_SALES_DORMANT_INTERVAL_MIN", 4000, minimum=60),
}
# A more urgent tier must never accidentally run less often than a less
# urgent one because of a partial env override - same guard the old
# rating bands had for 95+ vs 85+, now over the tier order instead.
_TIER_URGENCY_ORDER = ("CRITICAL", "HIGH", "NORMAL", "LOW", "DORMANT")
for _prev_tier, _next_tier in zip(_TIER_URGENCY_ORDER, _TIER_URGENCY_ORDER[1:]):
    SALES_INTERVALS[_next_tier] = max(
        SALES_INTERVALS[_next_tier], SALES_INTERVALS[_prev_tier]
    )

#: Mirror of backend futgg_config.EngineConfig.reject_sales_older_than_minutes.
#: Past this age the engine refuses to value a card at all - the sales
#: sample no longer describes today's market, so any target derived from
#: it would be guesswork.
#:
#: Duplicated rather than imported: this worker deploys separately from
#: the backend and cannot import app.services. test_sales_cadence_vs_gate.py
#: asserts the two stay equal, so drift fails CI rather than silently
#: reintroducing the contradiction below.
ENGINE_REJECT_SALES_OLDER_THAN_MINUTES = env_int(
    "FUTGG_REJECT_SALES_OLDER_THAN_MIN", 4320, minimum=60,
)


def cadence_gate_conflicts(
    intervals: dict[str, int] | None = None,
    reject_after_minutes: int | None = None,
) -> list[tuple[str, int, int]]:
    """Bands whose refresh interval exceeds what the engine accepts.

    Generic over whatever `intervals` names its bands - SALES_INTERVALS
    today keys on adaptive-priority tier (CRITICAL/HIGH/NORMAL/LOW/
    DORMANT), though the historical example below predates that switch
    and still uses the original rating-band names; the check itself
    never cared which axis the bands were drawn on.

    A band scheduled to be re-read less often than the engine's sales-age
    limit is not merely slow - it is structurally incapable of ever
    producing a valuation. Its sales age past the limit before the next
    scrape is even due, so every card in it is rejected as STALE_SALES
    forever, no matter how healthy this worker is.

    That is not hypothetical. Production ran:

        intervals={'85_plus': 1440, '80_84': 2880,
                   '75_79': 5760, 'under_75': 40320}

    against a 4320-minute (72h) engine limit. The two lower bands - 96
    hours and 28 days - could never be valued. Nothing said so: the
    dashboard simply showed no trades, and the cards were rejected one at
    a time with a reason nobody was aggregating.

    The two numbers are set in different places for different reasons -
    the intervals by cost control, the limit by advice quality - so
    neither side can see the contradiction it creates. Hence this check.

    Returns (band, interval_minutes, limit_minutes) for each conflict.
    """
    intervals = SALES_INTERVALS if intervals is None else intervals
    limit = (
        ENGINE_REJECT_SALES_OLDER_THAN_MINUTES
        if reject_after_minutes is None else reject_after_minutes
    )
    return [
        (band, minutes, limit)
        for band, minutes in intervals.items()
        if minutes >= limit
    ]


def log_cadence_gate_conflicts() -> None:
    conflicts = cadence_gate_conflicts()
    if not conflicts:
        return
    log.warning(
        "CADENCE EXCEEDS THE ENGINE'S SALES-AGE LIMIT for %d tier(s): %s. "
        "Cards in these tiers are scheduled to be re-read less often than "
        "the engine will accept sales for (%d min), so their sales age out "
        "before the next scrape is due and they can NEVER produce a "
        "recommendation - however well this worker runs. Either shorten "
        "the interval(s) below %d, or raise "
        "FUTGG_REJECT_SALES_OLDER_THAN_MIN (and the backend's "
        "reject_sales_older_than_minutes) to match, accepting older sales "
        "as evidence.",
        len(conflicts),
        ", ".join(
            f"{band}={minutes}min" for band, minutes, _ in conflicts
        ),
        ENGINE_REJECT_SALES_OLDER_THAN_MINUTES,
        ENGINE_REJECT_SALES_OLDER_THAN_MINUTES,
    )

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

    def __init__(
        self, batch_size: int, delay: float,
        *, resume_batch_size: int | None = None, resume_delay: float | None = None,
    ) -> None:
        # `batch_size`/`delay` set the BOUNDS (max_batch/min_delay), not
        # necessarily the starting point - see resume_batch_size/
        # resume_delay below. Without that distinction, resuming from a
        # previous run's throttled-down state (see load_throttle_state())
        # would permanently cap max_batch/min_delay at whatever the
        # throttle had backed off to, e.g. batch=2, and the ceiling could
        # then never recover back toward BATCH_SIZE even after FUT.GG
        # stopped rate-limiting entirely.
        self.max_batch = batch_size
        self.min_batch = 1
        self.min_delay = delay
        self.max_delay = max(delay * 40, 10.0)
        # Resume values are clamped into the bounds above - persisted
        # state from a run under a DIFFERENT BATCH_SIZE/BATCH_DELAY
        # config (an env var changed between runs) must not start outside
        # what this run's own bounds allow.
        self.batch_size = (
            max(self.min_batch, min(self.max_batch, resume_batch_size))
            if resume_batch_size is not None else batch_size
        )
        self.delay = (
            max(self.min_delay, min(self.max_delay, resume_delay))
            if resume_delay is not None else delay
        )
        self._clean_streak = 0
        self.consecutive_fully_throttled_batches = 0
        self.retry_after_until = 0.0
        # Rolling (attempted, throttled) history - see
        # THROTTLE_ROLLING_WINDOW_BATCHES's own comment for why this
        # exists: a single isolated throttled batch inside an otherwise
        # healthy window must not wipe out recovery progress the way a
        # zero-tolerance streak did.
        self._recent_batches: "deque[tuple[int, int]]" = deque(
            maxlen=THROTTLE_ROLLING_WINDOW_BATCHES
        )

    def record(
        self, attempted: int, throttled: int, retry_after_seconds: float | None = None,
    ) -> None:
        if throttled > 0 and retry_after_seconds is not None and retry_after_seconds > 0:
            self.retry_after_until = max(
                self.retry_after_until, time.monotonic() + retry_after_seconds
            )
            log.warning(
                "FUT.GG Retry-After received; pausing sales requests for at least %.0fs",
                retry_after_seconds,
            )
        if attempted > 0 and throttled >= attempted:
            self.consecutive_fully_throttled_batches += 1
        else:
            self.consecutive_fully_throttled_batches = 0

        self._recent_batches.append((attempted, throttled))

        if throttled:
            # Proportional to how badly we overshot: a batch that was
            # entirely rejected deserves a harder cut than one that lost a
            # single request. Unconditional - a genuine 429 is never
            # forgiven for THIS batch's own backoff, only for whether it
            # resets the recovery streak below.
            severity = throttled / max(attempted, 1)
            self.delay = min(self.max_delay, max(self.delay, 0.25) * (1.0 + 2.0 * severity))
            if severity > 0.25:
                self.batch_size = max(self.min_batch, self.batch_size - 1)

            window_attempted = sum(a for a, _ in self._recent_batches)
            window_throttled = sum(t for _, t in self._recent_batches)
            window_ratio = (window_throttled / window_attempted) if window_attempted else 1.0
            forgivable = (
                window_attempted >= THROTTLE_ROLLING_MIN_ATTEMPTED
                and window_ratio <= THROTTLE_ROLLING_TOLERANCE
            )
            if not forgivable:
                self._clean_streak = 0
            return

        self._clean_streak += 1
        # Ease back in slowly, and only after sustained-or-forgiven
        # success - the limiter is likely windowed, so one clean batch
        # proves little.
        if self._clean_streak >= THROTTLE_RECOVERY_STREAK:
            self._clean_streak = 0
            self.delay = max(self.min_delay, self.delay * 0.8)
            self.batch_size = min(self.max_batch, self.batch_size + 1)

    def cooldown_remaining(self) -> float:
        return max(0.0, self.retry_after_until - time.monotonic())

    def next_wait_seconds(self) -> float:
        return max(self.delay, self.cooldown_remaining())

    def describe(self) -> str:
        remaining = self.cooldown_remaining()
        suffix = f" retry_after={remaining:.0f}s" if remaining > 0 else ""
        return f"batch={self.batch_size} delay={self.delay:.2f}s{suffix}"


#: AdaptiveThrottle only ever lives in-process. In continuous mode that's
#: fine - it keeps learning for as long as the worker runs. But this
#: worker also ships a "bounded" mode (MAX_RUNTIME_MINUTES/MAX_CYCLES,
#: see run_forever()) specifically so it can run as a cheap scheduled
#: task instead of a always-on service - and every one of those scheduled
#: runs starts a brand new process, which throws the learned
#: batch_size/delay away and re-learns FUT.GG's rate limit through a
#: fresh wave of HTTP 429s each time, even though the limit itself hasn't
#: changed since the last run minutes or hours earlier. Persisting the
#: last-known-good values (best-effort, mirroring auto_sync/http_client.py's
#: already-Postgres-backed rate limiter/circuit breaker for the price/bin
#: workers) lets a new run start already-tuned instead of re-discovering
#: the same limit from scratch every single time.
_THROTTLE_STATE_DDL = """
CREATE TABLE IF NOT EXISTS futgg_sales_throttle_state (
    id         SMALLINT PRIMARY KEY DEFAULT 1,
    batch_size INTEGER NOT NULL,
    delay      REAL NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (id = 1)
)
"""


async def load_throttle_state(pool: asyncpg.Pool) -> tuple[int, float] | None:
    """Best-effort. Returns (batch_size, delay) learned by a previous run,
    or None if there is no saved state yet (first-ever run) or the read
    failed - either way the caller falls back to the static
    BATCH_SIZE/BATCH_DELAY defaults, exactly like before this existed."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(_THROTTLE_STATE_DDL)
            row = await conn.fetchrow(
                "SELECT batch_size, delay FROM futgg_sales_throttle_state WHERE id = 1"
            )
        if row is None:
            return None
        return int(row["batch_size"]), float(row["delay"])
    except Exception:
        log.warning("failed to load persisted throttle state - starting from defaults", exc_info=True)
        return None


async def save_throttle_state(pool: asyncpg.Pool, throttle: "AdaptiveThrottle") -> None:
    """Best-effort. A failure here must never interrupt scraping - losing
    the persisted tuning just means the next run re-learns it, same as
    every run did before this existed."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(_THROTTLE_STATE_DDL)
            await conn.execute(
                """
                INSERT INTO futgg_sales_throttle_state (id, batch_size, delay, updated_at)
                VALUES (1, $1, $2, NOW())
                ON CONFLICT (id) DO UPDATE
                    SET batch_size = EXCLUDED.batch_size,
                        delay = EXCLUDED.delay,
                        updated_at = NOW()
                """,
                throttle.batch_size, throttle.delay,
            )
    except Exception:
        log.warning("failed to persist throttle state", exc_info=True)


def circuit_breaker_tripped(
    stats: dict[str, Any], throttle: "AdaptiveThrottle | None" = None,
) -> bool:
    """True once a cycle's running failure ratio reaches
    CIRCUIT_BREAKER_THRESHOLD, but only after at least
    CIRCUIT_BREAKER_MIN_SAMPLE cards have actually been attempted (item 6)
    - a single unlucky batch of 8 must not trip a 5-minute backoff; the
    production incident this guards against was ~100% failed over 200
    cards, not a blip."""
    # A cumulative cycle ratio hides a late hard 429 wall after a long
    # successful prefix. Two consecutive batches in which every request
    # is throttled are stronger and more recent evidence than that diluted
    # ratio, so stop promptly and let auto mode move to page_capture.
    if (
        throttle is not None
        and throttle.consecutive_fully_throttled_batches
        >= CONSECUTIVE_FULL_THROTTLE_BATCHES
    ):
        return True
    attempted = stats["ok"] + stats["failed"]
    if attempted < CIRCUIT_BREAKER_MIN_SAMPLE:
        return False
    return (stats["failed"] / attempted) >= CIRCUIT_BREAKER_THRESHOLD


def next_backoff_seconds(consecutive_trips: int) -> float:
    """How long to back off after a circuit-breaker trip.

    A single trip gets exactly CIRCUIT_BREAKER_BACKOFF_SECONDS, same as
    always. But production showed a cycle re-tripping within seconds of
    resuming from that full wait - the fixed backoff had not actually
    let FUT.GG's rate limit clear, and immediately retrying into a still
    -active limit only spends more requests against it for nothing. Each
    consecutive trip (no clean cycle in between) doubles the wait, up to
    CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS, so the worker waits longer only
    once the shorter wait has already proven insufficient - not on the
    first trip, which is most often just ordinary throttling recovering
    fine on schedule.
    """
    if consecutive_trips <= 0:
        return CIRCUIT_BREAKER_BACKOFF_SECONDS
    multiplier = 2 ** (consecutive_trips - 1)
    return min(
        CIRCUIT_BREAKER_BACKOFF_SECONDS * multiplier, CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS
    )


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


def sales_interval_minutes(tier: str | None) -> int:
    """`tier` is a card_priority.effective_tier value (CRITICAL/HIGH/
    NORMAL/LOW/DORMANT) - see select_due()'s `effective_tier` column and
    apply_batch_results()'s use of it. An unrecognised or missing tier
    (a card_priority row that predates a new tier value, or absent
    entirely) falls back to NORMAL, matching market_data_provider.py's
    DEFAULT_SALES_TIER so scheduling and staleness-reporting never
    quietly disagree about an unscored card."""
    return SALES_INTERVALS.get(tier or "NORMAL", SALES_INTERVALS["NORMAL"])


# The signing + fetch pair, executed inside the page so it is same-origin
# and carries the browser's own cookies/TLS. Promise.all means a batch of
# N cards costs ONE round trip to the browser instead of N.
#
# Built from futgg_price_access.build_batch_script() - the canonical
# sign -> (solve a structured sha256-prefix-v1 proof-of-work challenge,
# if FUT.GG returns one; otherwise fail exactly as before) -> resubmit
# -> fetch implementation, shared with scripts/futgg_price_probe.py's
# manual diagnostic tool rather than reimplemented here a second time.
# See that module's docstring for the full challenge-type distinction:
# a STRUCTURED price-access challenge (data.challenge present, algorithm
# "sha256-prefix-v1") is expected application behaviour and is solved
# in-browser; an UNSTRUCTURED Cloudflare/browser challenge
# (challengeRequired with no data.challenge, or an unrecognised
# algorithm) is NOT solvable by computing anything and still fails
# straight into this file's existing circuit-breaker/backoff/transport-
# switch handling below, unchanged.
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
_FETCH_SCRIPT = build_batch_script()


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
        # PLATFORM and GAME_YEAR are both part of the fingerprint so a
        # sale on a different platform or a different game cycle that
        # otherwise collides (same card id, same second, same price,
        # same occurrence) is never treated as the same row and deduped
        # against it via ON CONFLICT. GAME_YEAR matters here for the
        # same reason migration 095 added it to the table itself: a
        # source_card_id can genuinely be shared by an FC26 and an FC27
        # card (confirmed live, not just Icons - see that migration's
        # comment), and this worker only ever runs one game year at a
        # time, but the numeric id alone can't tell two years apart.
        raw = f"{card_id}|{sold_at.isoformat()}|{price}|{occurrence}|{PLATFORM}|{GAME_YEAR}"
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        age_seconds = max(0, int((captured_at - sold_at).total_seconds()))
        tax = int(price * 0.05)
        rows.append(
            (
                card_id, price, price, tax, price - tax, sold_at,
                f"{age_seconds // 60} minutes ago", age_seconds,
                position, occurrence, fingerprint, captured_at, PLATFORM,
                int(GAME_YEAR),
            )
        )
    return rows


_INSERT = """
INSERT INTO futgg_sales_history (
    source_card_id, listed_price, sold_price, ea_tax, net_price,
    approximate_sold_at, source_age_text, source_age_seconds,
    source_row_position, occurrence_index, source_fingerprint, captured_at,
    platform, game_year
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
ON CONFLICT (source_fingerprint) DO NOTHING
"""

_BIN_INSERT = """
INSERT INTO futgg_bin_history (source_card_id, lowest_bin, captured_at, platform, game_year)
VALUES ($1,$2,$3,$4,$5)
"""


def build_bin_row(card_id: int, current_price: Any, captured_at: datetime) -> tuple | None:
    """Map one card's currentPrice object onto a futgg_bin_history row.

    FUT.GG stopped serving the r2.fut.gg bulk catalogue feed
    (player-prices-index/player-prices-<platform>-dyn) to individual
    player pages - confirmed live 2026-09-18: three separate production
    captures of every response fut.gg made to a real player page (~120
    responses each, including several OTHER r2.fut.gg feeds such as
    manifest.json and metarank-definition, proving r2.fut.gg itself is
    still reachable) never once included it, and a HAR of a real browser
    session for the same page confirms it now fetches prices exclusively
    via this same signed /api/fut/player-prices/{year}/{id}/ endpoint
    this worker already calls for sales. futgg_bulk_price_sync.py's
    "canonical BIN source" premise (see the AUDIT NOTE this replaced,
    just above in apply_batch_results) is dead along with the feed it
    depended on - this is the replacement, using data already fetched on
    every card this worker processes, at no extra network cost.

    isExtinct/isSbc/isObjective/isUntradeable all mean the number in
    `price` does not describe a real open-market BIN (an extinct card,
    an SBC-only reward, an objective reward, or a card that cannot be
    listed at all) - skipped rather than written as if it were one.
    """
    if not isinstance(current_price, dict):
        return None
    if current_price.get("isExtinct") or current_price.get("isSbc"):
        return None
    if current_price.get("isObjective") or current_price.get("isUntradeable"):
        return None
    price = current_price.get("price")
    if price is None:
        return None
    try:
        price = int(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    priced_at = _parse_sold_at(current_price.get("priceUpdatedAt"), captured_at) or captured_at
    return (card_id, price, priced_at, PLATFORM, int(GAME_YEAR))


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


def classify_page_capture_error(
    *,
    nav_status: int | None,
    nav_cf_mitigated: str | None,
    data_status: int | None,
    data_cf_mitigated: str | None,
    parse_error: bool,
    price_section_count: int | None,
) -> str:
    """Classify a page_capture attempt which produced no JSON payload.

    `no_price_section` is deliberately narrow: the page itself must have
    loaded normally (HTTP 200, no Cloudflare challenge), the price API must
    not have returned an error, and the stable price-panel element must be
    absent. This is the observed distinction between untradeable SBC cards
    and a known tradeable comparison card.
    """
    if nav_cf_mitigated == "challenge":
        return "page_capture_nav_challenge"
    if data_cf_mitigated == "challenge":
        return "page_capture_challenge"
    if data_status == 403:
        return "page_capture_403"
    if (
        nav_status == 200
        and data_status is None
        and price_section_count == 0
    ):
        return "no_price_section"
    if data_status is None:
        return "page_capture_timeout"
    if parse_error:
        return "malformed_json"
    return f"page_capture_status_{data_status}"


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

    # The navigation's OWN response (page.goto()'s return value) - captured
    # separately from the API subresponse above so a failure can be
    # attributed correctly: if Cloudflare challenges the PAGE navigation
    # itself (the real FUT.GG app's JS bundle never loads, so it never
    # makes its own organic price request at all), that is a categorically
    # different failure from the page loading fine but the API subrequest
    # being challenged - and from the API subrequest simply never firing
    # for an unrelated reason (slow hydration, JS error). Without this,
    # "no response ever captured" (page_capture_timeout) was
    # indistinguishable from "the page itself was Cloudflare's interstitial
    # the whole time" - the 2026-08-04 production run needed exactly this
    # to move the investigation forward.
    nav_status: int | None = None
    nav_cf_mitigated: str | None = None
    price_section_count: int | None = None

    page.on("response", on_response)
    try:
        try:
            nav_response = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            if nav_response is not None:
                nav_status = nav_response.status
                try:
                    nav_headers = await nav_response.all_headers()
                    nav_cf_mitigated = nav_headers.get("cf-mitigated")
                except Exception:
                    pass
        except Exception as exc:
            return {"eaId": card_id, "error": f"navigation_failed:{type(exc).__name__}"}

        # Same idiom as PriceSync's HYDRATION_WAIT_MS: the price request
        # fires during hydration, after domcontentloaded - minimum wait,
        # not full page load (item 3).
        #
        # Guarded like the navigation step above it, unlike before: a
        # crashed Chromium renderer (Playwright surfaces this as
        # "Page crashed") raised straight out of this await, uncaught,
        # all the way through process_batch_page_capture()'s
        # asyncio.gather() and into run_forever()'s main loop - killing
        # the ENTIRE worker process over a single bad page, not just
        # this one card. Confirmed in production: repeated
        # "Page.wait_for_timeout: Page crashed" crash-loops with zero
        # sales written for 24h straight, each cron run dying here
        # before completing any work. A crashed page can't reliably
        # continue past this point either way, so failing just this
        # card - exactly like a failed navigation already does - is the
        # right outcome, not a process-wide crash.
        try:
            await page.wait_for_timeout(PAGE_SETTLE_MS)
        except Exception as exc:
            return {"eaId": card_id, "error": f"page_capture_crashed:{type(exc).__name__}"}

        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=10
                )
            except asyncio.TimeoutError:
                pass

        # A successful page navigation which never requests price JSON can
        # mean either slow/broken hydration or a card with no market. FUT.GG
        # exposes a stable structural distinction: market cards render
        # #prices-overview; confirmed SBC reward pages do not.
        try:
            price_section_count = await page.locator("#prices-overview").count()
        except Exception:
            # If DOM inspection itself fails, retain the ordinary timeout
            # path. Never classify an unknown browser failure as no-market.
            price_section_count = None
    finally:
        page.remove_listener("response", on_response)

    if "payload" not in captured:
        status = captured.get("status")
        error = classify_page_capture_error(
            nav_status=nav_status,
            nav_cf_mitigated=nav_cf_mitigated,
            data_status=status,
            data_cf_mitigated=captured.get("cf_mitigated"),
            parse_error=bool(captured.get("parse_error")),
            price_section_count=price_section_count,
        )
        result: dict[str, Any] = {"eaId": card_id, "error": error}
        # Always included, not diagnostic-only: cheap (status/one header,
        # no secrets), and exactly the field needed to tell a page-level
        # block apart from an API-level one during normal circuit-breaker
        # -driven runs, not just one-card diagnostic mode.
        result["navStatus"] = nav_status
        result["navCfMitigated"] = nav_cf_mitigated
        result["priceSectionCount"] = price_section_count
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


def _row_value(row, key: str, default=None):
    """Tolerant column read.

    asyncpg.Record raises KeyError for an absent column rather than
    returning None, and process_batch can legitimately see a row selected
    before ensure_schema added these columns (a worker mid-deploy against
    a database the new schema has not reached yet). Treating that as
    "no value" keeps the failure path working instead of turning a schema
    lag into a crash loop on the very worker this change exists to calm.
    """
    try:
        value = row[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


async def select_due(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Pick the next cards to read.

    Cards that have NEVER been read come first, regardless of tier.

    That ordering matters more than it looks. Sorting by priority tier
    first starves the tail: the CRITICAL/HIGH tiers alone generate
    enough "due" volume at their cadence that, if throughput sits
    anywhere near it, unclassified/lower-tier cards never reach the
    front of the queue and never get a FIRST read at all - they would
    sit permanently at zero sales, and a card with no sales returns
    insufficient_data forever no matter how fresh its BIN is.

    Backfill-first guarantees every card gets covered once, after which
    NULLs are exhausted and the ordering settles into normal
    tier-priority maintenance.

    Backfill preference now keys on sales_first_read_at, NOT on
    next_sales_due_at being NULL. Those used to be the same test, which
    meant a never-read card that failed - and so was left with a NULL
    due-at on purpose, to keep its place - was also, unavoidably, due
    again immediately. It came straight back to the front of this query
    on the very next cycle, and a card that could never succeed did that
    forever. Splitting the two keeps the intent (unread cards outrank
    routine maintenance) while letting a failure carry a real retry time,
    so the preference is over cards that are DUE, not a licence to retry
    without limit.

    ONCE BACKFILL IS EXHAUSTED (confirmed in production - "0 never_read"),
    a plain priority order lets the fast tiers (CRITICAL, HIGH) fill the
    whole SELECT_LIMIT every cycle and never reach the tail at all - see
    _TIER_MIN_SHARE's own comment for why tier, not rating, is the axis
    this bands on. So this first reserves each tier its guaranteed floor
    (still backfill-first, still ordered by how overdue, just scoped to
    that tier), then fills whatever capacity is left with the original
    global priority order, excluding whatever the floors already picked
    so nothing is selected twice. A quiet cycle for a fast tier lets
    that remaining capacity reach further into whichever tier actually
    has the backlog, rather than being rigidly fixed to a per-tier
    count.

    Every row carries `effective_tier` (card_priority.effective_tier,
    defaulting to NORMAL for an unscored card) so apply_batch_results()
    can schedule the NEXT read against the same tier this one was
    selected under, via sales_interval_minutes().

    Both queries below are scoped to GAME_YEAR. Without it, this would
    also select from a prior cycle's cards - futgg_players.is_active
    stays TRUE for them forever (kept queryable as historical/reference
    data, see backend/app/services/game_cycle.py) - splitting this
    worker's fixed per-cycle SELECT_LIMIT between the live catalogue and
    a game nobody is generating new sales for, for no benefit.
    """
    guaranteed: list[asyncpg.Record] = []
    guaranteed_ids: list[int] = []
    for tier, share in _TIER_MIN_SHARE.items():
        floor = int(SELECT_LIMIT * share)
        if floor <= 0:
            continue
        rows = await conn.fetch(
            f"""
            SELECT source_card_id, rating, next_sales_due_at, source_url,
                   sales_first_read_at, sales_consecutive_failures,
                   COALESCE(cp.effective_tier, 'NORMAL') AS effective_tier
            FROM futgg_players
            LEFT JOIN card_priority cp ON cp.card_id = futgg_players.source_card_id
            WHERE is_active
              AND is_tradeable IS DISTINCT FROM FALSE
              AND game_year = $2
              AND (next_sales_due_at IS NULL OR next_sales_due_at <= NOW())
              AND {_TIER_WHERE[tier]}
            ORDER BY
                (sales_first_read_at IS NOT NULL),
                next_sales_due_at ASC NULLS FIRST,
                rating DESC NULLS LAST
            LIMIT $1
            """,
            floor, int(GAME_YEAR),
        )
        guaranteed.extend(rows)
        guaranteed_ids.extend(int(r["source_card_id"]) for r in rows)

    remaining = SELECT_LIMIT - len(guaranteed)
    if remaining <= 0:
        return guaranteed

    overflow = await conn.fetch(
        """
        SELECT source_card_id, rating, next_sales_due_at, source_url,
               sales_first_read_at, sales_consecutive_failures,
               COALESCE(cp.effective_tier, 'NORMAL') AS effective_tier
        FROM futgg_players
        LEFT JOIN card_priority cp ON cp.card_id = futgg_players.source_card_id
        WHERE is_active
          AND is_tradeable IS DISTINCT FROM FALSE
          AND game_year = $3
          AND (next_sales_due_at IS NULL OR next_sales_due_at <= NOW())
          AND NOT (source_card_id = ANY($1::bigint[]))
        ORDER BY
            -- never-SUCCESSFULLY-read cards first (backfill), most
            -- urgent tier within that. A failed attempt does not
            -- forfeit this.
            (sales_first_read_at IS NOT NULL),
            CASE COALESCE(cp.effective_tier, 'NORMAL')
                 WHEN 'CRITICAL' THEN 0
                 WHEN 'HIGH' THEN 1
                 WHEN 'NORMAL' THEN 2
                 WHEN 'LOW' THEN 3
                 ELSE 4 END,
            next_sales_due_at ASC NULLS FIRST,
            rating DESC NULLS LAST
        LIMIT $2
        """,
        guaranteed_ids,
        remaining,
        int(GAME_YEAR),
    )
    return guaranteed + list(overflow)


async def report_coverage(conn: asyncpg.Connection) -> dict[str, int] | None:
    """How much of the catalogue actually has sales yet.

    The single number that says whether the intelligence layer can work:
    a card with fewer than MIN_SALES_FOR_SIGNAL sales produces no signal
    at all, however good its price data is.

    Returns the raw counts so the caller can put them in the heartbeat.
    Coverage is the metric that says whether the collector is winning or
    losing: a run can report cards_ok>0 every cycle and still be falling
    behind the 72h freshness gate, which is precisely what a throttled
    transport looks like from the outside. None when the catalogue is
    empty and there is nothing to measure.
    """
    row = await conn.fetchrow(
        """
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE s.n > 0) AS any_sales,
            count(*) FILTER (WHERE s.n >= 5) AS enough_sales,
            -- Keyed on read history, not on due-at: a never-read card now
            -- carries a retry time like any other, so the old
            -- next_sales_due_at IS NULL test would under-count it to zero
            -- and hide exactly the population this metric exists to watch.
            count(*) FILTER (WHERE p.sales_first_read_at IS NULL) AS never_read,
            count(*) FILTER (
                WHERE p.sales_first_read_at IS NULL
                  AND p.sales_consecutive_failures > 0
            ) AS never_read_and_failing,
            count(*) FILTER (WHERE p.sales_consecutive_failures >= 5) AS chronic_failures
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
        return None
    log.info(
        "sales coverage: %d/%d have any sales (%.1f%%) | %d/%d have >=5 and can "
        "produce a signal (%.1f%%) | %d never read",
        row["any_sales"], total, 100.0 * row["any_sales"] / total,
        row["enough_sales"], total, 100.0 * row["enough_sales"] / total,
        row["never_read"],
    )
    # Surfaced separately because this is the population that used to
    # generate unbounded retries: before the backoff fix each of these was
    # re-attempted every cycle rather than on any schedule at all.
    if row["never_read_and_failing"] or row["chronic_failures"]:
        log.info(
            "sales retry health: %d never-read cards are failing | %d cards have "
            ">=5 consecutive failures (now capped at one attempt per %d min)",
            row["never_read_and_failing"], row["chronic_failures"],
            min(FAILURE_BACKOFF_LADDER[-1], FAILURE_BACKOFF_MAX_MINUTES),
        )
    return {
        "total": total,
        "any_sales": int(row["any_sales"] or 0),
        "enough_sales": int(row["enough_sales"] or 0),
        "never_read": int(row["never_read"] or 0),
        "chronic_failures": int(row["chronic_failures"] or 0),
    }


#: Worker name this collector reports under in pipeline_heartbeats.
#:
#: SalesSync writes its own row rather than reusing the retired
#: 'futgg_price_sync' name. That row is a tombstone: futgg_price_sync.py
#: was the per-card Playwright price scraper this module replaced, it is
#: no longer deployed (see Procfile and docker-compose.yml, both of which
#: say so), and its last heartbeat is frozen at whatever it wrote on its
#: final run - `ok: true`, forever. The backend's freshness panel and
#: readiness check both read this table, so for as long as the live sales
#: collector wrote nothing, the only sales-shaped row they could see was
#: that frozen one. A three-week-old `ok: true` is worse than no row at
#: all: it answers "are sales being collected?" with a confident yes.
HEARTBEAT_WORKER = "futgg_sales_sync"

#: Below this, a cycle is not collecting fast enough to matter.
#:
#: The failure this exists to catch is not a crash. It is the transport
#: falling back to page_capture under FUT.GG rate limiting and grinding
#: out single-digit cards per cycle at minutes per card - a worker that
#: is up, logging, and losing ground against the 72h freshness gate the
#: engine applies to sales. cards_ok>0 does not distinguish that from
#: healthy running; throughput does.
HEARTBEAT_MIN_CARDS_PER_MIN = env_float(
    "FUTGG_SALES_HEARTBEAT_MIN_CARDS_PER_MIN", 1.0, minimum=0.0
)


async def write_heartbeat(
    pool: asyncpg.Pool,
    *,
    transport: str,
    stats: dict[str, Any],
    elapsed: float,
    breaker_tripped: bool,
    coverage: dict[str, int] | None,
) -> None:
    """Record this cycle's outcome in pipeline_heartbeats.

    `ok` is deliberately strict. It means "this cycle collected sales at a
    rate that keeps the catalogue inside the freshness gate", not "the
    process is alive" - liveness is already carried by last_run_at. A
    throughput collapse means the feed is draining even though nothing
    crashed, and that is the state the backend needs to be able to see.

    `breaker_tripped` does NOT by itself make a cycle not-ok. Production
    evidence (1 September) showed that assumption was wrong: with
    PAGE_CAPTURE_FALLBACK_ON_THROTTLE off (see that constant), hitting
    FUT.GG's rate-limit wall after ~150-175 productive cards is now the
    normal, sustainable steady state, not a failure - a real cycle
    logged cards_ok=153 cards_per_min=27.9 circuit_breaker=tripped, and
    the old "trip means not ok" rule marked a cycle that was comfortably
    beating the throughput the catalogue actually needs as unhealthy.
    That drowns out the one thing this field exists to catch: a cycle
    that trips immediately having collected nothing (cards_ok=0,
    0 cards/min) still reads not-ok on cards_per_min alone, with no need
    to also look at breaker_tripped - so dropping it from this decision
    loses no real detection and stops crying wolf on healthy cycles.
    breaker_tripped still rides along in `detail` below, since it is
    useful context for whoever is reading the row.

    Best-effort throughout: monitoring must never take down the worker it
    watches, and `monitoring` is imported here rather than at module load
    so a missing optional dependency cannot stop the collector from
    running.
    """
    try:
        from monitoring import heartbeat
    except Exception as e:  # pragma: no cover - import environment only
        log.warning("heartbeat unavailable: %s", e)
        return

    cards_ok = int(stats.get("ok", 0))
    failed = int(stats.get("failed", 0))
    cards_per_min = (cards_ok / elapsed) * 60 if elapsed > 0 else 0.0

    ok = cards_ok > 0 and cards_per_min >= HEARTBEAT_MIN_CARDS_PER_MIN

    parts = [
        f"transport={transport}",
        f"cards_ok={cards_ok}",
        f"failed={failed}",
        f"sales={int(stats.get('sales_seen', 0))}",
        f"cards_per_min={cards_per_min:.1f}",
    ]
    if breaker_tripped:
        parts.append("circuit_breaker=tripped")
    if coverage and coverage.get("total"):
        total = coverage["total"]
        parts.append(
            f"coverage={coverage['any_sales']}/{total}"
            f" ({100.0 * coverage['any_sales'] / total:.1f}%)"
        )
        # The number that actually gates the engine: fewer than
        # MIN_SALES_FOR_SIGNAL sales and the card produces nothing,
        # however fresh its price is.
        parts.append(
            f"signal_capable={coverage['enough_sales']}"
            f" ({100.0 * coverage['enough_sales'] / total:.1f}%)"
        )
    errors = stats.get("errors") or {}
    if errors:
        top = sorted(errors.items(), key=lambda kv: -kv[1])[:3]
        parts.append("errors=" + ",".join(f"{k}:{v}" for k, v in top))

    try:
        async with pool.acquire() as conn:
            await heartbeat(conn, HEARTBEAT_WORKER, ok=ok, detail=" ".join(parts))
    except Exception as e:
        log.warning("heartbeat write failed: %s", e)


async def write_lock_skipped_heartbeat(pool: asyncpg.Pool, *, waited_seconds: float) -> None:
    """Record that this invocation did NOT run at all - the advisory
    lock was still held by another instance after waiting the full
    retry window.

    THE GAP THIS FIXES. The old code just logged a warning and exited.
    A scheduled invocation that skips this way looks, from the outside,
    identical to one that simply never fired - no pipeline_heartbeats
    row is written either way, so a previous instance stuck for hours
    (see runtime_budget_exhausted's own docstring for how that can
    happen) can silently cancel one or more of the NEXT scheduled
    invocations too, with nothing anywhere recording that it happened.
    Reuses the existing pipeline_heartbeats mechanism (monitoring.py) -
    no new table, no new alerting channel - `ok=False` and
    "skipped_due_to_lock" in `detail` are enough for the same freshness/
    readiness checks that already read this table to notice.
    """
    try:
        from monitoring import heartbeat
    except Exception as e:  # pragma: no cover - import environment only
        log.warning("heartbeat unavailable: %s", e)
        return
    detail = (
        f"skipped_due_to_lock lock_key={SALES_SYNC_ADVISORY_LOCK_KEY} "
        f"waited_seconds={waited_seconds:.0f} "
        f"at={datetime.now(timezone.utc).isoformat()}"
    )
    try:
        async with pool.acquire() as conn:
            await heartbeat(conn, HEARTBEAT_WORKER, ok=False, detail=detail)
    except Exception as e:
        log.warning("lock-skip heartbeat write failed: %s", e)


#: How long a DDL statement in ensure_schema() may wait for its lock
#: before giving up. Deliberately short. See ensure_schema's docstring:
#: the alternative is inheriting the pool's 120s command_timeout, which
#: is long enough to kill the worker outright and short enough to leave
#: an ACCESS EXCLUSIVE request queued on futgg_players for two minutes,
#: blocking every writer behind it while it waits.
SCHEMA_LOCK_TIMEOUT_MS = env_int("FUTGG_SALES_SCHEMA_LOCK_TIMEOUT_MS", 5000, minimum=250)


async def _existing_columns(conn: asyncpg.Connection, table: str) -> set[str]:
    """Column names on `table`, or an empty set if it does not exist.

    Reads pg_attribute rather than information_schema: no locks, no
    permission-dependent view filtering, one index scan.
    """
    rows = await conn.fetch(
        """
        SELECT a.attname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = $1 AND n.nspname = current_schema()
          AND a.attnum > 0 AND NOT a.attisdropped
        """,
        table,
    )
    return {r["attname"] for r in rows}


async def _existing_indexes(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
    )
    return {r["indexname"] for r in rows}


async def _apply_schema_changes(
    conn: asyncpg.Connection, needed: list[tuple[str, str]],
) -> None:
    """Run the DDL that introspection found genuinely missing.

    Under a short lock_timeout, because the alternative is what killed
    the worker in production: an ACCESS EXCLUSIVE request on
    futgg_players sitting in the lock queue for the pool's full 120s
    command_timeout, blocking every writer behind it the entire time and
    then raising anyway.

    A lock failure is NOT automatically fatal. Whether it matters depends
    on why the statement was queued, and the catalogue can answer that:
    if the object exists after all - a concurrent deploy applied it, or
    the introspection raced - the worker has everything it needs and
    carries on. Only a genuinely absent object aborts startup, which is
    the case that always deserved to.
    """
    if not needed:
        log.info("schema: already current, no DDL required")
        return

    log.info(
        "schema: %d change(s) required: %s",
        len(needed), ", ".join(label for label, _ in needed),
    )
    blocked: list[str] = []
    try:
        await conn.execute(f"SET lock_timeout = {SCHEMA_LOCK_TIMEOUT_MS}")
        for label, sql in needed:
            try:
                await conn.execute(sql)
                log.info("schema: applied %s", label)
            except (asyncpg.exceptions.LockNotAvailableError, asyncio.TimeoutError):
                # futgg_players is written continuously by the bulk price
                # sync; losing a race for its lock is ordinary, not an
                # error, and says nothing about whether the object exists.
                log.warning(
                    "schema: could not acquire a lock for %s within %dms - "
                    "will verify whether it is actually needed",
                    label, SCHEMA_LOCK_TIMEOUT_MS,
                )
                blocked.append(label)
    finally:
        # RESET, not a second SET: the pool hands this connection on to
        # the rest of the worker, whose statements must not inherit a
        # 5-second lock timeout.
        await conn.execute("RESET lock_timeout")

    if not blocked:
        return

    columns = await _existing_columns(conn, "futgg_players")
    columns |= {f"futgg_sales_history.{c}" for c in await _existing_columns(conn, "futgg_sales_history")}
    indexes = await _existing_indexes(conn)
    still_missing = []
    for label in blocked:
        name = label.rsplit(".", 1)[-1]
        if name in columns or name in indexes:
            continue
        if label == "futgg_sales_history table" and await _existing_columns(conn, "futgg_sales_history"):
            continue
        still_missing.append(label)

    if still_missing:
        raise RuntimeError(
            "schema changes are required but their locks could not be acquired: "
            + ", ".join(still_missing)
            + ". futgg_players is likely under sustained write load - retry when "
            "the bulk price sync is between cycles."
        )
    log.info(
        "schema: %d blocked statement(s) turned out to be unnecessary - "
        "the objects already exist", len(blocked),
    )


async def ensure_schema(conn: asyncpg.Connection) -> None:
    """Bring the schema up to what this worker needs - doing nothing at
    all when it already is.

    WHY THE INTROSPECTION
    --------------------
    Every statement below is written IF NOT EXISTS, which makes them
    idempotent in EFFECT but not in COST. `ALTER TABLE ... ADD COLUMN IF
    NOT EXISTS` still takes an ACCESS EXCLUSIVE lock on the table to
    discover there is nothing to do, and futgg_players is the single
    busiest table in the system: futgg_bulk_price_sync.py holds row locks
    on it for the 62-66 seconds its write stage takes, every cycle.

    That collision is not theoretical. Production, 2026-08-14 15:05:22:

        File "/app/futgg_sales_sync.py", line 1003, in ensure_schema
          await conn.execute(
        TimeoutError

    Line 1003 was `ADD COLUMN IF NOT EXISTS next_sales_due_at` - a column
    that had existed for weeks. It waited out the pool's whole 120s
    command_timeout for a lock it did not need, then killed the worker on
    startup. And because this worker runs bounded (see
    MAX_RUNTIME_MINUTES) and is restarted, it re-ran this DDL every 45
    minutes forever, each time queuing an ACCESS EXCLUSIVE request that
    blocks every writer behind it for as long as it waits.

    So: look first, and only take a lock when there is real work. On a
    steady deployment this function now performs two catalogue reads and
    no DDL whatsoever.

    WHY IT NO LONGER DIES ON A LOCK
    -------------------------------
    A blocked lock and a missing column are completely different
    situations, and inheriting command_timeout conflated them. DDL now
    runs under a short lock_timeout, and a failure is re-checked against
    the catalogue: if the schema turns out to be correct anyway - someone
    else applied it, or it was always there - the worker continues.
    It aborts only when something it genuinely needs is genuinely absent.
    """
    needed: list[tuple[str, str]] = []

    sales_columns = await _existing_columns(conn, "futgg_sales_history")
    bin_columns = await _existing_columns(conn, "futgg_bin_history")
    player_columns = await _existing_columns(conn, "futgg_players")
    indexes = await _existing_indexes(conn)

    # futgg_sales_history is owned by futgg_price_sync.ensure_schema, and
    # this worker is meant to REPLACE that one - so on any database where
    # price sync has been retired (or never ran) the table this worker
    # writes to would not exist. Own it here too; CREATE TABLE IF NOT
    # EXISTS keeps the two definitions safely idempotent while both
    # workers coexist during the changeover.
    if not sales_columns:
        needed.append(("futgg_sales_history table", """
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
        """))
    # Defaults to 'ps5' (this worker's own PLATFORM default) so every
    # pre-existing row - there has only ever been one platform's data in
    # this table - stays correctly labelled. Lets a second instance of
    # this worker (FUTGG_SALES_PLATFORM=pc) safely coexist: without this,
    # a pc sale and a ps5 sale could collide on source_fingerprint (see
    # build_sale_rows) and get silently deduped against each other via
    # ON CONFLICT DO NOTHING, discarding a genuine sale.
    if "platform" not in sales_columns:
        needed.append((
            "futgg_sales_history.platform",
            "ALTER TABLE futgg_sales_history ADD COLUMN IF NOT EXISTS "
            "platform TEXT NOT NULL DEFAULT 'ps5'",
        ))
    # Confirmed live (see migration 095): a source_card_id can be shared
    # by an FC26 and an FC27 card - not just Icons, ordinary cards too -
    # and without this column futgg_market_snapshot's per-year LATERAL
    # join pulled the exact same sales rows for both years. Defaults to
    # 27 for the same reason migration 095 does: every row this worker
    # has ever written was written under FUTGG_GAME_YEAR=27.
    if "game_year" not in sales_columns:
        needed.append((
            "futgg_sales_history.game_year",
            "ALTER TABLE futgg_sales_history ADD COLUMN IF NOT EXISTS "
            "game_year SMALLINT NOT NULL DEFAULT 27",
        ))
    # Both the snapshot view and the coverage query filter sales by card
    # and recency; without this they degrade to sequential scans once the
    # table passes a few million rows.
    if "futgg_sales_history_card_sold_idx" not in indexes:
        needed.append((
            "futgg_sales_history_card_sold_idx",
            "CREATE INDEX IF NOT EXISTS futgg_sales_history_card_sold_idx "
            "ON futgg_sales_history (source_card_id, approximate_sold_at DESC)",
        ))

    # futgg_bin_history was owned by futgg_price_sync.py, then by
    # futgg_bulk_price_sync.py's own r2.fut.gg catalogue feed. That feed
    # is gone (see build_bin_row()'s docstring) and this worker is now
    # the one writing current BIN data, so it needs to be able to create
    # its own table on a database where neither of those ever ran -
    # same reasoning as futgg_sales_history just above.
    if not bin_columns:
        needed.append(("futgg_bin_history table", """
        CREATE TABLE IF NOT EXISTS futgg_bin_history (
            id BIGSERIAL PRIMARY KEY,
            source_card_id BIGINT NOT NULL
                REFERENCES futgg_players(source_card_id),
            lowest_bin INTEGER NOT NULL,
            price_range_low INTEGER,
            price_range_high INTEGER,
            source_age_text TEXT,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """))
    if "platform" not in bin_columns:
        needed.append((
            "futgg_bin_history.platform",
            "ALTER TABLE futgg_bin_history ADD COLUMN IF NOT EXISTS "
            "platform TEXT NOT NULL DEFAULT 'ps5'",
        ))
    if "game_year" not in bin_columns:
        needed.append((
            "futgg_bin_history.game_year",
            "ALTER TABLE futgg_bin_history ADD COLUMN IF NOT EXISTS "
            "game_year SMALLINT NOT NULL DEFAULT 27",
        ))
    if "futgg_bin_history_card_captured_idx" not in indexes:
        needed.append((
            "futgg_bin_history_card_captured_idx",
            "CREATE INDEX IF NOT EXISTS futgg_bin_history_card_captured_idx "
            "ON futgg_bin_history (source_card_id, captured_at DESC)",
        ))

    # Sales get their own due-at column: the price cadence is now driven
    # by the bulk feed and is far shorter, so sharing next_price_due_at
    # would make the two workers fight over the same schedule.
    if "next_sales_due_at" not in player_columns:
        needed.append((
            "futgg_players.next_sales_due_at",
            "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS next_sales_due_at TIMESTAMPTZ",
        ))
    if "last_sales_status" not in player_columns:
        needed.append((
            "futgg_players.last_sales_status",
            "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS last_sales_status TEXT",
        ))
    # Added for incident recovery: without a timestamp of when
    # last_sales_status was actually written, a repair after a bad
    # deploy (like the 2026-08-04 data_403 incident - see COOLIFY.md's
    # "SalesSync incident repair" section) can only scope "which rows
    # were touched during the incident" by reverse-engineering it from
    # next_sales_due_at, which only works for as long as the backoff
    # constant that wrote it stays unchanged and no later write has
    # touched the row since. This column makes any future repair exact.
    if "last_sales_status_at" not in player_columns:
        needed.append((
            "futgg_players.last_sales_status_at",
            "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS last_sales_status_at TIMESTAMPTZ",
        ))

    # "Never read" used to be expressed as next_sales_due_at IS NULL,
    # which forced one column to mean two different things - "we have
    # never got data for this card" AND "this card is due right now" -
    # and that conflation is exactly the retry loop (see
    # failure_backoff_minutes). These two columns separate them.
    #
    # sales_first_read_at is set once, on the first genuinely successful
    # read, and never cleared. A failure cannot change it, so backfill
    # preference in select_due() survives any number of failed attempts
    # while next_sales_due_at is free to carry a real retry time.
    if "sales_first_read_at" not in player_columns:
        needed.append((
            "futgg_players.sales_first_read_at",
            "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS sales_first_read_at TIMESTAMPTZ",
        ))
    if "sales_consecutive_failures" not in player_columns:
        needed.append((
            "futgg_players.sales_consecutive_failures",
            "ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS "
            "sales_consecutive_failures INTEGER NOT NULL DEFAULT 0",
        ))

    if "futgg_players_sales_due_idx" not in indexes:
        needed.append((
            "futgg_players_sales_due_idx",
            "CREATE INDEX IF NOT EXISTS futgg_players_sales_due_idx "
            "ON futgg_players (next_sales_due_at) "
            "WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE",
        ))
    # select_due() now orders on this first; without an index the
    # backfill-preference sort degrades to a full scan every cycle.
    if "futgg_players_sales_first_read_idx" not in indexes:
        needed.append((
            "futgg_players_sales_first_read_idx",
            "CREATE INDEX IF NOT EXISTS futgg_players_sales_first_read_idx "
            "ON futgg_players (sales_first_read_at) "
            "WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE",
        ))
    await _apply_schema_changes(conn, needed)

    # Backfill for databases written by the previous logic. Under it, a
    # non-NULL next_sales_due_at could ONLY have been written by a card
    # that had been read at least once (the failure branch explicitly
    # skipped never-read cards), so it is a sound witness of "has been
    # read". last_sales_status_at is the closest real timestamp we hold;
    # now() is the honest fallback when even that is absent.
    #
    # Guarded by an EXISTS probe rather than run blind. The WHERE clause
    # already made it a no-op after the first boot, but a no-op UPDATE
    # still scans futgg_players and still contends with the bulk price
    # sync's 62-second write window - on every restart, i.e. every 45
    # minutes. The probe answers the same question with an index lookup
    # that stops at the first matching row.
    if "sales_first_read_at" in player_columns or needed:
        has_backfill = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM futgg_players
                WHERE sales_first_read_at IS NULL AND next_sales_due_at IS NOT NULL
            )
            """
        )
        if has_backfill:
            updated = await conn.execute(
                """
                UPDATE futgg_players
                SET sales_first_read_at = COALESCE(last_sales_status_at, now())
                WHERE sales_first_read_at IS NULL AND next_sales_due_at IS NOT NULL
                """
            )
            log.info("schema: backfilled sales_first_read_at (%s)", updated)


#: Throttle-worthy error markers - anything that looks like FUT.GG
#: pushing back on request volume/origin (rate limit, WAF/Cloudflare
#: block, bot challenge, upstream failure) rather than a genuine
#: application-level bug (malformed payload, missing signed url). Drives
#: AdaptiveThrottle, which must react to more than just 429s (item 7) - a
#: 403 wall or a sign_5xx means "slow down" exactly as a 429 does. Also
#: what the circuit breaker's auto-transport-switch (item 4) looks for -
#: "challenge" covers signed_fetch's unstructured_challenge/
#: unknown_challenge_algorithm (see futgg_price_access.py - a
#: STRUCTURED sha256-prefix-v1 challenge is solved in-browser and never
#: reaches here as an error at all) and page_capture's
#: page_capture_challenge uniformly.
#:
#: "pow_" added for the structured sha256-prefix-v1 proof-of-work
#: challenge (see futgg_price_access.py): a card whose challenge could
#: not be solved in budget (pow_exhausted/pow_timeout) is throttle-
#: shaped evidence exactly like a 429 - FUT.GG demanding more work per
#: request - but deliberately NOT added to _BLOCKING_ERROR_MARKERS
#: below: unlike an outright 403/Cloudflare wall, nothing here shows
#: page_capture would fare any better against the same challenge, and
#: that file's own history documents the cost of switching transport on
#: a signal that only meant "slow down".
_THROTTLE_ERROR_MARKERS = (
    "429", "403", "sign_5", "data_5", "challenge", "pow_",
    "failed to fetch", "network",
)


def is_throttle_signal(error: str) -> bool:
    error = str(error).lower()
    return any(marker in error for marker in _THROTTLE_ERROR_MARKERS)


#: Errors that mean this TRANSPORT is being refused, as opposed to errors
#: that merely mean we are going too fast.
#:
#: The distinction is load-bearing and its absence was expensive. Every
#: throttle marker above used to also authorise the auto-mode transport
#: switch, so a burst of HTTP 429s - which is FUT.GG saying "slow down",
#: nothing to do with which transport we use - tripped the breaker and
#: flipped the worker from signed_fetch to page_capture. That trade is
#: catastrophic for cost: signed_fetch is a ~25 KB JSON fetch, while
#: page_capture is a full ~275 KB page navigation with JS rendering and a
#: 4-second settle for EVERY card. Roughly a tenfold cost increase, chosen
#: automatically, in response to a signal that specifically means
#: "requests are too frequent".
#:
#: Worse, it is sticky: after switching, signed_fetch is only reprobed
#: every SIGNED_REPROBE_MINUTES, and a failed reprobe parks it on
#: page_capture again. A single overload burst could therefore leave the
#: worker on the expensive transport indefinitely.
#:
#: A 429 is answered by backing off, which AdaptiveThrottle already does
#: (it still reacts to the full marker list above - that behaviour is
#: unchanged). Only an outright refusal of this transport - a 403 wall or
#: a bot challenge - is evidence that a DIFFERENT transport is needed. 5xx
#: is excluded too: an upstream hiccup says nothing about transport.
_BLOCKING_ERROR_MARKERS = ("403", "challenge", "failed to fetch", "network")


def is_blocking_signal(error: str) -> bool:
    """True only for errors meaning this transport is being refused."""
    error = str(error).lower()
    return any(marker in error for marker in _BLOCKING_ERROR_MARKERS)


#: How many CONSECUTIVE cycles signed_fetch may trip the breaker on
#: throttle-shaped failures (429/5xx) before we fall back to page_capture
#: anyway.
#:
#: Refusing to switch on a 429 is right for a burst - the remedy is to
#: back off, and switching would multiply cost per card for a problem
#: caused by sending too many requests. But refusing FOREVER is worse than
#: the disease: if signed_fetch is persistently rate-limited, never
#: falling back means the worker simply returns no sales data at all,
#: indefinitely. Sales are the anchor for every recommendation the engine
#: makes, so "cheap and blank" is not a better outcome than "expensive and
#: working".
#:
#: So: back off first, fall back only if backing off did not fix it.
#: Blocking-shaped failures (403/challenge) still switch immediately -
#: backing off cannot fix a wall.
SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK = env_int(
    "FUTGG_SALES_SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK", 3, minimum=1
)

#: Whether a persistent THROTTLE (429/5xx) is allowed to fall back to
#: page_capture at all. Default OFF.
#:
#: The reasoning above this constant assumed page_capture was merely
#: expensive but working - "expensive and working" beats "cheap and
#: blank". Production evidence (31 August) showed that assumption was
#: wrong: once switched, page_capture managed cards_ok=2 failed=18 in
#: 2311.5s (0 cards/min), with per-card fetches up to 21 minutes and
#: repeated Playwright page crashes. signed_fetch, meanwhile, was
#: collecting 150-185 cards per cycle before the wall hit. Falling back
#: on throttle was not a tradeoff of cost for reliability - it was a
#: straight downgrade, from "slow but working" to "not working", exactly
#: at the moment the worker most needed to keep flowing.
#:
#: So by default a throttle-shaped trip never switches transport - it
#: just keeps backing off signed_fetch, cycle after cycle, which is still
#: strictly better than the fallback as currently observed. Blocking-
#: shaped failures (403/challenge, see _mostly_challenge_failures) are
#: unaffected by this flag and still switch immediately, since page_capture
#: exists specifically because organic navigation can get past a
#: Cloudflare challenge that an injected fetch() cannot - that is a real
#: capability difference, not a cost tradeoff, and no amount of backing
#: off fixes a wall. Flip this back on once page_capture's own throughput
#: and crash rate have actually been fixed.
PAGE_CAPTURE_FALLBACK_ON_THROTTLE = (
    os.environ.get("FUTGG_SALES_PAGE_CAPTURE_FALLBACK_ON_THROTTLE", "").strip().lower()
    in ("1", "true", "yes")
)

#: Consecutive breaker trips on signed_fetch caused by throttling rather
#: than blocking. Reset by any successful cycle.
_consecutive_signed_throttle_trips = 0


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
            "sales fetch failed [page_capture] card=%s error=%s navStatus=%s "
            "navCfMitigated=%s priceSectionCount=%s dataStatus=%s "
            "dataContentType=%r cfMitigated=%s%s",
            card_id, result.get("error"), result.get("navStatus"), result.get("navCfMitigated"),
            result.get("priceSectionCount"), result.get("dataStatus"),
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


#: Playwright error text meaning the page's own renderer process died -
#: as opposed to a normal HTTP-level failure (429/403/5xx/timeout) that
#: page.evaluate() would otherwise raise for. Matched as a substring
#: against str(exc): Playwright's own wording for this is stable
#: ("Target crashed") but not documented as a typed exception, so this
#: is the same "match on the message" approach the rest of this file
#: already uses for HTTP-shaped errors (see _THROTTLE_ERROR_MARKERS).
_CRASHED_PAGE_ERROR_MARKERS = ("Target crashed", "has been closed")


def is_crashed_page_error(exc: BaseException) -> bool:
    """True for an exception meaning the page itself is gone/dead, not
    merely that one request failed.

    THE BUG THIS FIXES. signed_fetch reuses ONE page for the entire
    process lifetime (see this module's own docstring) - a real
    efficiency win while the page is healthy, but a crashed page NEVER
    recovers on its own: every subsequent page.evaluate() call against
    it raises the exact same error forever. Before this existed,
    process_batch's except-block treated a crash identically to an
    ordinary throttled batch (see SignedPageHandle.recreate's own
    docstring for the production evidence) - backing off and retrying
    against a page that backing off can never fix, escalating the
    circuit-breaker's backoff toward its cap while producing zero
    sales, cycle after cycle, until something external (a container
    restart) happened to replace it.
    """
    text = str(exc)
    return any(marker in text for marker in _CRASHED_PAGE_ERROR_MARKERS)


class SignedPageHandle:
    """Mutable holder for signed_fetch's reused anchor page.

    A plain Page reference can't be transparently replaced: Python
    passes it by value, so a page recreated inside process_batch would
    never be seen by run_forever's own `signed_page` variable, or by
    the next call into process_batch on the following batch. Wrapping
    it in one shared, mutable object lets process_batch recreate the
    page in place - every caller reads `self.page` fresh each time
    rather than holding a stale reference.
    """

    def __init__(self, page, context, anchor: str) -> None:
        self.page = page
        self.context = context
        self.anchor = anchor

    async def recreate(self) -> None:
        """Best-effort: replace a crashed/closed page with a fresh one
        navigated back to the same anchor.

        Production evidence (2026-09-04): the same "Target crashed"
        error recurred on the same anchor page every cycle for at least
        six minutes, each recurrence escalating the circuit breaker's
        backoff (300s -> 600s, toward its 1800s cap) while attempting
        (and failing) only a handful of cards each time - the page was
        permanently dead, and nothing before this existed ever created
        a new one.

        A failure here is logged, not raised: the caller (process_batch)
        must still be able to record this batch as failed and return -
        a failed recreation just means the NEXT batch tries again
        against whatever `self.page` currently is, which the same crash
        detection catches again.
        """
        old_page = self.page
        try:
            new_page = await self.context.new_page()
            await new_page.goto(
                self.anchor, wait_until="domcontentloaded", timeout=TIMEOUT_MS
            )
        except Exception:
            log.warning(
                "failed to recreate signed_fetch anchor page after a crash - "
                "still using the old (crashed) page for now",
                exc_info=True,
            )
            return
        self.page = new_page
        log.warning("signed_fetch anchor page recreated after a crash: %s", self.anchor)
        try:
            if not old_page.is_closed():
                await old_page.close()
        except Exception:
            pass


async def process_batch(
    page_handle: "SignedPageHandle", pool: asyncpg.Pool, rows, stats, timers, throttle,
    diagnostic: bool = False,
) -> None:
    ids = [int(r["source_card_id"]) for r in rows]

    with timers.track("fetch_batch"):
        try:
            results = await page_handle.page.evaluate(
                _FETCH_SCRIPT,
                {"ids": ids, "gameYear": GAME_YEAR, "platform": PLATFORM, "diagnostic": diagnostic},
            )
        except Exception as exc:
            log.warning("batch fetch failed", exc_info=True)
            stats["batch_errors"] += 1
            throttle.record(len(ids), len(ids))
            crashed = is_crashed_page_error(exc)
            try:
                crashed = crashed or page_handle.page.is_closed()
            except Exception:
                pass
            if crashed:
                await page_handle.recreate()
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
    bin_rows: list[tuple] = []
    updates: list[tuple] = []
    throttled = 0
    retry_after_seconds = 0.0
    logged_failures = 0
    for result in results or []:
        card_id = int(result.get("eaId"))
        row = by_id.get(card_id)
        # select_due() carries effective_tier on every row it selects;
        # a row from a hand-rolled query (run_diagnostic_card, a stale
        # reprobe sample) that never selected the column falls back to
        # NORMAL, same default the tier itself uses for an unscored card.
        tier = _row_value(row, "effective_tier", "NORMAL") if row is not None else "NORMAL"
        # True only for a card that has NEVER had a successful (or even
        # attempted) read before this batch - see select_due()'s
        # never-read-first ordering and report_coverage()'s never_read
        # count, both keyed off sales_first_read_at IS NULL.
        was_never_read = (
            row is not None and _row_value(row, "sales_first_read_at") is None
        )
        prior_failures = int(_row_value(row, "sales_consecutive_failures") or 0)
        error = result.get("error")

        if error:
            if error == "no_price_section":
                # This is a classified no-market page, not a broken fetch.
                # Record the reason in the existing status column and move
                # it out of the hot queue for a week. Do not set
                # is_tradeable=false permanently: a mistaken/temporary DOM
                # result will self-heal on the scheduled recheck.
                stats["excluded_no_price_section"] = (
                    stats.get("excluded_no_price_section", 0) + 1
                )
                updates.append((
                    card_id, NO_PRICE_SECTION_RECHECK_MINUTES,
                    "excluded:no_price_section", False, prior_failures,
                ))
                continue

            stats["failed"] += 1
            stats.setdefault("errors", {})
            stats["errors"][error] = stats["errors"].get(error, 0) + 1
            if logged_failures < MAX_FAILURE_SAMPLE_LOGS_PER_BATCH:
                log_failure_sample(card_id, result, transport=transport)
                logged_failures += 1

            if is_throttle_signal(error):
                throttled += 1
                server_delay = parse_retry_after_seconds(result.get("retryAfter"))
                if server_delay is not None:
                    retry_after_seconds = max(retry_after_seconds, server_delay)

            # DATA-INTEGRITY FIX (item 4): a failed fetch of ANY kind
            # (data_403, sign_502, unstructured_challenge, network errors,
            # malformed payloads, ...) must never count as a successful
            # read. A never-read card must stay never-read so it keeps
            # its place at the front of the backfill queue and the
            # never_read coverage count doesn't fall on a cycle that wrote
            # zero real sales. This was the fix for an earlier production
            # bug: every failed card used to get
            # `next_sales_due_at = NOW() + 5 minutes` regardless of
            # success, which silently converted "never read" into "read
            # (but failed)" - 200 cards/cycle, exactly matching the
            # observed never_read drop.
            #
            # ...and it still does not: sales_first_read_at is only ever
            # written by a SUCCESS below, so a failure cannot convert
            # "never read" into "read (but failed)" and cannot cost a card
            # its backfill priority.
            #
            # What HAS changed is that every failure - never-read included
            # - now carries a real retry time. Leaving never-read failures
            # with a NULL due-at was what produced the hot loop: NULL meant
            # both "keep your place in the queue" and "due right now", so a
            # card that could never succeed was re-attempted every cycle
            # indefinitely. The backoff escalates with the consecutive
            # failure count and is capped, so a permanently-broken card
            # settles at one cheap attempt per cap rather than thousands
            # per day, while a transient failure still retries almost as
            # quickly as before (ladder step 1 is
            # FAILURE_RETRY_BACKOFF_MINUTES).
            failures = prior_failures + 1
            updates.append((
                card_id,
                failure_backoff_minutes_for_error(
                    error, failures, parse_retry_after_seconds(result.get("retryAfter"))
                ),
                f"error:{error}", False, failures,
            ))
            continue

        # UPDATED (2026-09-18, was: AUDIT NOTE, Part 3 price-detail
        # field-usage review): currentPrice used to be read only for the
        # two DIAGNOSTIC log lines, deliberately never persisted, because
        # futgg_bulk_price_sync.py was this deployment's canonical BIN
        # source for the whole catalogue and a second, independently-timed
        # current-price value from here would have been exactly the
        # "parallel competing price source" this codebase avoids
        # elsewhere. That premise no longer holds: FUT.GG stopped serving
        # the bulk r2.fut.gg feed that worker depends on (confirmed live -
        # see build_bin_row()'s docstring for the evidence), so this is
        # now the only source of current BIN data at all, not a second
        # one. The full detailed response's other fields (liveAuctions/
        # history/priceRange/overview/momentum) are still not fetched by
        # this batch script - only reachable today via
        # futgg_price_access.fetch_player_price_detail()'s single-card
        # path (scripts/futgg_price_probe.py). None have a database
        # column yet; each needs a real captured example (not a
        # description) and a specific, checked hypothesis for what it
        # would improve before one is added.
        bin_row = build_bin_row(card_id, result.get("currentPrice"), captured_at)
        if bin_row is not None:
            bin_rows.append(bin_row)
        sale_rows = build_sale_rows(card_id, result.get("completedAuctions") or [], captured_at)
        to_insert.extend(sale_rows)
        stats["ok"] += 1
        stats["sales_seen"] += len(sale_rows)
        # A genuinely successful fetch counts as read whether or not it
        # returned any sales this time - zero completedAuctions for a
        # well-formed response is a real observation ("checked, nothing
        # new"), not a failure (item 4).
        # A success stamps sales_first_read_at (once - COALESCE below keeps
        # the original) and clears the consecutive-failure count, so a card
        # that recovers after an outage is not left on a punitive backoff.
        updates.append((card_id, sales_interval_minutes(tier), "success", True, 0))

    if throttle is not None:
        throttle.record(len(rows), throttled, retry_after_seconds or None)

    if not APPLY:
        log.info(
            "FUTGG_SALES_APPLY=false - not writing to database. Would have "
            "inserted %d sale row(s), %d bin row(s), and updated %d card(s) (sample: %s)",
            len(to_insert), len(bin_rows), len(updates), updates[:10],
        )
        return

    with timers.track("db_write"):
        for attempt in range(1, DB_WRITE_MAX_ATTEMPTS + 1):
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        if to_insert:
                            # executemany: one round trip for the whole batch
                            # instead of one per sale. The old scraper issued up
                            # to 50 sequential inserts per card.
                            await conn.executemany(_INSERT, to_insert)
                        if bin_rows:
                            await conn.executemany(_BIN_INSERT, bin_rows)
                        if updates:
                            # One set-based statement, not one round trip per
                            # card. Sorting also makes concurrent writers take
                            # row locks in a consistent order.
                            ordered = sorted(updates, key=lambda u: u[0])
                            await conn.execute(
                                """
                                UPDATE futgg_players p
                                SET next_sales_due_at = NOW() + (u.minutes * INTERVAL '1 minute'),
                                    last_sales_status = u.status,
                                    last_sales_status_at = NOW(),
                                    sales_first_read_at = CASE
                                        WHEN u.succeeded
                                        THEN COALESCE(p.sales_first_read_at, NOW())
                                        ELSE p.sales_first_read_at
                                    END,
                                    sales_consecutive_failures = u.failures
                                FROM unnest(
                                    $1::bigint[], $2::int[], $3::text[],
                                    $4::boolean[], $5::int[]
                                ) AS u(card_id, minutes, status, succeeded, failures)
                                WHERE p.source_card_id = u.card_id
                                """,
                                [u[0] for u in ordered], [u[1] for u in ordered],
                                [u[2] for u in ordered], [u[3] for u in ordered],
                                [u[4] for u in ordered],
                            )
                break
            except asyncpg.exceptions.DeadlockDetectedError:
                if attempt >= DB_WRITE_MAX_ATTEMPTS:
                    log.error(
                        "database deadlock persisted after %d write attempts",
                        attempt,
                    )
                    raise
                delay = DB_WRITE_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                delay += random.uniform(0.0, DB_WRITE_RETRY_BASE_SECONDS)
                log.warning(
                    "database deadlock on write attempt %d/%d; transaction rolled "
                    "back, retrying in %.2fs",
                    attempt, DB_WRITE_MAX_ATTEMPTS, delay,
                )
                await asyncio.sleep(delay)


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
    """Whether a cycle's failures mean this TRANSPORT is being refused
    (403 wall / bot challenge), rather than merely that we are sending
    requests too quickly.

    Only the former justifies switching transport. This used to count
    every throttle signal - including HTTP 429 - which meant a burst of
    rate limiting flipped the worker onto page_capture, a transport an
    order of magnitude more expensive per card, in response to a signal
    that means "slow down". See _BLOCKING_ERROR_MARKERS.
    """
    errors = stats.get("errors", {})
    total_failed = stats["failed"]
    if total_failed == 0:
        return False
    blocking = sum(count for err, count in errors.items() if is_blocking_signal(err))
    return (blocking / total_failed) >= 0.5


def _throttle_is_saturated(throttle: "AdaptiveThrottle | None") -> bool:
    """Has backing off already been taken as far as it goes?

    If the batch is at its floor and the delay at (or near) its ceiling
    and requests are STILL being refused, then "slow down" has been tried
    to exhaustion and is not the answer. Checking this makes the fallback
    decision self-contained within a single cycle, which matters because
    the consecutive-trip counter below lives in process memory: under
    FUTGG_SALES_MAX_RUNTIME_MINUTES the process exits every run, so a
    streak spanning runs can never accumulate and the fallback would
    otherwise never fire at all.
    """
    if throttle is None:
        return False
    return (
        throttle.batch_size <= throttle.min_batch
        and throttle.delay >= throttle.max_delay * 0.9
    )


def should_fall_back_to_page_capture(
    stats: dict[str, Any], throttle: "AdaptiveThrottle | None" = None,
) -> tuple[bool, str]:
    """Decide, after the breaker has tripped on signed_fetch, whether to
    switch transport. Returns (switch, reason).

    Two distinct situations, deliberately handled differently:

      * A wall (403 / bot challenge) - switch immediately. No amount of
        backing off gets past a refusal, and page_capture is the whole
        reason that fallback exists.

      * Throttling (429 / 5xx) - do NOT switch on the first trip. The
        remedy for "too many requests" is fewer requests, and switching
        would raise the cost per card roughly tenfold in response to a
        signal caused by volume. AdaptiveThrottle is already backing off.
        But if it keeps tripping across
        SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK consecutive cycles, backing
        off has not worked, and continuing to fail cheaply just means no
        sales data at all - so fall back and keep the pipeline alive.
    """
    global _consecutive_signed_throttle_trips

    if _mostly_challenge_failures(stats):
        _consecutive_signed_throttle_trips = 0
        failed = stats["failed"]
        total = stats["ok"] + stats["failed"]
        return True, (
            f"circuit breaker tripped on signed_fetch ({failed}/{total} failed, "
            "predominantly blocking-shaped: 403/challenge)"
        )

    if not PAGE_CAPTURE_FALLBACK_ON_THROTTLE:
        # See PAGE_CAPTURE_FALLBACK_ON_THROTTLE's own comment: page_capture
        # is disabled as a throttle response until its throughput and
        # crash rate are fixed, so a 429/5xx wall just keeps backing off
        # signed_fetch, cycle after cycle, rather than downgrading to a
        # transport that currently manages ~0 cards/min.
        _consecutive_signed_throttle_trips += 1
        return False, (
            "signed_fetch throttled (not blocked) - backing off, not switching "
            "transport (PAGE_CAPTURE_FALLBACK_ON_THROTTLE is off: page_capture's "
            "last known throughput does not justify the downgrade)"
        )

    if (
        throttle is not None
        and throttle.consecutive_fully_throttled_batches
        >= CONSECUTIVE_FULL_THROTTLE_BATCHES
    ):
        _consecutive_signed_throttle_trips = 0
        return True, (
            f"signed_fetch had {throttle.consecutive_fully_throttled_batches} "
            "consecutive fully-throttled batches - switching immediately to "
            "page_capture instead of letting earlier cycle successes hide the 429 wall"
        )

    _consecutive_signed_throttle_trips += 1
    if _throttle_is_saturated(throttle):
        _consecutive_signed_throttle_trips = 0
        return True, (
            "signed_fetch still refused with the throttle already at its floor "
            f"({throttle.describe()}) - backing off further cannot help, falling "
            "back to page_capture so sales data keeps flowing"
        )
    if _consecutive_signed_throttle_trips >= SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK:
        trips = _consecutive_signed_throttle_trips
        _consecutive_signed_throttle_trips = 0
        return True, (
            f"signed_fetch throttled (not blocked) for {trips} consecutive cycles "
            "despite backoff - falling back to page_capture so sales data keeps "
            "flowing, at higher cost per card"
        )
    return False, (
        f"signed_fetch throttled (not blocked) - backing off rather than switching "
        f"transport (trip {_consecutive_signed_throttle_trips} of "
        f"{SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK} before fallback)"
    )


def note_signed_fetch_recovered() -> None:
    """A clean cycle means the backoff worked - forget the trip streak."""
    global _consecutive_signed_throttle_trips
    _consecutive_signed_throttle_trips = 0


async def _reprobe_signed_fetch(
    signed_page_handle: "SignedPageHandle", pool: asyncpg.Pool, sample: list
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
    await process_batch(signed_page_handle, pool, sample, probe_stats, StageTimers(), probe_throttle)

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


def runtime_deadline(started_at: float) -> float | None:
    """Monotonic deadline for a bounded run, or None if unbounded
    (MAX_RUNTIME_MINUTES == 0). Computed once, from run_forever's own
    `run_started_at`, so every later check compares against a fixed
    point in time rather than re-deriving "how much is left" from a
    moving now()."""
    if not MAX_RUNTIME_MINUTES:
        return None
    return started_at + MAX_RUNTIME_MINUTES * 60


def runtime_budget_exhausted(deadline: float | None, *, now: float | None = None) -> bool:
    """True once a bounded run's deadline has passed. Always False for
    an unbounded run (deadline is None).

    THE BUG THIS FIXES. run_forever's MAX_RUNTIME_MINUTES check used to
    live only at the top of the outer per-cycle while loop - a cycle
    already in flight, including every batch inside select_due()'s up-
    to-SELECT_LIMIT due list, ran to completion regardless of how long
    that took. At a degraded throttle (batch=1, delay=20s observed in
    production), a single cycle selecting SELECT_LIMIT=400 cards could
    take on the order of 400 * ~20s = ~8000s (over two hours) - many
    times the configured 45-minute bound - with nothing in the process
    itself able to stop it early. This (and its sibling check at every
    batch-dispatch site in run_forever) closes that gap: called before
    every batch, not just every cycle.
    """
    if deadline is None:
        return False
    return (now if now is not None else time.monotonic()) >= deadline


async def sleep_within_budget(
    seconds: float,
    shutdown_event: asyncio.Event,
    deadline: float | None,
    *,
    now: float | None = None,
) -> tuple[bool, bool]:
    """Sleep up to `seconds` via sleep_or_shutdown, but never past a
    bounded run's deadline. Returns (shutdown_requested, budget_exhausted).

    If the deadline has already passed, or sleeping the full `seconds`
    would cross it, this returns immediately with budget_exhausted=True
    and never calls sleep_or_shutdown at all - "finish the run cleanly"
    rather than "sleep out however much of the budget is left and then
    discover the deadline at the next check" (a circuit-breaker backoff
    of up to CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS=3600s is exactly the
    case this matters for: sleeping 20 of the last 25 remaining minutes
    of a 45-minute bounded run for a backoff that then has no time left
    to be useful is strictly worse than ending the run now).
    """
    if deadline is not None:
        current = now if now is not None else time.monotonic()
        remaining = deadline - current
        if remaining <= 0 or seconds >= remaining:
            return False, True
    shutdown = await sleep_or_shutdown(seconds, shutdown_event)
    return shutdown, False


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


async def acquire_advisory_lock_with_retry(
    lock_conn: Any, key: int, *, retry_seconds: float, poll_seconds: float,
    sleep=asyncio.sleep, now=time.monotonic,
) -> bool:
    """pg_try_advisory_lock(), retrying for up to `retry_seconds` if it's
    already held.

    A single failed attempt only ever meant "another SalesSync is running"
    safely in the old bounded/cron-schedule world, where the previous run
    would finish and free the lock on its own schedule regardless of what
    this container did. Running continuously changed that: a rolling
    deploy starts the NEW container while the OLD one is still up, so the
    very first attempt here is *expected* to lose that race - and if this
    container gives up immediately (as it used to), nothing else ever
    restarts it, so a routine deploy silently stops the worker for good.
    The old instance releases the lock within seconds of its own SIGTERM
    handling (see run_forever's finally block), so a short retry window
    turns that race into a brief handoff instead. `sleep`/`now` are
    injectable so tests never actually sleep.
    """
    got = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", key)
    deadline = now() + retry_seconds
    while not got and now() < deadline:
        await sleep(poll_seconds)
        got = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", key)
    return bool(got)


async def _launch_browser_resources(
    playwright, *, need_signed: bool, need_page_capture: bool, anchor: str,
):
    """Launch a fresh browser+context and, per the active transport(s), a
    signed_fetch anchor page and/or a page_capture pool - the exact set of
    long-lived Playwright objects run_forever keeps alive for as long as
    one browser instance lasts. Shared by the initial startup and by
    run_forever's periodic recycle (see BROWSER_RECYCLE_MINUTES) so both
    paths build these resources identically.
    """
    browser = await playwright.chromium.launch(
        headless=HEADLESS, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
    )
    context = await browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        viewport={'width': 1920, 'height': 1080},
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        # Do not force navigation-only Sec-Fetch-* / Upgrade-Insecure-Requests
        # headers at context level. They also get applied to JavaScript
        # fetch() POSTs to /api/fut/price-access/sign/, causing Chromium
        # net::ERR_INVALID_ARGUMENT before a response is received.
        # Chromium supplies the correct request-specific headers itself.
        locale='en-GB',
    )

    signed_page_handle = None
    if need_signed:
        signed_page = await context.new_page()
        # ONE navigation for the entire process lifetime. Everything
        # after this is fetch() from within the already-loaded page.
        await signed_page.goto(anchor, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        log.info("anchor page loaded (signed_fetch transport): %s", anchor)
        # Wrapped so process_batch can transparently recreate this
        # page in place after a crash (see SignedPageHandle) - a
        # raw Page reference can't be replaced from inside a
        # function call the way this shared, mutable handle can.
        signed_page_handle = SignedPageHandle(signed_page, context, anchor)

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

    return browser, context, signed_page_handle, capture_pages


async def run_forever() -> None:
    from playwright.async_api import async_playwright

    pool = await create_pool_with_retry(
        DATABASE_URL, min_size=1, max_size=4, command_timeout=120
    )
    # Session advisory lock: only one scheduled/continuous SalesSync may
    # own the queue at a time, even if Railway starts a new container
    # before the previous one has exited - a rolling deploy on a
    # continuous service does exactly this on every single deploy, so
    # the retry here (not just one attempt) is what keeps a routine
    # deploy from being indistinguishable from the worker being stopped.
    lock_conn = await pool.acquire()
    got_lock = await acquire_advisory_lock_with_retry(
        lock_conn, SALES_SYNC_ADVISORY_LOCK_KEY,
        retry_seconds=LOCK_ACQUIRE_RETRY_SECONDS,
        poll_seconds=LOCK_ACQUIRE_POLL_SECONDS,
    )
    if not got_lock:
        log.warning(
            "another SalesSync still owns advisory lock %d after %.0fs - exiting cleanly",
            SALES_SYNC_ADVISORY_LOCK_KEY, LOCK_ACQUIRE_RETRY_SECONDS,
        )
        await write_lock_skipped_heartbeat(pool, waited_seconds=LOCK_ACQUIRE_RETRY_SECONDS)
        await pool.release(lock_conn)
        await pool.close()
        return
    async with pool.acquire() as conn:
        await ensure_schema(conn)
        anchor = await conn.fetchval(
            """
            SELECT source_url FROM futgg_players
            WHERE is_active AND source_url IS NOT NULL
            ORDER BY rating DESC NULLS LAST LIMIT 1
            """
        )
    anchor = anchor or "https://www.fut.gg/players/"

    log.info(
        "%s starting: transport=%s platform=%s batch=%d page_concurrency=%d "
        "select_limit=%d intervals=%s run_mode=%s",
        SCRIPT_VERSION, TRANSPORT_MODE, PLATFORM, BATCH_SIZE, PAGE_CONCURRENCY,
        SELECT_LIMIT, SALES_INTERVALS,
        (
            f"bounded({MAX_RUNTIME_MINUTES}min)" if MAX_RUNTIME_MINUTES
            else f"bounded({MAX_CYCLES} cycles)" if MAX_CYCLES
            else "continuous"
        ),
    )
    # Immediately after the intervals are printed, so the warning sits
    # next to the numbers it is about. A band scraped less often than the
    # engine accepts sales for can never produce a recommendation, and
    # nothing else in the system is positioned to notice.
    log_cadence_gate_conflicts()

    shutdown_event = asyncio.Event()
    _install_shutdown_handlers(shutdown_event)

    need_signed = TRANSPORT_MODE in ("signed_fetch", "auto")
    need_page_capture = TRANSPORT_MODE in ("page_capture", "auto")

    async with Stealth().use_async(async_playwright()) as patched_playwright:
        playwright = patched_playwright
        browser, context, signed_page_handle, capture_pages = await _launch_browser_resources(
            playwright, need_signed=need_signed, need_page_capture=need_page_capture, anchor=anchor,
        )
        # See BROWSER_RECYCLE_MINUTES's own docstring - tracks how long
        # the CURRENT browser instance has been alive, reset on every
        # recycle, so the periodic-recycle check below is a plain
        # elapsed-time comparison rather than re-deriving it from
        # run_started_at (which must keep counting across recycles).
        browser_started_at = time.monotonic()
        try:
            persisted = await load_throttle_state(pool)
            throttle = AdaptiveThrottle(
                BATCH_SIZE, BATCH_DELAY,
                resume_batch_size=persisted[0] if persisted else None,
                resume_delay=persisted[1] if persisted else None,
            )
            if persisted:
                log.info(
                    "resuming throttle from a previous run: %s (bounds batch<=%d delay>=%.2fs)",
                    throttle.describe(), BATCH_SIZE, BATCH_DELAY,
                )

            # Run-level summary counters (item D) - a small, fixed set of
            # running totals logged once at shutdown, not a metrics
            # framework: every field here already exists per-cycle
            # (stats/breaker_tripped/throttle.describe()), this just adds
            # them up across the whole invocation so a bounded run's one
            # log line answers "was this run healthy overall" without
            # having to reconstruct it from N per-cycle lines by hand.
            run_start_throttle_desc = throttle.describe()
            run_totals = {
                "ok": 0, "failed": 0, "sales_seen": 0,
                "throttle_errors": 0, "breaker_trips": 0,
                "breaker_sleep_seconds": 0.0,
            }
            # True once any runtime-budget check (top-of-loop, mid-cycle
            # batch dispatch, or a backoff/throttle-delay sleep that
            # would have crossed the deadline) is the reason this run
            # ended, as opposed to MAX_CYCLES, an empty due queue, or a
            # shutdown signal.
            run_ended_by_runtime_budget = False

            run_started_at = time.monotonic()
            # Fixed point in time, computed once - see runtime_deadline()
            # and runtime_budget_exhausted()'s own docstrings. Every
            # runtime-budget check in this loop (outer per-cycle check
            # below, every batch dispatch inside a cycle, every backoff/
            # throttle-delay sleep) compares against this SAME deadline,
            # so a bounded run's total wall-clock time is bounded no
            # matter which of those checks ends up being the one that
            # fires.
            deadline = runtime_deadline(run_started_at)
            cycles_done = 0
            # Consecutive breaker trips with no clean cycle in between -
            # see next_backoff_seconds(). Reset the moment a cycle
            # actually completes without tripping.
            consecutive_cycle_trips = 0

            while not shutdown_event.is_set():
                # Bounded-run check, at the TOP of the loop - this alone
                # is NOT sufficient (see runtime_budget_exhausted's
                # docstring: a cycle already in flight used to run to
                # completion regardless of how long that took), but it's
                # still the cheap, common-case exit for the normal case
                # where the previous cycle finished promptly.
                if runtime_budget_exhausted(deadline):
                    run_ended_by_runtime_budget = True
                    log.info(
                        "bounded run complete: %d minute limit reached after %d cycle(s) "
                        "- exiting cleanly (FUTGG_SALES_MAX_RUNTIME_MINUTES)",
                        MAX_RUNTIME_MINUTES, cycles_done,
                    )
                    break
                if MAX_CYCLES and cycles_done >= MAX_CYCLES:
                    log.info(
                        "bounded run complete: %d cycle(s) done - exiting cleanly "
                        "(FUTGG_SALES_MAX_CYCLES)", cycles_done,
                    )
                    break
                cycles_done += 1
                # Set inside a cycle's batch loop (either transport) or
                # its closing backoff/CYCLE_SLEEP sleep if the runtime
                # budget runs out before either finishes - checked once
                # after this cycle's normal finalization (log/heartbeat/
                # throttle-state save) to exit the OUTER loop immediately
                # rather than starting select_due() on a new cycle with
                # no time left to do anything with it.
                cycle_runtime_exhausted = False

                timers = StageTimers()
                stats = {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}
                started = time.perf_counter()
                last_progress_attempted = 0

                # `throttle` itself (batch_size/delay) deliberately survives
                # across cycles - that learned tuning is the whole point of
                # AdaptiveThrottle. But consecutive_fully_throttled_batches
                # is a same-cycle streak counter, and it was never reset
                # between cycles: a cycle that tripped the breaker ends
                # with this already at/above CONSECUTIVE_FULL_THROTTLE_
                # BATCHES, so after the CIRCUIT_BREAKER_BACKOFF_SECONDS
                # sleep, the very next cycle's FIRST batch - even a single
                # one - could push the inherited streak back over the
                # threshold and re-trip in seconds, before the backoff had
                # any real chance to let FUT.GG's rate-limit window clear.
                # Production logs showed exactly this: a productive
                # ~150-175 card cycle immediately followed by a near-empty
                # cards_ok=0 cycle that failed and re-tripped in under 10s,
                # roughly halving effective throughput. Each cycle must
                # start its own streak count at zero.
                throttle.consecutive_fully_throttled_batches = 0

                async with pool.acquire() as conn:
                    due = await select_due(conn)
                    coverage = await report_coverage(conn)
                if not due:
                    # In a bounded run, an empty queue means the work is
                    # finished - exit now rather than idling. Idling is not
                    # free here: the whole point of a bounded run is that
                    # the container stops, and sitting in IDLE_SLEEP with a
                    # browser open costs exactly as much as working.
                    if MAX_RUNTIME_MINUTES or MAX_CYCLES:
                        log.info(
                            "bounded run: nothing due after %d cycle(s) - caught up, "
                            "exiting cleanly", cycles_done,
                        )
                        break
                    if await sleep_or_shutdown(IDLE_SLEEP, shutdown_event):
                        break
                    continue

                if signed_reprobe_due() and signed_page_handle is not None:
                    sample, due = due[:SIGNED_REPROBE_SAMPLE], due[SIGNED_REPROBE_SAMPLE:]
                    await _reprobe_signed_fetch(signed_page_handle, pool, sample)
                    if not due:
                        await sleep_or_shutdown(CYCLE_SLEEP, shutdown_event)
                        continue

                transport = current_transport() if TRANSPORT_MODE == "auto" else TRANSPORT_MODE
                breaker_tripped = False

                if transport == "signed_fetch":
                    offset = 0
                    while offset < len(due) and not shutdown_event.is_set():
                        if runtime_budget_exhausted(deadline):
                            cycle_runtime_exhausted = True
                            run_ended_by_runtime_budget = True
                            log.warning(
                                "runtime budget exhausted mid-cycle on signed_fetch: "
                                "processed %d/%d selected cards this cycle (ok=%d "
                                "failed=%d) - stopping before the next batch "
                                "(FUTGG_SALES_MAX_RUNTIME_MINUTES=%d)",
                                offset, len(due), stats["ok"], stats["failed"],
                                MAX_RUNTIME_MINUTES,
                            )
                            break
                        # Size is read per iteration, so a mid-cycle
                        # backoff takes effect immediately rather than at
                        # the next cycle.
                        size = throttle.batch_size
                        await process_batch(
                            signed_page_handle, pool, due[offset: offset + size], stats, timers, throttle
                        )
                        offset += size

                        attempted = stats["ok"] + stats["failed"]
                        if attempted - last_progress_attempted >= PROGRESS_LOG_EVERY:
                            last_progress_attempted = attempted
                            log.info(
                                "progress[%s]: attempted=%d/%d ok=%d failed=%d sales=%d "
                                "elapsed=%.1fs throttle[%s]",
                                transport, attempted, len(due), stats["ok"],
                                stats["failed"], stats["sales_seen"],
                                time.perf_counter() - started, throttle.describe(),
                            )

                        if circuit_breaker_tripped(stats, throttle):
                            breaker_tripped = True
                            # circuit_breaker_tripped() has two independent
                            # trip conditions (see its own docstring): this
                            # unconditionally logged the cumulative-ratio
                            # wording even when the OTHER condition (two
                            # consecutive fully-throttled batches) was what
                            # actually fired - which, read against a real
                            # 10-hour trace, it almost always was: cycles
                            # logged as "15/165 failed (>=80% threshold)"
                            # are 9.1% failed, nowhere near 80%. The message
                            # now names whichever condition actually tripped
                            # it, rather than always citing the ratio text.
                            # (The exact backoff this trip leads to is
                            # logged separately below, once this cycle's
                            # productivity - which the backoff depends on -
                            # is actually known; predicting it here was
                            # itself a second inaccuracy this fixes.)
                            if throttle.consecutive_fully_throttled_batches >= CONSECUTIVE_FULL_THROTTLE_BATCHES:
                                log.warning(
                                    "circuit breaker TRIPPED on signed_fetch: %d consecutive "
                                    "fully-throttled batches after selecting %d/%d cards this "
                                    "cycle - stopping further selection/marking",
                                    throttle.consecutive_fully_throttled_batches, offset, len(due),
                                )
                            else:
                                log.warning(
                                    "circuit breaker TRIPPED on signed_fetch: %d/%d failed "
                                    "(>=%.0f%% threshold) after selecting %d/%d cards this "
                                    "cycle - stopping further selection/marking",
                                    stats["failed"], stats["ok"] + stats["failed"],
                                    CIRCUIT_BREAKER_THRESHOLD * 100, offset, len(due),
                                )
                            if TRANSPORT_MODE == "auto":
                                switch, why = should_fall_back_to_page_capture(stats, throttle)
                                if switch:
                                    switch_to_page_capture(why)
                                else:
                                    log.warning("staying on signed_fetch: %s", why)
                            break

                        wait_seconds = throttle.next_wait_seconds()
                        if wait_seconds:
                            shutdown, exhausted = await sleep_within_budget(
                                wait_seconds, shutdown_event, deadline
                            )
                            if exhausted:
                                cycle_runtime_exhausted = True
                                run_ended_by_runtime_budget = True
                                log.warning(
                                    "runtime budget exhausted during throttle delay on "
                                    "signed_fetch: processed %d/%d selected cards this "
                                    "cycle (ok=%d failed=%d) - stopping "
                                    "(FUTGG_SALES_MAX_RUNTIME_MINUTES=%d)",
                                    offset, len(due), stats["ok"], stats["failed"],
                                    MAX_RUNTIME_MINUTES,
                                )
                                break
                            if shutdown:
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
                        if runtime_budget_exhausted(deadline):
                            cycle_runtime_exhausted = True
                            run_ended_by_runtime_budget = True
                            log.warning(
                                "runtime budget exhausted mid-cycle on page_capture: "
                                "processed %d/%d selected cards this cycle (ok=%d "
                                "failed=%d) - stopping before the next batch "
                                "(FUTGG_SALES_MAX_RUNTIME_MINUTES=%d)",
                                offset, len(due), stats["ok"], stats["failed"],
                                MAX_RUNTIME_MINUTES,
                            )
                            break
                        chunk = due[offset: offset + chunk_size]
                        await process_batch_page_capture(
                            capture_pages, pool, chunk, stats, timers,
                            shutdown_event=shutdown_event,
                        )
                        offset += chunk_size

                        attempted = stats["ok"] + stats["failed"]
                        if attempted - last_progress_attempted >= PROGRESS_LOG_EVERY:
                            last_progress_attempted = attempted
                            log.info(
                                "progress[%s]: attempted=%d/%d ok=%d failed=%d sales=%d "
                                "elapsed=%.1fs",
                                transport, attempted, len(due), stats["ok"],
                                stats["failed"], stats["sales_seen"],
                                time.perf_counter() - started,
                            )

                        if circuit_breaker_tripped(stats):
                            breaker_tripped = True
                            # Same fix as the signed_fetch branch above: the
                            # actual backoff now depends on this cycle's
                            # productivity, which isn't known yet here -
                            # predicting it against consecutive_cycle_trips + 1
                            # was already wrong before that change and more
                            # so now. The wall-clock resume time is logged
                            # separately below once it's actually computed.
                            log.warning(
                                "circuit breaker TRIPPED on page_capture: %d/%d failed "
                                "(>=%.0f%% threshold) after selecting %d/%d cards this "
                                "cycle - stopping further selection/marking",
                                stats["failed"], stats["ok"] + stats["failed"],
                                CIRCUIT_BREAKER_THRESHOLD * 100, offset, len(due),
                            )
                            break

                elapsed = max(time.perf_counter() - started, 0.001)
                log.info(
                    "cycle[%s]: cards_ok=%d excluded_no_price_section=%d failed=%d "
                    "sales=%d in %.1fs (%.0f cards/min) "
                    "throttle[%s] errors=%s%s",
                    transport, stats["ok"], stats.get("excluded_no_price_section", 0),
                    stats["failed"], stats["sales_seen"], elapsed,
                    (stats["ok"] / elapsed) * 60, throttle.describe(),
                    stats.get("errors", {}),
                    " CIRCUIT_BREAKER_TRIPPED" if breaker_tripped else "",
                )
                for line in timers.format_lines():
                    log.info(line)

                run_totals["ok"] += stats["ok"]
                run_totals["failed"] += stats["failed"]
                run_totals["sales_seen"] += stats["sales_seen"]
                run_totals["throttle_errors"] += sum(
                    count for error, count in (stats.get("errors") or {}).items()
                    if is_throttle_signal(error)
                )
                if breaker_tripped:
                    run_totals["breaker_trips"] += 1

                await write_heartbeat(
                    pool,
                    transport=transport,
                    stats=stats,
                    elapsed=elapsed,
                    breaker_tripped=breaker_tripped,
                    coverage=coverage,
                )

                # Saved every cycle, not just at shutdown: a bounded run
                # can be stopped by MAX_RUNTIME_MINUTES/MAX_CYCLES or an
                # external SIGTERM at any point, and losing this write
                # would mean the next run re-learns FUT.GG's rate limit
                # from scratch again - the exact problem this exists to
                # avoid. Best-effort (see save_throttle_state).
                await save_throttle_state(pool, throttle)

                # A cycle that completed without tripping means the backoff
                # did its job, so the throttle-trip streak must not carry
                # over. Without this reset, three unlucky cycles spread
                # across hours of otherwise-healthy running would
                # eventually force the expensive fallback for no reason.
                if not breaker_tripped:
                    note_signed_fetch_recovered()
                    consecutive_cycle_trips = 0
                else:
                    # Production evidence (1 September): cycles that
                    # collected cards_ok=142 and 185 (24-31 cards/min,
                    # comfortably over HEARTBEAT_MIN_CARDS_PER_MIN) each
                    # still eventually tripped near the end, and the first
                    # version of this escalation counted that the same as
                    # the true "instant retrip" failure (cards_ok=0, 0/min)
                    # it was built to catch - driving the backoff to its
                    # 1800s cap even though nothing was actually getting
                    # worse. AdaptiveThrottle easing its batch size back up
                    # and eventually overshooting the limit again is the
                    # intended, recurring shape of a HEALTHY cycle, so a
                    # productive-but-tripped cycle must not itself count as
                    # evidence the wait was too short (still true, see
                    # circuit_breaker_tripped's own docstring).
                    #
                    # CORRECTED after a full 10-hour trace (2 September):
                    # the fix above also RESET the streak to 0 on every
                    # such productive trip, on the theory that a healthy
                    # cycle proves the backoff before it was sufficient.
                    # It doesn't - it proves that cycle's OWN wait was
                    # sufficient, which says nothing about whether the very
                    # next one will be. Across 61 real cycles, productive
                    # and unproductive trips alternated almost metronomically
                    # (a cycle collecting 110-190 cards, immediately followed
                    # by one collecting 0-11) for the entire window - 28 of
                    # 61 cycles (46%) collected nothing. Because a productive
                    # cycle always intervened, the streak could never reach
                    # 2 and escalation never engaged even once in 10 hours,
                    # despite the same near-instant wall recurring on a
                    # stable cadence the whole time. The mechanism this
                    # matches is a rolling rate-limit window: a productive
                    # cycle spends down the budget a 300s wait let
                    # accumulate, so the very next cycle - only 300s later -
                    # finds it already drained again. A productive trip is
                    # therefore left alone (neither resets nor increments:
                    # it is not proof of failure, per the comment above),
                    # but only a cycle that runs completely clean now resets
                    # the streak - only that is proof the window has
                    # actually cleared, not merely that one more cycle's
                    # worth of it was available.
                    cards_ok = int(stats.get("ok", 0))
                    cycle_cards_per_min = (cards_ok / elapsed) * 60 if elapsed > 0 else 0.0
                    cycle_was_productive = (
                        cards_ok > 0 and cycle_cards_per_min >= HEARTBEAT_MIN_CARDS_PER_MIN
                    )
                    if not cycle_was_productive:
                        consecutive_cycle_trips += 1

                if shutdown_event.is_set():
                    break
                if cycle_runtime_exhausted:
                    # Already logged the specific mid-cycle reason above;
                    # this cycle's normal finalization (log/heartbeat/
                    # save_throttle_state) already ran, exactly as if the
                    # cycle had finished normally, so there is nothing
                    # left to do but stop - starting select_due() on
                    # another cycle, or sleeping out a backoff, with no
                    # runtime budget left would just be discovered at the
                    # top of the next iteration anyway (see
                    # runtime_budget_exhausted's docstring for why that
                    # alone isn't enough to rely on).
                    log.info(
                        "bounded run complete: runtime budget exhausted mid-cycle "
                        "after %d cycle(s) - exiting cleanly "
                        "(FUTGG_SALES_MAX_RUNTIME_MINUTES=%d)",
                        cycles_done, MAX_RUNTIME_MINUTES,
                    )
                    break

                # Periodic browser recycle (see BROWSER_RECYCLE_MINUTES's
                # own docstring) - between cycles only, never mid-batch,
                # so a recycle can never abandon a batch already in
                # flight. Closing the whole browser drops every page it
                # owns (the signed_fetch anchor page and/or the
                # page_capture pool) in one call; _launch_browser_resources
                # then rebuilds exactly the set this transport needs, the
                # same as the initial startup.
                if (
                    BROWSER_RECYCLE_MINUTES
                    and (time.monotonic() - browser_started_at) >= BROWSER_RECYCLE_MINUTES * 60
                ):
                    log.info(
                        "recycling browser after %.0f minute(s) "
                        "(FUTGG_SALES_BROWSER_RECYCLE_MINUTES=%d)",
                        (time.monotonic() - browser_started_at) / 60, BROWSER_RECYCLE_MINUTES,
                    )
                    try:
                        await browser.close()
                    except Exception:
                        log.warning("error closing browser before recycle - continuing anyway", exc_info=True)
                    browser, context, signed_page_handle, capture_pages = await _launch_browser_resources(
                        playwright, need_signed=need_signed, need_page_capture=need_page_capture, anchor=anchor,
                    )
                    browser_started_at = time.monotonic()

                # A signed_fetch block has already switched auto mode to
                # page_capture. Do not impose signed_fetch's one-hour breaker
                # penalty on the *different* transport before it gets a try.
                # Keep any adaptive cooldown, and let page_capture's own
                # breaker enforce a longer pause if that also gets blocked.
                switched_to_capture = (
                    breaker_tripped
                    and transport == "signed_fetch"
                    and TRANSPORT_MODE == "auto"
                    and current_transport() == "page_capture"
                )
                backoff_seconds = (
                    CYCLE_SLEEP if switched_to_capture
                    else next_backoff_seconds(consecutive_cycle_trips) if breaker_tripped
                    else CYCLE_SLEEP
                )
                if breaker_tripped:
                    backoff_seconds = max(backoff_seconds, throttle.cooldown_remaining())
                if switched_to_capture:
                    log.info(
                        "signed_fetch blocked; trying page_capture after %.0fs "
                        "instead of applying the signed_fetch breaker backoff",
                        backoff_seconds,
                    )
                if breaker_tripped:
                    run_totals["breaker_sleep_seconds"] += backoff_seconds
                    # An escalated backoff can now run up to
                    # CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS (60 min default),
                    # long enough that a silent log stream reads as a
                    # stall rather than the worker doing exactly what it
                    # should. The "backing off Ns" figure in the TRIPPED
                    # warning above makes the reader do the arithmetic;
                    # this states the wall-clock answer directly.
                    resume_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
                    log.info(
                        "backing off %.0fs after %d unresolved trip(s) "
                        "(a productive cycle no longer clears this count - "
                        "only a fully clean one does) - "
                        "next cycle expected around %s UTC",
                        backoff_seconds, consecutive_cycle_trips,
                        resume_at.strftime("%H:%M:%S"),
                    )
                _shutdown, _exhausted = await sleep_within_budget(
                    backoff_seconds, shutdown_event, deadline
                )
                if _exhausted:
                    run_ended_by_runtime_budget = True
                    log.info(
                        "bounded run complete: not enough runtime remaining for "
                        "another useful cycle after a %.0fs backoff - exiting "
                        "cleanly instead of sleeping past the deadline "
                        "(FUTGG_SALES_MAX_RUNTIME_MINUTES=%d)",
                        backoff_seconds, MAX_RUNTIME_MINUTES,
                    )
                    break

            run_wall_seconds = time.monotonic() - run_started_at
            run_attempted = run_totals["ok"] + run_totals["failed"]
            run_throttle_rate = (
                run_totals["throttle_errors"] / run_attempted if run_attempted else 0.0
            )
            log.info(
                "run summary: duration=%.0fs cycles=%d attempted=%d ok=%d failed=%d "
                "sales=%d throttle_errors=%d (%.1f%% of attempted) breaker_trips=%d "
                "breaker_sleep_seconds=%.0f throttle[start=%s end=%s] "
                "runtime_budget_exhausted=%s",
                run_wall_seconds, cycles_done, run_attempted, run_totals["ok"],
                run_totals["failed"], run_totals["sales_seen"],
                run_totals["throttle_errors"], 100.0 * run_throttle_rate,
                run_totals["breaker_trips"], run_totals["breaker_sleep_seconds"],
                run_start_throttle_desc, throttle.describe(), run_ended_by_runtime_budget,
            )
        finally:
            log.info("shutting down: closing browser and database pool")
            await browser.close()
            # Best-effort: seen in production, the advisory-lock connection
            # can already be dead by the time this runs - a container
            # teardown (Railway "Stopping Container") can cut the DB
            # connection within milliseconds of the SIGTERM that triggers
            # this shutdown path, before this unlock ever gets to run.
            # asyncpg detects that and detaches lock_conn automatically, so
            # calling it here raises InterfaceError. That is not a bug to
            # propagate: Postgres already drops a session-scoped advisory
            # lock the instant its connection dies, so the lock is gone
            # either way - an unhandled exception here only turns a routine
            # redeploy into a scary "failed" traceback in the logs, and (if
            # thrown before pool.close()) could leave the pool's other
            # connections closed uncleanly instead of via close()'s own
            # graceful path.
            try:
                await lock_conn.execute(
                    "SELECT pg_advisory_unlock($1)", SALES_SYNC_ADVISORY_LOCK_KEY
                )
            except Exception:
                log.warning(
                    "advisory unlock skipped during shutdown - connection "
                    "already gone (lock is released with it regardless)",
                    exc_info=True,
                )
            await pool.release(lock_conn)
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

    async with Stealth().use_async(async_playwright()) as patched_playwright:
        playwright = patched_playwright
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
        try:
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

    async with Stealth().use_async(async_playwright()) as patched_playwright:
        playwright = patched_playwright
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
        try:
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
        # Distinguishes "the player page navigation itself was
        # Cloudflare's interstitial (the real app never ran)" from "the
        # page loaded fine but the API subrequest was challenged" from
        # "neither was challenged but no price request ever fired" -
        # this is the exact question the 2026-08-04 headed-Chromium
        # result left open.
        log.error(
            "DIAGNOSTIC: card=%d navigation itself: navStatus=%s navCfMitigated=%s "
            "(this is the /players/... page load, NOT the API call)",
            card_id, result.get("navStatus"), result.get("navCfMitigated"),
        )
        log.error(
            "DIAGNOSTIC: card=%d FAILED [page_capture]: error=%s dataStatus=%s "
            "cfMitigated=%s elapsed=%.2fs (cloudflare_challenged=%s) "
            "(nav_challenged=%s)",
            card_id, error, result.get("dataStatus"), result.get("cfMitigated"), elapsed,
            result.get("cfMitigated") == "challenge" or error == "page_capture_challenge",
            result.get("navCfMitigated") == "challenge",
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
                SELECT source_card_id, rating, name, next_sales_due_at, source_url,
                       sales_first_read_at, sales_consecutive_failures
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

        # never_read reads sales_first_read_at, not the due-at: a failing
        # never-read card now carries a retry time like any other, so the
        # old due-at test would report never_read=False for exactly the
        # cards a diagnostic is most often run against.
        log.info(
            "DIAGNOSTIC: card=%d name=%r rating=%s next_sales_due_at=%s (never_read=%s "
            "consecutive_failures=%s) source_url=%s",
            card_id, row["name"], row["rating"], row["next_sales_due_at"],
            _row_value(row, "sales_first_read_at") is None,
            _row_value(row, "sales_consecutive_failures", 0), row["source_url"],
        )

        anchor = anchor or "https://www.fut.gg/players/"

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
        # A fatal collector error must be visible to the process supervisor.
        # Exiting successfully left Railway running a dead service with the
        # previous heartbeat frozen for hours.
        sys.exit(1)
    # A deliberate shutdown remains successful. Unexpected exceptions above
    # are failures so either the entrypoint watchdog or the container runtime
    # can restart the collector.
    if result is None:
        sys.exit(0)
    sys.exit(0 if result else 1)

