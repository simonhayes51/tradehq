#!/usr/bin/env python
"""Manual FUT.GG r2.fut.gg reconnaissance probe.

Answers one question: does FUT.GG serve a bulk COMPLETED-SALES feed
anywhere on r2.fut.gg, the way it already serves bulk BIN prices via
player-prices-index/player-prices-<platform>-dyn (see
auto_sync/futgg_bulk_price_sync.py)? Confirmed so far (see that file's
own module docstring and auto_sync/futgg_sales_shadow.py's): a bulk
PRICE feed exists; no bulk SALES feed has been found; sales still come
one card at a time from the signed /api/fut/player-prices/<year>/<eaId>/
endpoint. This probe is a targeted, repeatable way to look for one,
rather than guessing.

    python scripts/futgg_r2_capture_probe.py \\
        --url "https://www.fut.gg/players/1114-roberto-baggio/26-1114/" \\
        --url "https://www.fut.gg/players/195864-paul-pogba/26-117636376/" \\
        --save-dir /tmp/r2_capture

CANNOT BE RUN FROM A CLAUDE CODE REMOTE SANDBOX SESSION for its real
purpose: that environment's egress proxy explicitly denies fut.gg
(confirmed for both www.fut.gg and r2.fut.gg - see
scripts/futgg_price_probe.py's own module docstring for the identical
limitation). Run this from the production sales container, a local
machine, or any other environment with real network access to fut.gg.

RUNNING IT INSIDE THE DEPLOYED COLLECTOR CONTAINER
---------------------------------------------------
The Railway sales container already has Xvfb running on :99 (started
by docker-entrypoint-sales.sh for the live SalesSync process) and a
real Chromium (see Dockerfile.sales). Exec into that running container
and reuse the same display rather than starting a second Xvfb:

    DISPLAY=:99 PLAYWRIGHT_HEADLESS=false python scripts/futgg_r2_capture_probe.py \\
        --url "https://www.fut.gg/players/1114-roberto-baggio/26-1114/" \\
        --save-dir /tmp/r2_capture

(Railway: `railway run --service <sales-service> -- <command above>`,
or open a shell via the Railway/Coolify dashboard and run it directly.)
Headless also works (default PLAYWRIGHT_HEADLESS=true) if you only need
the CDN reconnaissance and not a Cloudflare-sensitive fetch - this probe
never calls the signed price-access endpoint, so the headless-detection
finding that forced headed mode for signed_fetch does not apply here.

WHY THIS DOES NOT FETCH r2.fut.gg DIRECTLY
--------------------------------------------
r2.fut.gg refuses every direct request this codebase has ever tried -
plain HTTP, context.request with origin/referer headers, in-page
fetch() (CORS blocked). manifest.json 403s identically to the blobs
themselves - see auto_sync/futgg_bulk_price_sync.py's TRANSPORT section
and its "AUDIT NOTE (bulk CDN feed task, 2026-09)", and
auto_sync/futgg_sales_shadow.py's own module docstring for the same
finding. There is no lower-effort "poll the manifest" or "guess the
filename" path against this origin - the only thing that has ever
worked is letting a real page's own JS make the request and observing
it off the wire, exactly as harvest_feed() does for the two known price
files.

This probe does the same thing, generalised: it attaches a
page.on("response", ...) listener (the SAME passive-capture mechanism
harvest_feed() uses, not a route()-based re-fetch) before navigation,
and for any response whose URL contains "r2.fut.gg/26/" - not just the
two known price filenames - it reads the body off the already-arrived
response and inspects it. It never impersonates the request, never
constructs a URL itself, and never re-fetches anything: everything
captured is something the real page's own JavaScript asked for and
already received.

WHAT IT PRINTS
---------------
For every captured response: URL, HTTP status, content-type, body size,
and - if the body parses as JSON - a structural summary (top-level keys
for an object; length and first-element keys for an array). A response
is flagged INTERESTING if its filename or any JSON key (collected up to
a bounded depth/count) contains "sale", "auction", "completed",
"transaction", "history", or "price" - whether or not it matches the
already-known price-feed shape (the known price files DO match
"price" and will show as interesting too - that's expected, not a bug).
A final summary groups every distinct resource seen (by filename with
its content hash stripped, since the hash changes on every republish -
see harvest_feed()'s own docstring) across every URL you probed.

IMPORTANT: a run against N pages only proves what those N pages
triggered. It does NOT prove no bulk sales feed exists anywhere on this
CDN - a market/search page, a different player, or a promo/SBC page
could trigger something the pages you tried never do. Run this against
several different page shapes before concluding anything from a null
result; the final summary says this again as a standing reminder.

NO DATABASE, NO WRITES, NO GUESSING
-------------------------------------
DATABASE_URL is never read - this script has no database dependency at
all. Nothing is written except, optionally, the raw captured JSON
bodies under --save-dir (one file per response) for later inspection.
No filename or URL is ever constructed by this script beyond what the
real page itself requested.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "auto_sync"))

import futgg_price_access as price_access  # noqa: E402


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


# Same convention as scripts/futgg_price_probe.py and
# auto_sync/futgg_sales_sync.py: headless by default (safe/portable),
# headed (with Xvfb) only where the container is already set up for it.
# Headless is fine for THIS probe specifically - it never touches the
# signed price-access endpoint that headless Cloudflare-detection
# targets (see Dockerfile.sales's own comment for that finding).
HEADLESS = env_bool("PLAYWRIGHT_HEADLESS", True)
TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "45000"))
DEFAULT_SETTLE_MS = int(os.getenv("FUTGG_R2_PROBE_SETTLE_MS", "4000"))
#: How long to wait for all in-flight body reads (see R2Capture.attach)
#: to finish after settling - mirrors harvest_feed()'s own 30s cap on
#: asyncio.gather so one stuck response can't hang the whole probe.
PENDING_DRAIN_TIMEOUT_S = 30

#: The only thing this probe ever matches against - never a guessed
#: filename, never a manifest path. See module docstring's "WHY THIS
#: DOES NOT FETCH r2.fut.gg DIRECTLY".
R2_URL_MARKER = "r2.fut.gg/26/"

#: Case-insensitive substrings that flag a captured resource as worth a
#: human's attention, checked against both the filename and every JSON
#: key collected from the body (see _collect_keys).
INTERESTING_KEYWORDS = ("sale", "auction", "completed", "transaction", "history", "price")

#: Strips the republish content hash from a filename (e.g.
#: "player-prices-index.v1.0ecea41c.json" -> "player-prices-index") so
#: the final summary groups by FEED IDENTITY, not by one-off hash -
#: harvest_feed()'s own docstring notes the hash changes on every
#: republish (observed within ~25 minutes), so raw URLs would never
#: group across separate probe runs or separate pages.
_HASH_SUFFIX_RE = re.compile(r"\.v\d+\.[0-9a-fA-F]{6,}\.json$", re.IGNORECASE)


def base_resource_name(filename: str) -> str:
    stripped = _HASH_SUFFIX_RE.sub("", filename)
    if stripped == filename:
        stripped = re.sub(r"\.[^./]+$", "", filename)
    return stripped or filename


#: Duplicated (not imported) from auto_sync/futgg_sales_sync.py's
#: identically-named function on purpose: importing that module
#: requires DATABASE_URL to already be set (it raises at import time
#: otherwise) and pulls in asyncpg/db_connect - a real dependency chain
#: for a two-line pure-string check, and this script's whole point is
#: "no database, no auto_sync worker dependency". See that function's
#: own docstring for the production incident (a crashed Playwright page
#: never recovers on its own) this same detection was built to catch.
_CRASHED_PAGE_ERROR_MARKERS = ("Target crashed", "has been closed")


def is_crashed_page_error(exc: BaseException) -> bool:
    text = str(exc)
    return any(marker in text for marker in _CRASHED_PAGE_ERROR_MARKERS)


def _collect_keys(value: Any, *, depth: int = 3, limit: int = 300, _out: Optional[list] = None) -> list[str]:
    """All string keys found in `value` down to `depth` levels, capped
    at `limit` total - bounded so a ~167 KB dyn blob's array of prices
    can't make this crawl indefinitely."""
    if _out is None:
        _out = []
    if len(_out) >= limit or depth < 0:
        return _out
    if isinstance(value, dict):
        for k, v in value.items():
            if len(_out) >= limit:
                break
            _out.append(str(k))
            _collect_keys(v, depth=depth - 1, limit=limit, _out=_out)
    elif isinstance(value, list):
        for item in value[:5]:  # first few elements only - same shape repeats
            if len(_out) >= limit:
                break
            _collect_keys(item, depth=depth - 1, limit=limit, _out=_out)
    return _out


