"""
Phase 4 chart-fixture capture utility.

Runs LOCALLY on a machine that can reach fut.gg normally in a real browser
(this repo's sandboxed sessions cannot -- see docs/phase1_findings.md).
Uses ordinary Playwright browser automation only: no Cloudflare/anti-bot
bypass of any kind. If the site blocks or challenges the browser, this
tool will simply fail those cards and record it -- it will not attempt to
route around that.

For each card in the input list, this script:
  1. Navigates to the player page and waits for normal page load.
  2. Finds the price-chart widget and clicks the "Year" range toggle if it
     is not already selected.
  3. Waits until the Recharts SVG's <circle> data points are rendered.
  4. Saves the chart container's outerHTML to fixtures_dir/<card_id>.html
  5. For the first N successfully-captured cards (default 5), also saves
     the full rendered page HTML to fixtures_dir/full_pages/<card_id>.html
     (to inspect for embedded JSON / hydration data / script tags -- see
     the still-open item in docs/phase1_findings.md).
  6. Records one row per card in manifest.csv: card_id, url, timestamp,
     status, num_circles, attempts, error_reason.

This tool does NOT extract or interpret prices -- that's Phase 2's job,
unchanged. Its only output is raw HTML fixtures + a manifest.

Resumable: on a re-run, any card already marked "success" in the existing
manifest is skipped unless --force is passed. Failed/never-attempted cards
are (re)attempted.

See tools/README.md for exact Windows setup/run commands.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeoutError, sync_playwright

CARD_URL_RE = re.compile(
    r"^/players/(?P<player_id>\d+)-(?P<slug>[^/]+)/"
    r"(?P<game_year>\d+)-(?P<card_id>\d+)/?$"
)
BASE_URL = "https://www.fut.gg"

MANIFEST_FIELDS = [
    "card_id", "url", "timestamp_utc", "status", "num_circles",
    "attempts", "error_reason", "full_page_saved",
]


@dataclass
class InputCard:
    raw: str
    url: str | None
    card_id: str | None
    resolved: bool  # True if `url` is a real canonical URL, False if guessed


def parse_card_id_from_url(url: str) -> str | None:
    path = url.split("//", 1)[-1].split("/", 1)[-1]
    path = "/" + path.split("?")[0].rstrip("/") + "/"
    m = CARD_URL_RE.match(path)
    if m:
        return m.group("card_id")
    return None


def load_input_list(path: Path) -> list[InputCard]:
    """Accepts .txt (one URL or bare card_id per line, '#' comments ok),
    .csv (a 'url' column preferred; falls back to 'card_id'), or .json
    (list of strings, or list of {"url": ...} / {"card_id": ...} objects).
    Bare card_id entries get a BEST-EFFORT, UNVERIFIED URL guess
    (BASE_URL/players/{game_year}-{card_id}/) since FUT.GG's canonical URL
    also requires the player_id-slug segment that a card_id alone can't
    give us -- if that guess doesn't resolve to a real chart page, the
    card is recorded as a failure with a clear reason, never silently
    dropped. Prefer full canonical URLs (e.g. from build_sample_list.py)."""
    cards: list[InputCard] = []

    def add(raw: str):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            return
        if raw.startswith("http"):
            cid = parse_card_id_from_url(raw)
            cards.append(InputCard(raw=raw, url=raw, card_id=cid, resolved=True))
        else:
            # bare card_id (optionally "26:12345" or "12345")
            cid = raw.split(":")[-1].strip()
            guess = f"{BASE_URL}/players/26-{cid}/"
            cards.append(InputCard(raw=raw, url=guess, card_id=cid, resolved=False))

    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                add(item.get("url") or item.get("card_id") or "")
    elif suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                add(row.get("url") or row.get("card_id") or "")
    else:  # .txt or anything else, line-based
        for line in path.read_text(encoding="utf-8").splitlines():
            add(line)

    return cards


def load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["card_id"]: row for row in csv.DictReader(f)}


def write_manifest(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for card_id in sorted(rows, key=lambda k: (len(k), k)):
            w.writerow(rows[card_id])


def find_year_button(page: Page):
    """Locates the chart's range-toggle 'Year' button. Matches by exact
    trimmed text, case-insensitive, among <button> elements -- avoids
    relying on specific CSS class names that may change."""
    candidates = page.locator("button", has_text=re.compile(r"^\s*Year\s*$", re.I))
    return candidates.first if candidates.count() > 0 else None


def is_button_active(page: Page, button) -> bool:
    """Best-effort 'already selected' check: looks for a highlighted-style
    class (bg-orange / active / selected) on the button. If we can't tell,
    returns False so the caller clicks it anyway (idempotent: clicking an
    already-active range toggle should be harmless)."""
    try:
        cls = button.get_attribute("class") or ""
    except Exception:  # noqa: BLE001
        return False
    cls_l = cls.lower()
    return any(marker in cls_l for marker in ("bg-orange", "active", "selected", "text-black"))


def dismiss_cookie_banner(page: Page) -> bool:
    """Ordinary consent-banner dismissal, same as a human clicking
    "Accept" -- NOT anti-bot evasion. Production evidence (from the
    Coolify twin of this script): a verified Year-button click failed
    with Playwright naming the real cause explicitly -- '<div
    id="cmpwrapper" class="cmpwrapper"> intercepts pointer events'.
    Tries several common consent-platform selectors, then (last resort,
    only if the wrapper is still obstructing) hides #cmpwrapper directly
    -- equivalent in effect to a user dismissing the banner, not a bypass
    of fut.gg's own protections. Returns True if something was
    dismissed/hidden."""
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
            if button.is_visible(timeout=500):
                button.click(timeout=2000)
                return True
        except Exception:  # noqa: BLE001
            pass

    try:
        hidden = page.evaluate(
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


def click_year_and_verify(
    page: Page, max_attempts: int = 4, verify_poll_interval: float = 0.4, verify_polls: int = 5,
) -> tuple[bool, str]:
    """Clicks 'Year' and CONFIRMS it took effect (button gains its active
    class) instead of trusting a fire-and-forget click. Production
    evidence (from the Coolify twin of this script): the page ships
    server-rendered with "Week" active by default, and a click fired
    before React finishes hydrating the handler is a real DOM click that
    quietly does nothing -- every card was capturing the ~7-day Week view
    (all converging on ~8 circles), not a wrong-widget or render-race
    bug. Retries the click itself since a pre-hydration click is lost,
    not delayed."""
    btn = find_year_button(page)
    if btn is None:
        return False, "no 'Year' button found on page"

    if is_button_active(page, btn):
        return True, "already active"

    for attempt in range(1, max_attempts + 1):
        try:
            btn.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            # "element intercepts pointer events" from #cmpwrapper is the
            # confirmed real-world cause of this failure mode -- try
            # dismissing it before the next attempt.
            dismiss_cookie_banner(page)
            if attempt == max_attempts:
                return False, f"click raised on final attempt: {exc!r}"
            time.sleep(0.3)
            continue

        for _ in range(verify_polls):
            time.sleep(verify_poll_interval)
            if is_button_active(page, btn):
                return True, f"switched on click attempt {attempt}"

    return False, f"clicked {max_attempts}x but button never showed active state"


def find_chart_container_html(page: Page) -> tuple[str | None, int]:
    """Finds the smallest ancestor of the Recharts chart that also
    contains the range-toggle buttons (Year/Month/Week/...), and returns
    its outerHTML plus a count of rendered data-point circles inside it.

    IMPORTANT: fut.gg pages can render more than one
    .recharts-responsive-container (e.g. a small stats/comparison widget
    elsewhere on the page) -- picking "the first one in the DOM" silently
    grabbed the wrong, much smaller chart in a production run (every
    sampled card returned exactly 8 circles regardless of position/
    league, a signature of hitting the same small secondary widget
    rather than real per-card daily history). Fixed by anchoring on the
    'Year' toggle BUTTON instead (structurally unique to the real price
    chart's own widget) and climbing ITS ancestors to find one that
    contains a .recharts-responsive-container descendant; if more than
    one button's text is exactly 'Year', every candidate is evaluated and
    whichever has the MOST circles wins, since the real daily-history
    chart will always dominate any small widget by point count."""
    result = page.evaluate(
        """
        () => {
            const buttons = Array.from(document.querySelectorAll('button'))
                .filter(b => b.textContent.trim().toLowerCase() === 'year');
            if (buttons.length === 0) return { html: null, circles: 0 };

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
            return { html: best.html, circles: Math.max(best.circles, 0) };
        }
        """
    )
    return result.get("html"), result.get("circles", 0)


def wait_for_stable_chart(
    page: Page, max_polls: int = 10, poll_interval: float = 0.5,
) -> tuple[str | None, int]:
    """Don't trust the first non-zero circle read: wait_for_selector only
    guarantees ONE circle is attached, not that Recharts has finished
    painting all of them (a mount animation / staged reveal can produce a
    fixed early-frame count that looks identical across unrelated cards
    -- exactly what a production run of the Coolify twin of this script
    hit). Poll until the count is unchanged across two consecutive reads,
    or max_polls is exhausted."""
    last_html, last_circles = find_chart_container_html(page)
    for _ in range(max_polls):
        time.sleep(poll_interval)
        html, circles = find_chart_container_html(page)
        if circles == last_circles and html:
            return html, circles
        last_html, last_circles = html, circles
    return last_html, last_circles


def capture_one(
    page: Page, card: InputCard, timeout_ms: int, retries: int,
) -> tuple[str, int, str, str | None]:
    """Returns (status, num_circles, html_or_empty, error_reason)."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = page.goto(card.url, wait_until="domcontentloaded", timeout=timeout_ms)
            if not resp or resp.status >= 400:
                last_error = f"HTTP {resp.status if resp else 'none'} on attempt {attempt}"
                time.sleep(1.5 * attempt)
                continue

            # Normal-load settle: wait for network to go quiet briefly.
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PWTimeoutError:
                pass  # some pages never go fully idle (polling widgets); proceed anyway

            dismiss_cookie_banner(page)

            switched, switch_note = click_year_and_verify(page)
            if not switched:
                last_error = f"could not switch chart to Year range (attempt {attempt}): {switch_note}"
                print(f"  {last_error}")
                time.sleep(1.5 * attempt)
                continue

            try:
                page.wait_for_selector(
                    "circle.recharts-dot, circle[class*='area-dot']",
                    timeout=timeout_ms, state="attached",
                )
            except PWTimeoutError:
                last_error = f"chart circles never rendered (attempt {attempt})"
                time.sleep(1.5 * attempt)
                continue

            html, num_circles = wait_for_stable_chart(page)
            if not html or num_circles == 0:
                last_error = f"chart container found but 0 circles (attempt {attempt})"
                time.sleep(1.5 * attempt)
                continue

            return "success", num_circles, html, None

        except PWTimeoutError as exc:
            last_error = f"navigation/timeout on attempt {attempt}: {exc}"
            time.sleep(1.5 * attempt)
        except Exception as exc:  # noqa: BLE001
            last_error = f"unexpected error on attempt {attempt}: {exc!r}"
            time.sleep(1.5 * attempt)

    return "failed", 0, "", (last_error or "unknown failure")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="path to .txt/.csv/.json list of URLs or card IDs")
    ap.add_argument(
        "--out-dir", type=Path,
        default=Path(__file__).resolve().parents[3] / "fc26_chart_fixtures",
    )
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between cards")
    ap.add_argument("--delay-jitter", type=float, default=1.0, help="+/- random seconds added to delay")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--timeout-ms", type=int, default=20_000)
    ap.add_argument("--full-page-count", type=int, default=5, help="save full page HTML for first N successes")
    ap.add_argument("--headless", action="store_true", default=False)
    ap.add_argument("--force", action="store_true", help="re-capture cards already marked success")
    args = ap.parse_args()

    out_dir = args.out_dir
    full_pages_dir = out_dir / "full_pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    full_pages_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.csv"

    cards = load_input_list(args.input)
    if not cards:
        print("No cards found in input list.")
        return 1

    manifest = load_manifest(manifest_path)
    already_ok = {cid for cid, row in manifest.items() if row.get("status") == "success"}
    full_pages_saved = sum(1 for row in manifest.values() if row.get("full_page_saved") == "yes")

    print(f"{len(cards)} cards in input list, {len(already_ok)} already captured successfully.")
    todo = [c for c in cards if args.force or (c.card_id not in already_ok)]
    print(f"{len(todo)} cards to (re)attempt this run.\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))

        for i, card in enumerate(todo, 1):
            if card.card_id is None:
                cid_key = f"unresolved_{i}"
                print(f"[{i}/{len(todo)}] SKIP-INVALID input {card.raw!r}: could not determine card_id")
                manifest[cid_key] = {
                    "card_id": cid_key, "url": card.raw,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "failed", "num_circles": "0", "attempts": "0",
                    "error_reason": "could not determine card_id from input; supply a full canonical URL",
                    "full_page_saved": "no",
                }
                write_manifest(manifest_path, manifest)
                continue

            note = "" if card.resolved else " (UNVERIFIED URL GUESS from bare card_id)"
            print(f"[{i}/{len(todo)}] card_id={card.card_id}{note}: {card.url}")

            status, num_circles, html, error_reason = capture_one(
                page, card, args.timeout_ms, args.retries
            )

            full_page_saved = "no"
            if status == "success":
                (out_dir / f"{card.card_id}.html").write_text(html, encoding="utf-8")
                if full_pages_saved < args.full_page_count:
                    try:
                        full_html = page.content()
                        (full_pages_dir / f"{card.card_id}.html").write_text(full_html, encoding="utf-8")
                        full_pages_saved += 1
                        full_page_saved = "yes"
                    except Exception as exc:  # noqa: BLE001
                        print(f"  (full-page save failed, non-fatal: {exc!r})")
                print(f"  OK: {num_circles} circles")
            else:
                if not card.resolved:
                    error_reason = (
                        f"{error_reason}; note: card_id-only input, URL was an "
                        "unverified guess -- supply the full canonical URL instead"
                    )
                print(f"  FAILED: {error_reason}")

            manifest[card.card_id] = {
                "card_id": card.card_id,
                "url": card.url,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "num_circles": str(num_circles),
                "attempts": str(args.retries),
                "error_reason": error_reason or "",
                "full_page_saved": full_page_saved,
            }
            write_manifest(manifest_path, manifest)  # flush after every card

            if i < len(todo):
                sleep_for = max(0.0, args.delay + random.uniform(-args.delay_jitter, args.delay_jitter))
                time.sleep(sleep_for)

        browser.close()

    total_success = sum(1 for row in manifest.values() if row.get("status") == "success")
    total_failed = sum(1 for row in manifest.values() if row.get("status") == "failed")
    print(f"\nDone. manifest: {total_success} success, {total_failed} failed, "
          f"{len(manifest)} total rows -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
