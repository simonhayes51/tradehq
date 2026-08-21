"""
Phase 4 chart-fixture capture -- RESEARCH ONE-OFF, not a scheduled worker.

Deploys via the *existing* PlayerSync image (Dockerfile.player already
bakes in Playwright + Chromium, PLAYWRIGHT_HEADLESS=true, DATABASE_URL) --
no new Dockerfile, no new Coolify resource, no schedule change. See
research/fc26_launch_market/tools/README.md for the exact "temporarily
override this resource's start command for one manual run" steps (same
pattern COOLIFY.md already documents for futgg_sales_sync.py's one-card
diagnostic mode).

What this does, in one run:
  1. Selects a representative ~40-card sample of FC26 base gold cards
     directly from the already-populated `futgg_players` table (joined to
     the latest `futgg_bin_history` row per card for a price tier) --
     no fut.gg catalogue crawl needed, since PlayerSync already keeps
     that table fresh. Uses classify_price_tier() from futgg_common.py
     (same function production code uses) to keep only common/rare base
     golds, skipping specials.
  2. For each selected card: navigates to its real fut.gg source_url,
     selects the "Year" chart range if not already active, waits for the
     Recharts <circle> points to render, and captures the chart
     container's outerHTML (+ full page HTML for the first few cards).
  3. Writes one row per card into fc26_chart_capture_fixtures -- a
     RESEARCH-ONLY table this script creates itself (CREATE TABLE IF NOT
     EXISTS, same convention futgg_player_sync.py's ensure_schema()
     already uses), separate from every production futgg_* table. Safe
     to drop once Phase 4 fixtures have been exported.

Resumable: cards already marked status='success' in that table are
skipped on a re-run unless FUTGG_CHART_CAPTURE_FORCE=true.

Env vars (all optional, production-safe defaults):
  DATABASE_URL                     required (same as every other worker)
  PLAYWRIGHT_HEADLESS               default true
  FUTGG_CHART_CAPTURE_ALL           default false -- Phase 7 bulk mode:
                                     capture EVERY qualifying Gold Common/
                                     Gold Rare card instead of a balanced
                                     ~40-card sample. Logs the true
                                     qualifying-card count before starting
                                     (Phase 6's "how many cards exist"
                                     question) so you see the real scale
                                     before committing to a run.
  FUTGG_CHART_SAMPLE_TARGET         default 40 (ignored when _ALL=true)
  FUTGG_CHART_CAPTURE_CONCURRENCY   default 3 -- number of Playwright pages
                                     run in parallel, each on its own
                                     card queue with its own delay/jitter.
                                     Keep this conservative -- raising it
                                     increases load on fut.gg as well as
                                     this container. Start low, watch a
                                     run, only raise if it stays stable.
  FUTGG_CHART_CAPTURE_DELAY         default 3.0 (seconds between cards,
                                     PER WORKER -- total request rate is
                                     roughly concurrency / delay)
  FUTGG_CHART_CAPTURE_RETRIES       default 3
  FUTGG_CHART_CAPTURE_TIMEOUT_MS    default 20000
  FUTGG_CHART_FULL_PAGE_COUNT       default 5
  FUTGG_CHART_CAPTURE_FORCE         default false

Does NOT touch futgg_players, futgg_bin_history, futgg_sales_history, or
any other production table -- read-only against them, and it never
extracts/interprets a price. That's still Phase 2's job, unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
from datetime import datetime, timezone

import asyncpg
from db_connect import create_pool_with_retry
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from futgg_common import classify_price_tier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("research_fc26_chart_capture")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").strip().lower() not in {"0", "false", "no"}
CAPTURE_ALL = os.getenv("FUTGG_CHART_CAPTURE_ALL", "false").strip().lower() in {"1", "true", "yes"}
SAMPLE_TARGET = int(os.getenv("FUTGG_CHART_SAMPLE_TARGET", "40"))
CONCURRENCY = max(1, int(os.getenv("FUTGG_CHART_CAPTURE_CONCURRENCY", "3")))
CAPTURE_DELAY = float(os.getenv("FUTGG_CHART_CAPTURE_DELAY", "3.0"))
RETRIES = int(os.getenv("FUTGG_CHART_CAPTURE_RETRIES", "3"))
TIMEOUT_MS = int(os.getenv("FUTGG_CHART_CAPTURE_TIMEOUT_MS", "20000"))
FULL_PAGE_COUNT = int(os.getenv("FUTGG_CHART_FULL_PAGE_COUNT", "5"))
FORCE = os.getenv("FUTGG_CHART_CAPTURE_FORCE", "false").strip().lower() in {"1", "true", "yes"}

# Same buckets as research/fc26_launch_market/tools/build_sample_list.py's
# POSITION_BUCKETS -- kept in sync manually since that script runs in a
# completely different execution context (a local machine with no DB
# access) and duplicating four short dict literals is simpler than
# sharing a module across the two.
POSITION_BUCKETS = {
    "CB": {"CB"},
    "FULLBACK": {"LB", "RB", "LWB", "RWB"},
    "MIDFIELD": {"CM", "CDM", "CAM"},
    "WINGER": {"LW", "RW", "LM", "RM"},
    "STRIKER": {"ST", "CF"},
}


def position_bucket(primary_position: str | None) -> str:
    if not primary_position:
        return "other"
    pos = primary_position.strip().upper()
    for bucket, members in POSITION_BUCKETS.items():
        if pos in members:
            return bucket
    return "other"


def price_bucket(lowest_bin: int | None) -> str:
    if lowest_bin is None:
        return "unknown"
    if lowest_bin <= 1000:
        return "discard"
    if lowest_bin <= 5000:
        return "cheap"
    if lowest_bin <= 50000:
        return "mid"
    return "expensive"


async def ensure_schema(conn: asyncpg.Connection) -> None:
    # RESEARCH-ONLY table. Not part of the app schema, not created via
    # backend/migrations (deliberately -- this is a one-off Phase 4
    # capture, not a permanent feature). Safe to DROP TABLE once Phase 4
    # fixtures have been exported to research/fc26_launch_market/.
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fc26_chart_capture_fixtures (
            card_id             BIGINT PRIMARY KEY,
            url                 TEXT NOT NULL,
            name                TEXT,
            rating              SMALLINT,
            primary_position    TEXT,
            rarity              TEXT,
            league              TEXT,
            category_tag        TEXT,
            status              TEXT NOT NULL,
            num_circles         INTEGER NOT NULL DEFAULT 0,
            attempts            INTEGER NOT NULL DEFAULT 0,
            error_reason        TEXT,
            chart_html          TEXT,
            full_page_html      TEXT,
            captured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


async def dismiss_cookie_banner(page) -> bool:
    """Ordinary consent-banner dismissal, same as a human clicking
    "Accept" -- NOT anti-bot evasion. Production evidence: a verified
    Year-button click failed with Playwright's own diagnostic naming the
    real cause explicitly -- '<div id="cmpwrapper" class="cmpwrapper">
    intercepts pointer events' -- a consent-management-platform overlay
    the original OneTrust-only selector list below didn't recognise.
    Extended with several common CMP vendors' own accept buttons, plus a
    text-based fallback, plus (last resort, only if the wrapper is STILL
    obstructing after trying to click something in it) forcibly hiding
    #cmpwrapper itself via a style change -- equivalent in effect to a
    user who dismissed the banner, not a bypass of any protection.
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
        except Exception:  # noqa: BLE001
            pass

    # Last resort: the wrapper still exists and is still intercepting
    # pointer events, but none of the known "accept" selectors matched
    # (unrecognised/custom CMP markup). Hiding it has the same practical
    # effect as a user dismissing it -- it doesn't touch fut.gg's own
    # anti-bot posture at all, only a third-party consent widget.
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
    except Exception:  # noqa: BLE001
        return False


