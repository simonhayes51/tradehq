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
  FUTGG_CHART_SAMPLE_TARGET         default 40
  FUTGG_CHART_CAPTURE_DELAY         default 3.0 (seconds between cards)
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
from db_connect import connect_with_retry
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
SAMPLE_TARGET = int(os.getenv("FUTGG_CHART_SAMPLE_TARGET", "40"))
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


async def dismiss_cookie_banner(page) -> None:
    # Duplicated locally rather than shared, same as
    # futgg_player_sync.py and test_futgg_catalogue_10.py each already do
    # for this exact helper -- small enough not to be worth a shared
    # module for a one-off script.
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
        except Exception:  # noqa: BLE001
            pass


async def select_sample(conn: asyncpg.Connection, target: int) -> list[dict]:
    """Builds the ~N-card representative sample directly from the
    already-populated futgg_players table (+ latest known price from
    futgg_bin_history), instead of re-crawling fut.gg's catalogue -- this
    container already has DB access to production's own fresh scrape."""
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

    buckets: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        card = dict(r)
        tier = classify_price_tier({"rating": card["rating"], "rarity": card["rarity"]})
        if tier not in ("gold_common", "gold_rare"):
            continue
        pos_b = position_bucket(card["primary_position"])
        pr_b = price_bucket(card["lowest_bin"])
        card["category_tag"] = f"{pos_b}/{pr_b}/{tier}"
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

    from collections import Counter
    counts = Counter(c["category_tag"] for c in selected)
    log.info("sample bucket coverage:")
    for tag, n in sorted(counts.items()):
        log.info("  %s: %d", tag, n)

    return selected


def find_year_button(page):
    return page.locator("button", has_text=re.compile(r"^\s*Year\s*$", re.I))


async def is_button_active(button) -> bool:
    try:
        cls = (await button.get_attribute("class")) or ""
    except Exception:  # noqa: BLE001
        return False
    cls_l = cls.lower()
    return any(m in cls_l for m in ("bg-orange", "active", "selected", "text-black"))


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

            year_locator = find_year_button(page)
            try:
                if await year_locator.count() > 0:
                    btn = year_locator.first
                    if not await is_button_active(btn):
                        await btn.click(timeout=5_000)
            except Exception:  # noqa: BLE001
                pass

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


async def main() -> int:
    conn = await connect_with_retry(DATABASE_URL)
    try:
        await ensure_schema(conn)
        sample = await select_sample(conn, SAMPLE_TARGET)
        if not sample:
            log.error("No qualifying gold cards found in futgg_players -- has PlayerSync run yet?")
            return 1

        done = set() if FORCE else await already_captured(conn)
        todo = [c for c in sample if c["card_id"] not in done]
        log.info(
            "%d cards in sample, %d already captured successfully, %d to (re)attempt this run%s",
            len(sample), len(done & {c["card_id"] for c in sample}), len(todo),
            " (FORCE=true, ignoring existing manifest)" if FORCE else "",
        )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=HEADLESS)
            page = await browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ))

            full_pages_saved = 0
            success_count = 0
            failed_count = 0
            recent_circle_counts: list[int] = []
            IDENTICAL_STREAK_THRESHOLD = 3  # see warning below

            for i, card in enumerate(todo, 1):
                log.info("[%d/%d] card_id=%s %s (%s)", i, len(todo), card["card_id"],
                         card.get("name"), card["url"])

                status, num_circles, html, error_reason = await capture_one(
                    page, card["url"], RETRIES, TIMEOUT_MS
                )

                full_page_html = None
                if status == "success":
                    success_count += 1
                    if full_pages_saved < FULL_PAGE_COUNT:
                        try:
                            full_page_html = await page.content()
                            full_pages_saved += 1
                        except Exception as exc:  # noqa: BLE001
                            log.warning("full-page capture failed (non-fatal): %r", exc)
                    log.info("  OK: %d circles", num_circles)

                    recent_circle_counts.append(num_circles)
                    recent_circle_counts = recent_circle_counts[-IDENTICAL_STREAK_THRESHOLD:]
                    if (
                        len(recent_circle_counts) == IDENTICAL_STREAK_THRESHOLD
                        and len(set(recent_circle_counts)) == 1
                    ):
                        log.warning(
                            "*** SUSPECTED EXTRACTION BUG: last %d successful cards ALL "
                            "reported exactly %d circles. Different real cards should not "
                            "converge on an identical daily-history length -- this is the "
                            "same signature as the wrong-chart bug found earlier. Consider "
                            "stopping this run and inspecting a saved full_page_html sample "
                            "before trusting any more 'success' rows. ***",
                            IDENTICAL_STREAK_THRESHOLD, num_circles,
                        )
                else:
                    failed_count += 1
                    log.warning("  FAILED: %s", error_reason)

                await upsert_result(
                    conn, card, status, num_circles, RETRIES, error_reason, html, full_page_html
                )

                if i < len(todo):
                    jitter = random.uniform(-1.0, 1.0)
                    await asyncio.sleep(max(0.0, CAPTURE_DELAY + jitter))

            await browser.close()

        log.info(
            "DONE: %d success, %d failed this run. Query fc26_chart_capture_fixtures for the full picture.",
            success_count, failed_count,
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