def summarize_body(parsed: Any, raw_len: int) -> str:
    if isinstance(parsed, dict):
        keys = sorted(parsed.keys())
        shown = keys[:20]
        more = f", +{len(keys) - 20} more" if len(keys) > 20 else ""
        return f"JSON object, {len(keys)} top-level key(s): {shown}{more}"
    if isinstance(parsed, list):
        if parsed and isinstance(parsed[0], dict):
            first_keys = sorted(parsed[0].keys())
            return f"JSON array, {len(parsed)} item(s); first item keys: {first_keys}"
        return f"JSON array, {len(parsed)} item(s)"
    if parsed is None:
        return f"not JSON ({raw_len} bytes)"
    return f"JSON scalar ({type(parsed).__name__}), {raw_len} bytes"


def matched_keywords_for(filename: str, keys: list[str]) -> list[str]:
    haystack = filename.lower()
    matched = [kw for kw in INTERESTING_KEYWORDS if kw in haystack]
    key_blob = " ".join(k.lower() for k in keys)
    matched += [kw for kw in INTERESTING_KEYWORDS if kw not in matched and kw in key_blob]
    return matched


@dataclass
class CaptureRecord:
    source_page: str
    url: str
    status: int
    content_type: str
    byte_size: int
    summary: str
    matched_keywords: list[str] = field(default_factory=list)
    saved_path: Optional[str] = None