async def _fetch_qualifying_cards(conn: asyncpg.Connection) -> list[dict]:
    """Shared by select_sample() and select_all_qualifying(): reads
    candidate cards directly from the already-populated futgg_players
    table (+ latest known price from futgg_bin_history) instead of
    re-crawling fut.gg's catalogue, then keeps only Gold Common/Gold Rare
    via classify_price_tier() (same function production code uses),
    tagging each with position/price-tier/rarity-tier for reporting."""
    rows = await conn.fetch(
        """
        SELECT
            p.source_card_id AS card_id,
            p.source_url     AS url,
            p.name,
            p.rating,
            p.primary_position,
            p.rarity,
            p.league,
            b.current_bin    AS lowest_bin
        FROM futgg_players p
        LEFT JOIN LATERAL (
            SELECT lowest_bin AS current_bin
            FROM futgg_bin_history h
            WHERE h.source_card_id = p.source_card_id
            ORDER BY h.captured_at DESC
            LIMIT 1
        ) b ON TRUE
        WHERE p.rating IS NOT NULL AND p.rating >= 75
        """
    )
    log.info("candidate pool from futgg_players: %d rows (rating >= 75)", len(rows))

    qualifying: list[dict] = []
    for r in rows:
        card = dict(r)
        tier = classify_price_tier({"rating": card["rating"], "rarity": card["rarity"]})
        if tier not in ("gold_common", "gold_rare"):
            continue
        pos_b = position_bucket(card["primary_position"])
        pr_b = price_bucket(card["lowest_bin"])
        card["category_tag"] = f"{pos_b}/{pr_b}/{tier}"
        qualifying.append(card)

    log.info(
        "qualifying Gold Common/Gold Rare cards (Phase 6 answer -- true "
        "scale before any scraping): %d",
        len(qualifying),
    )
    return qualifying


def _log_category_coverage(cards: list[dict], label: str) -> None:
    from collections import Counter
    counts = Counter(c["category_tag"] for c in cards)
    log.info("%s:", label)
    for tag, n in sorted(counts.items()):
        log.info("  %s: %d", tag, n)


async def select_sample(conn: asyncpg.Connection, target: int) -> list[dict]:
    """Builds a ~N-card sample balanced across position x price-tier
    buckets -- useful for a quick smoke test, not for real coverage. See
    select_all_qualifying() for Phase 7 bulk collection."""
    qualifying = await _fetch_qualifying_cards(conn)

    buckets: dict[tuple[str, str], list[dict]] = {}
    for card in qualifying:
        pos_b, pr_b, _tier = card["category_tag"].split("/")
        buckets.setdefault((pos_b, pr_b), []).append(card)

    for v in buckets.values():
        random.shuffle(v)

    selected: list[dict] = []
    keys = list(buckets.keys())
    random.shuffle(keys)
    idx = 0
    while len(selected) < target and any(buckets[k] for k in keys):
        key = keys[idx % len(keys)]
        if buckets[key]:
            selected.append(buckets[key].pop())
        idx += 1
        if idx > 10_000:
            break

    _log_category_coverage(selected, "sample bucket coverage")
    return selected


async def select_all_qualifying(conn: asyncpg.Connection) -> list[dict]:
    """Phase 7 bulk mode: every qualifying Gold Common/Gold Rare card, not
    just a balanced sample. Shuffled so concurrent workers and any
    mid-run stop/resume don't bias which cards land early vs late."""
    qualifying = await _fetch_qualifying_cards(conn)
    random.shuffle(qualifying)
    _log_category_coverage(qualifying, "full qualifying-pool category coverage")
    return qualifying


def find_year_button(page):
    return page.locator("button", has_text=re.compile(r"^\s*Year\s*$", re.I))


async def is_button_active(button) -> bool:
    try:
        cls = (await button.get_attribute("class")) or ""
    except Exception:  # noqa: BLE001
        return False
    cls_l = cls.lower()
    return any(m in cls_l for m in ("bg-orange", "active", "selected", "text-black"))


async def click_year_and_verify(
    page, max_attempts: int = 4, verify_poll_interval: float = 0.4, verify_polls: int = 5,
) -> tuple[bool, str]:
    """Clicks the 'Year' toggle and CONFIRMS it actually took effect
    (button gains its active class) before moving on, instead of firing
    the click and trusting it. Production evidence: the page ships
    server-rendered with "Week" active by default, and a click fired
    before React finishes hydrating the button's onClick handler is a
    real click on a DOM node that quietly does nothing -- exactly what
    was happening (every card capturing the ~7-day Week view instead of
    Year, all converging on ~8 circles). Retries the click itself, not
    just the verification wait, since a pre-hydration click is simply
    lost, not delayed.

    Returns (switched, note) -- note explains what happened either way,
    for logging."""
    year_locator = find_year_button(page)
    try:
        if await year_locator.count() == 0:
            return False, "no 'Year' button found on page"
        btn = year_locator.first

        if await is_button_active(btn):
            return True, "already active"

        for attempt in range(1, max_attempts + 1):
            try:
                await btn.click(timeout=5_000)
            except Exception as exc:  # noqa: BLE001
                # A click that raises (covered element, detached node from
                # a re-render, etc.) is exactly the kind of failure the
                # old bare `except: pass` was hiding. Log it, keep trying
                # -- but first try dismissing a consent overlay, since
                # "element intercepts pointer events" from #cmpwrapper is
                # the confirmed real-world cause of this failure mode.
                await dismiss_cookie_banner(page)
                if attempt == max_attempts:
                    return False, f"click raised on final attempt: {exc!r}"
                await asyncio.sleep(0.3)
                continue

            for _ in range(verify_polls):
                await asyncio.sleep(verify_poll_interval)
                if await is_button_active(btn):
                    return True, f"switched on click attempt {attempt}"

        return False, f"clicked {max_attempts}x but button never showed active state"
    except Exception as exc:  # noqa: BLE001
        return False, f"unexpected error locating/clicking Year button: {exc!r}"