def _safe_filename_for(url: str, index: int) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("?", 1)[0].rsplit("/", 1)[-1]) or "capture"
    return f"{index:04d}_{stem}"


class R2Capture:
    """Owns everything captured across every page probed this run, plus
    the passive response-listener that does the actual observing - the
    same page.on("response", ...) mechanism harvest_feed() already uses
    successfully, generalised from two known filenames to "anything
    under r2.fut.gg/26/". One instance per whole probe run - filenames
    saved to --save-dir are numbered globally so nothing from one
    page's capture overwrites another's."""

    def __init__(self, save_dir: Optional[Path]):
        self.save_dir = save_dir
        self.records: list[CaptureRecord] = []

    def attach(self, page, source_page: str) -> tuple[list, Any]:
        """Attaches the listener to `page` and returns (pending_tasks,
        on_response) - the caller is responsible for awaiting the
        pending tasks once settled and removing the listener when done
        with this page, exactly mirroring harvest_feed()'s own
        goto -> settle -> gather -> finally-remove_listener shape."""
        pending: list[asyncio.Task] = []

        async def _read(response) -> None:
            try:
                body = await response.body()
            except Exception as exc:
                print(f"  [capture] could not read body for {response.url}: {exc!r}", file=sys.stderr)
                return
            self._record(source_page, response, body)

        def on_response(response) -> None:
            if R2_URL_MARKER not in response.url:
                return
            pending.append(asyncio.ensure_future(_read(response)))

        page.on("response", on_response)
        return pending, on_response

    def _record(self, source_page: str, response, body: bytes) -> None:
        url = response.url
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:
            content_type = ""

        parsed = None
        try:
            parsed = json.loads(body)
        except Exception:
            pass
        keys = _collect_keys(parsed) if parsed is not None else []
        summary = summarize_body(parsed, len(body))
        filename = url.split("?", 1)[0].rsplit("/", 1)[-1]
        matched = matched_keywords_for(filename, keys)

        saved_path = None
        if self.save_dir is not None and body:
            try:
                name = _safe_filename_for(url, len(self.records))
                path = self.save_dir / f"{name}.json"
                path.write_bytes(body)
                saved_path = str(path)
            except Exception as exc:
                print(f"  [capture] could not save body for {url}: {exc!r}", file=sys.stderr)

        record = CaptureRecord(
            source_page=source_page, url=url, status=response.status,
            content_type=content_type, byte_size=len(body), summary=summary,
            matched_keywords=matched, saved_path=saved_path,
        )
        self.records.append(record)
        flag = "  *** INTERESTING ***" if matched else ""
        print(f"  [{response.status}] {url}{flag}")
        print(f"      content-type={content_type!r} bytes={len(body)}")
        print(f"      {summary}")
        if matched:
            print(f"      matched keyword(s): {matched}")
        if saved_path:
            print(f"      saved: {saved_path}")


#: Best-effort tab labels to click after the initial page load, so a
#: lazily-rendered sales/prices panel gets a chance to fire its own
#: requests - not a hardcoded URL (FUT.GG's tabs are client-side, no
#: separate page load), and never fatal if nothing matches.
_TAB_LABEL_PATTERNS = (
    re.compile(r"\bprice", re.IGNORECASE),
    re.compile(r"\b(sale|sales|auction|auctions)\b", re.IGNORECASE),
)


async def _try_click_tabs(page, *, settle_ms: int) -> None:
    for pattern in _TAB_LABEL_PATTERNS:
        try:
            locator = page.get_by_text(pattern)
            count = await locator.count()
        except Exception:
            continue
        if count == 0:
            continue
        try:
            await locator.first.click(timeout=3000)
            await page.wait_for_timeout(settle_ms)
        except Exception:
            continue


async def _new_page_with_capture(context, source_page: str, capture: "R2Capture"):
    page = await context.new_page()
    pending, on_response = capture.attach(page, source_page)
    return page, pending, on_response


async def _detach_and_close(page, on_response) -> None:
    try:
        page.remove_listener("response", on_response)
    except Exception:
        pass
    try:
        await page.close()
    except Exception:
        pass