async def find_chart_container_html(page) -> tuple[str | None, int]:
    """Locates the PRICE HISTORY chart specifically, not just "the first
    Recharts widget on the page" -- fut.gg pages can render more than one
    .recharts-responsive-container (e.g. a small stats/comparison widget),
    and picking the DOM-first one silently grabbed the wrong, much
    smaller chart in production (every sampled card returning exactly 8
    circles, regardless of position/league -- a signature of hitting the
    same small secondary widget, not real per-card daily history).

    Fix: anchor on the "Year" toggle BUTTON, which is structurally
    unique to the real price chart's own widget, and climb its ancestors
    (not the chart's) to find the smallest one that also contains a
    .recharts-responsive-container descendant. If more than one button's
    text is exactly "Year" (defensive, in case a second widget ever
    reuses that label), evaluate every candidate and keep whichever has
    the MOST rendered circles -- the real daily-history chart will always
    dominate any small stats/comparison widget by point count."""
    result = await page.evaluate(
        """
        () => {
            const buttons = Array.from(document.querySelectorAll('button'))
                .filter(b => b.textContent.trim().toLowerCase() === 'year');
            if (buttons.length === 0) return { html: null, circles: 0, candidates: 0 };

            let best = { html: null, circles: -1 };
            for (const btn of buttons) {
                let node = btn;
                let container = null;
                for (let i = 0; i < 10 && node; i++) {
                    if (node.querySelector && node.querySelector('.recharts-responsive-container')) {
                        container = node;
                        break;
                    }
                    node = node.parentElement;
                }
                if (!container) continue;
                const circles = container.querySelectorAll(
                    'circle.recharts-dot, circle[class*="area-dot"]'
                ).length;
                if (circles > best.circles) {
                    best = { html: container.outerHTML, circles };
                }
            }
            return { html: best.html, circles: Math.max(best.circles, 0), candidates: buttons.length };
        }
        """
    )
    return result.get("html"), result.get("circles", 0)


async def wait_for_stable_chart(
    page, max_polls: int = 10, poll_interval: float = 0.5,
) -> tuple[str | None, int]:
    """A second production run, AFTER anchoring on the Year button fixed
    nothing, still returned exactly 8 circles across totally unrelated
    cards (goalkeeper, fullback, midfielder, winger, striker). That rules
    out "wrong chart" as the sole cause -- the more likely explanation is
    a race: wait_for_selector(state='attached') resolves the instant the
    FIRST matching circle appears, not once Recharts has finished
    painting all of them (a mount animation / staged reveal would explain
    a fixed early-frame count showing up identically regardless of the
    card's real point total -- the same family of "captured mid-
    transition" issue Phase 1 already found in the duplicated/stale
    Y-axis tick markup on one manually-captured page).

    Fix: don't trust the first read. Poll find_chart_container_html()
    repeatedly and only return once the circle count is IDENTICAL across
    two consecutive reads (i.e. the chart has stopped growing), or
    max_polls is exhausted (whatever the last read was, so a genuinely
    slow-to-fully-render chart still returns its best-known state rather
    than nothing)."""
    last_html, last_circles = await find_chart_container_html(page)
    for _ in range(max_polls):
        await asyncio.sleep(poll_interval)
        html, circles = await find_chart_container_html(page)
        if circles == last_circles and html:
            return html, circles
        last_html, last_circles = html, circles
    return last_html, last_circles


async def capture_one(page, url: str, retries: int, timeout_ms: int) -> tuple[str, int, str, str | None]:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if not resp or resp.status >= 400:
                last_error = f"HTTP {resp.status if resp else 'none'} on attempt {attempt}"
                await asyncio.sleep(1.5 * attempt)
                continue

            await dismiss_cookie_banner(page)

            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass

            switched, switch_note = await click_year_and_verify(page)
            if not switched:
                # Diagnosed from a real production capture: the page is
                # SSR'd with "Week" active by default (NOT "Year" -- the
                # opposite of what the Phase 1 manually-captured fixture
                # showed, which was captured after a human had already
                # clicked Year). Every card converging on exactly 8
                # circles was the Week view's natural ~7-day point count,
                # not a wrong-widget or rendering-race bug. If the click
                # never takes (most likely: clicked before React finished
                # hydrating, so the onClick handler wasn't wired up yet),
                # this is now surfaced loudly instead of silently
                # swallowed, and counted as a failed attempt so it
                # retries rather than quietly capturing the wrong range.
                last_error = f"could not switch chart to Year range (attempt {attempt}): {switch_note}"
                log.warning("  %s", last_error)
                await asyncio.sleep(1.5 * attempt)
                continue

            try:
                await page.wait_for_selector(
                    "circle.recharts-dot, circle[class*='area-dot']",
                    timeout=timeout_ms, state="attached",
                )
            except PlaywrightTimeoutError:
                last_error = f"chart circles never rendered (attempt {attempt})"
                await asyncio.sleep(1.5 * attempt)
                continue

            html, num_circles = await wait_for_stable_chart(page)
            if not html or num_circles == 0:
                last_error = f"chart container found but 0 circles (attempt {attempt})"
                await asyncio.sleep(1.5 * attempt)
                continue

            if num_circles < 20:
                log.warning(
                    "  SUSPICIOUSLY LOW circle count (%d) -- could be a genuinely "
                    "short history (recently-added card) OR a mis-extraction; "
                    "verify this one manually before trusting it",
                    num_circles,
                )

            return "success", num_circles, html, None

        except PlaywrightTimeoutError as exc:
            last_error = f"navigation/timeout on attempt {attempt}: {exc}"
            await asyncio.sleep(1.5 * attempt)
        except Exception as exc:  # noqa: BLE001
            last_error = f"unexpected error on attempt {attempt}: {exc!r}"
            await asyncio.sleep(1.5 * attempt)

    return "failed", 0, "", (last_error or "unknown failure")


async def already_captured(conn: asyncpg.Connection) -> set[int]:
    rows = await conn.fetch(
        "SELECT card_id FROM fc26_chart_capture_fixtures WHERE status = 'success'"
    )
    return {r["card_id"] for r in rows}


async def upsert_result(conn: asyncpg.Connection, card: dict, status: str, num_circles: int,
                         attempts: int, error_reason: str | None, chart_html: str,
                         full_page_html: str | None) -> None:
    await conn.execute(
        """
        INSERT INTO fc26_chart_capture_fixtures (
            card_id, url, name, rating, primary_position, rarity, league,
            category_tag, status, num_circles, attempts, error_reason,
            chart_html, full_page_html, captured_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (card_id) DO UPDATE SET
            url = EXCLUDED.url,
            name = EXCLUDED.name,
            rating = EXCLUDED.rating,
            primary_position = EXCLUDED.primary_position,
            rarity = EXCLUDED.rarity,
            league = EXCLUDED.league,
            category_tag = EXCLUDED.category_tag,
            status = EXCLUDED.status,
            num_circles = EXCLUDED.num_circles,
            attempts = EXCLUDED.attempts,
            error_reason = EXCLUDED.error_reason,
            chart_html = COALESCE(EXCLUDED.chart_html, fc26_chart_capture_fixtures.chart_html),
            full_page_html = COALESCE(EXCLUDED.full_page_html, fc26_chart_capture_fixtures.full_page_html),
            captured_at = EXCLUDED.captured_at
        """,
        card["card_id"], card["url"], card.get("name"), card.get("rating"),
        card.get("primary_position"), card.get("rarity"), card.get("league"),
        card.get("category_tag"), status, num_circles, attempts, error_reason,
        chart_html or None, full_page_html,
        datetime.now(timezone.utc),
    )


IDENTICAL_STREAK_THRESHOLD = 3  # see warning text below