async def probe_one_url(context, url: str, capture: R2Capture, *, settle_ms: int) -> None:
    """Navigates to `url`, waits for hydration, tries clicking a
    prices/sales-shaped tab if one exists, then drains and closes -
    passively capturing every r2.fut.gg/26/ response along the way via
    R2Capture.attach(). Recreates the page once on a crashed-page error
    (see is_crashed_page_error's docstring) rather than aborting the
    whole run over one bad load."""
    page, pending, on_response = await _new_page_with_capture(context, url, capture)
    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        except Exception as exc:
            if not is_crashed_page_error(exc):
                print(f"  failed to load {url}: {exc!r} - skipping", file=sys.stderr)
                return
            print(f"  page crashed loading {url} - recreating and retrying once", file=sys.stderr)
            await _detach_and_close(page, on_response)
            page, pending, on_response = await _new_page_with_capture(context, url, capture)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            except Exception as exc2:
                print(f"  failed to load {url} even after recreating the page: {exc2!r} - skipping", file=sys.stderr)
                return

        await page.wait_for_timeout(settle_ms)
        try:
            await _try_click_tabs(page, settle_ms=settle_ms)
        except Exception as exc:
            if is_crashed_page_error(exc):
                print(f"  page crashed while clicking tabs on {url} - captures already made are kept", file=sys.stderr)
            else:
                print(f"  tab-click step failed on {url}: {exc!r} (non-fatal)", file=sys.stderr)

        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=PENDING_DRAIN_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                print(f"  timed out waiting for some captured response bodies on {url}", file=sys.stderr)
    finally:
        await _detach_and_close(page, on_response)


def print_final_summary(capture: R2Capture) -> None:
    print("\n" + "=" * 72)
    print("R2 CAPTURE PROBE - SUMMARY")
    print("=" * 72)
    if not capture.records:
        print(
            "No r2.fut.gg/26/ requests were observed on any probed page. Either "
            "none of the pages you tried trigger a bulk-CDN fetch, or something "
            "prevented the normal page load (see any warnings above)."
        )
        return

    by_base: dict[str, list[CaptureRecord]] = {}
    for r in capture.records:
        base = base_resource_name(r.url.split("?", 1)[0].rsplit("/", 1)[-1])
        by_base.setdefault(base, []).append(r)

    interesting_bases = sorted(b for b, recs in by_base.items() if any(r.matched_keywords for r in recs))
    if interesting_bases:
        print(f"\n{len(interesting_bases)} distinct resource(s) matched a sales/auction/history/price keyword:")
        for b in interesting_bases:
            recs = by_base[b]
            pages = sorted({r.source_page for r in recs})
            kws = sorted({kw for r in recs for kw in r.matched_keywords})
            print(f"  - {b}")
            print(f"      keywords: {kws}")
            print(f"      seen on {len(pages)} page(s), {len(recs)} response(s) total")
    else:
        print(
            "\nNo distinct r2.fut.gg/26/ resource matched sale/auction/completed/"
            "transaction/history/price beyond what's already known."
        )

    print(f"\nAll {len(by_base)} distinct resource(s) observed across every page probed:")
    for b in sorted(by_base):
        recs = by_base[b]
        flag = "  <-- interesting" if b in interesting_bases else ""
        print(f"  - {b} ({len(recs)} response(s)){flag}")

    print(
        "\nNOTE: this only reports what the pages you probed actually "
        "triggered. It does NOT prove no bulk sales feed exists anywhere on "
        "this CDN - try more player pages, a market/search page, and any "
        "other page shape not yet tried before concluding a negative result."
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Passively capture every r2.fut.gg/26/ request a real FUT.GG page "
                    "load makes, looking for a bulk completed-sales feed (see this file's own module docstring).",
    )
    p.add_argument(
        "--url", action="append", dest="urls", required=True,
        help="A FUT.GG page URL to probe (repeatable - pass this flag once per page; "
             "include player pages AND, if you have one, a market/search-shaped page)",
    )
    p.add_argument(
        "--save-dir", type=str, default=None,
        help="Directory to save every captured JSON body into (optional - nothing is saved by default)",
    )
    p.add_argument(
        "--settle-ms", type=int, default=DEFAULT_SETTLE_MS,
        help=f"Milliseconds to wait after navigation/tab-click for lazy requests to fire (default {DEFAULT_SETTLE_MS})",
    )
    return p


async def _run(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
    capture = R2Capture(save_dir)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(user_agent=price_access.DEFAULT_USER_AGENT, locale="en-GB")
            for url in args.urls:
                print(f"\n--- probing {url} ---")
                await probe_one_url(context, url, capture, settle_ms=args.settle_ms)
        finally:
            await browser.close()

    print_final_summary(capture)
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