async def _worker(
    worker_id: int, pool: asyncpg.Pool, browser, queue: "asyncio.Queue[dict]",
    total: int, stats: dict, stats_lock: asyncio.Lock,
    full_page_state: dict, full_page_lock: asyncio.Lock,
) -> None:
    """One of CONCURRENCY parallel workers, each with its own Playwright
    page pulling cards off a shared queue until it's empty. Playwright
    itself is fine with many pages on one browser; the DB pool (not a
    single shared connection) is what makes concurrent upserts safe."""
    page = await browser.new_page(user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ))
    try:
        while True:
            try:
                card = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            async with stats_lock:
                stats["started"] += 1
                progress = stats["started"]
            log.info("[w%d %d/%d] card_id=%s %s (%s)", worker_id, progress, total,
                      card["card_id"], card.get("name"), card["url"])

            status, num_circles, html, error_reason = await capture_one(
                page, card["url"], RETRIES, TIMEOUT_MS
            )

            full_page_html = None
            if status == "success":
                async with full_page_lock:
                    if full_page_state["count"] < FULL_PAGE_COUNT:
                        try:
                            full_page_html = await page.content()
                            full_page_state["count"] += 1
                        except Exception as exc:  # noqa: BLE001
                            log.warning("[w%d] full-page capture failed (non-fatal): %r",
                                        worker_id, exc)
                log.info("[w%d]   OK: %d circles", worker_id, num_circles)
            else:
                log.warning("[w%d]   FAILED: %s", worker_id, error_reason)

            async with pool.acquire() as conn:
                await upsert_result(
                    conn, card, status, num_circles, RETRIES, error_reason, html, full_page_html
                )

            async with stats_lock:
                if status == "success":
                    stats["success"] += 1
                    # Streak is across completion order, not selection
                    # order, since workers finish interleaved under
                    # concurrency -- still a useful smoke alarm, just a
                    # rougher one than the sequential version.
                    stats["recent_circles"].append(num_circles)
                    stats["recent_circles"] = stats["recent_circles"][-IDENTICAL_STREAK_THRESHOLD:]
                    if (
                        len(stats["recent_circles"]) == IDENTICAL_STREAK_THRESHOLD
                        and len(set(stats["recent_circles"])) == 1
                    ):
                        log.warning(
                            "*** SUSPECTED EXTRACTION BUG: last %d successful captures "
                            "(across all workers) ALL reported exactly %d circles. "
                            "Different real cards should not converge on an identical "
                            "daily-history length -- this is the same signature as the "
                            "wrong-chart bug found earlier. Consider stopping this run "
                            "and inspecting a saved full_page_html sample before "
                            "trusting any more 'success' rows. ***",
                            IDENTICAL_STREAK_THRESHOLD, num_circles,
                        )
                else:
                    stats["failed"] += 1

            queue.task_done()
            jitter = random.uniform(-1.0, 1.0)
            await asyncio.sleep(max(0.0, CAPTURE_DELAY + jitter))
    finally:
        await page.close()


async def main() -> int:
    pool = await create_pool_with_retry(DATABASE_URL, min_size=1, max_size=CONCURRENCY + 1)
    try:
        async with pool.acquire() as conn:
            await ensure_schema(conn)
            sample = await (select_all_qualifying(conn) if CAPTURE_ALL else select_sample(conn, SAMPLE_TARGET))
            if not sample:
                log.error("No qualifying gold cards found in futgg_players -- has PlayerSync run yet?")
                return 1
            done = set() if FORCE else await already_captured(conn)

        todo = [c for c in sample if c["card_id"] not in done]
        log.info(
            "%s mode: %d cards in pool, %d already captured successfully, %d to "
            "(re)attempt this run, concurrency=%d%s",
            "BULK (FUTGG_CHART_CAPTURE_ALL=true)" if CAPTURE_ALL else "sample",
            len(sample), len(done & {c["card_id"] for c in sample}), len(todo), CONCURRENCY,
            " (FORCE=true, ignoring existing manifest)" if FORCE else "",
        )
        if not todo:
            log.info("Nothing to do -- every qualifying card already captured successfully.")
            return 0

        queue: asyncio.Queue = asyncio.Queue()
        for c in todo:
            queue.put_nowait(c)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=HEADLESS)
            stats = {"started": 0, "success": 0, "failed": 0, "recent_circles": []}
            stats_lock = asyncio.Lock()
            full_page_state = {"count": 0}
            full_page_lock = asyncio.Lock()

            worker_count = min(CONCURRENCY, len(todo))
            workers = [
                asyncio.create_task(_worker(
                    i + 1, pool, browser, queue, len(todo),
                    stats, stats_lock, full_page_state, full_page_lock,
                ))
                for i in range(worker_count)
            ]
            await asyncio.gather(*workers)
            await browser.close()

        log.info(
            "DONE: %d success, %d failed this run. Query fc26_chart_capture_fixtures for the full picture.",
            stats["success"], stats["failed"],
        )
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
