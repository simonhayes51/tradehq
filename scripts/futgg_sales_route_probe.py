#!/usr/bin/env python
"""Site-wide FUT.GG sales-route reconnaissance probe (phase 2).

Phase 1 (scripts/futgg_r2_capture_probe.py) checked r2.fut.gg's own
manifest.json for a bulk completed-sales feed and found none among its
27 keys - that rules out an ADVERTISED, manifest-backed bulk feed
equivalent to the BIN-price feed. It does NOT prove the signed
per-card endpoint (/api/fut/player-prices/26/<eaId>/) is the only
possible sales source: a batch API, a GraphQL operation, a WebSocket
stream, or sales embedded directly in page-hydration data would never
show up in that CDN manifest at all.

This probe looks for those instead - broader in scope than phase 1,
narrower in what it will act on:

    python scripts/futgg_sales_route_probe.py \\
        --url "https://www.fut.gg/players/1114-roberto-baggio/26-1114/" \\
        --url "https://www.fut.gg/players/" \\
        --save-dir /tmp/sales_route_capture

CANNOT BE RUN FROM A CLAUDE CODE REMOTE SANDBOX SESSION - same fut.gg
egress block documented in scripts/futgg_price_probe.py and
scripts/futgg_r2_capture_probe.py. Run from the production sales
container, a local machine, or anywhere with real network access.
See futgg_r2_capture_probe.py's own docstring for exactly how to run
it inside the deployed SalesSync container (same Xvfb/Chromium setup,
same command shape).

WHAT IT DOES
-------------
For every --url you pass (each one a REAL page you navigated to via
the site's own public URLs - a player overview page, that player's
Prices tab, a search/listing page, a market/popular-player page,
anything reachable through normal navigation), this probe:

  1. Attaches page.on("response", ...) BEFORE navigating (the same
     passive-capture pattern as futgg_r2_capture_probe.py and
     auto_sync/futgg_bulk_price_sync.py's harvest_feed() - observing
     what the real page's own JS requests, never impersonating a
     request itself) and records every first-party (*.fut.gg) fetch/
     XHR response and first-party JavaScript bundle.
  2. Attaches page.on("websocket", ...) to log any WebSocket
     connection the page opens, and inspects any text frames sent or
     received on it.
  3. Tries clicking any Prices/Sales/Auctions-labelled tab it can find
     (best-effort, never fatal - FUT.GG's tabs are client-side, so
     there is no separate URL to navigate to for them).
  4. For every captured fetch/XHR JSON body: prints method, URL,
     status, content-type, size, and a structural summary (top-level
     keys, or array length + first-element keys).
  5. For every captured first-party JS bundle: greps its decoded text
     (not just its filename) for the same keyword list.
  6. Flags anything - URL, JSON body text, or JS bundle text - that
     contains completedAuctions, soldPrice, soldDate, recentSales,
     auction, transaction, player-prices, price-access/sign, or
     r2.fut.gg.

WHAT IT DELIBERATELY DOES NOT DO
-----------------------------------
- Does not guess, brute-force, or construct any URL beyond what a real
  page's own JS/navigation already requested. Every --url you pass
  must be a page you reached through the site's own public navigation
  - this probe does not crawl links or discover pages on its own.
- Does not bypass any access control, auth, or challenge.
- Does not conclude an endpoint "is" anything from its filename alone
  - only from what its actual captured content contains (see the
  keyword-match logic, which searches body text, not just names).
- Writes nothing except, optionally, captured bodies/bundles under
  --save-dir. No database dependency at all.

WHAT COMES NEXT
-----------------
This script only CAPTURES and FLAGS. Turning "response X mentions
soldPrice" into "route X is/isn't a viable multi-card sales source
that could replace the per-card collector" needs a human (or an LLM
given the raw captured evidence) to actually read the flagged bodies -
that judgement call is deliberately NOT automated here. Inspect
anything flagged under --save-dir, or paste this script's stdout back
for that analysis.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_SCRIPT_DIR))
# Two possible layouts for where auto_sync's modules (futgg_price_access,
# futgg_r2_capture_probe) actually live, depending on how this script was
# deployed:
#   - a full repo checkout: <repo_root>/auto_sync/futgg_price_access.py
#   - the deployed SalesSync container: Dockerfile.sales's build context
#     is auto_sync/ itself (per COOLIFY.md's Base Directory), so its
#     "COPY . ." puts auto_sync's own files directly at /app - there is
#     no /app/auto_sync subdirectory there at all.
# Try both rather than assuming either.
_auto_sync_dir = _REPO_ROOT / "auto_sync"
if _auto_sync_dir.is_dir():
    sys.path.insert(0, str(_auto_sync_dir))
sys.path.insert(0, str(_REPO_ROOT))

import futgg_price_access as price_access  # noqa: E402
# Reused rather than duplicated - phase 1's probe already implements
# and tests crash detection and JSON structural summarisation; no
# reason for phase 2 to reimplement either.
from futgg_r2_capture_probe import (  # noqa: E402
    HEADLESS, TIMEOUT_MS, is_crashed_page_error, summarize_body,
)

DEFAULT_SETTLE_MS = 4000
PENDING_DRAIN_TIMEOUT_S = 30

#: Exactly the list asked for - matched as case-insensitive substrings
#: against URLs, JSON response bodies (as raw text, not just parsed
#: keys - "auction"/"transaction" might appear as a VALUE, not a key),
#: and first-party JS bundle text.
SALES_KEYWORDS = (
    "completedAuctions", "soldPrice", "soldDate", "recentSales",
    "auction", "transaction", "player-prices", "price-access/sign", "r2.fut.gg",
)


def find_keyword_hits(haystack: str) -> list[str]:
    lower = haystack.lower()
    return [kw for kw in SALES_KEYWORDS if kw.lower() in lower]


def is_first_party(url: str) -> bool:
    """True for any *.fut.gg host - the site itself, its API, and its
    CDN. Never matches a third-party analytics/ads host, which this
    probe has no interest in and would only add noise."""
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return False
    return host == "fut.gg" or host.endswith(".fut.gg")


#: Playwright resource types this probe captures for full inspection.
#: "document"/"stylesheet"/"image"/"font"/"media" are skipped entirely
#: - they are never going to carry sales data and would only add noise
#: (per the module docstring's explicit scope: Fetch, XHR, WebSocket,
#: and first-party JS bundles).
_DATA_RESOURCE_TYPES = ("fetch", "xhr")
_SCRIPT_RESOURCE_TYPE = "script"


@dataclass
class NetworkRecord:
    source_page: str
    kind: str  # "fetch" | "xhr" | "script" | "websocket"
    method: str
    url: str
    status: int
    content_type: str
    byte_size: int
    summary: str
    matched_keywords: list[str] = field(default_factory=list)
    saved_path: Optional[str] = None


def _safe_filename_for(url: str, index: int, suffix: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("?", 1)[0].rsplit("/", 1)[-1]) or "capture"
    return f"{index:04d}_{stem}{suffix}"


class SalesRouteCapture:
    """Owns every record across the whole probe run, plus the passive
    response/websocket listeners that do the actual observing. One
    instance per run - see R2Capture in futgg_r2_capture_probe.py for
    the same shape applied to a narrower scope."""

    def __init__(self, save_dir: Optional[Path]):
        self.save_dir = save_dir
        self.records: list[NetworkRecord] = []

    def attach(self, page, source_page: str) -> tuple[list, Any, Any]:
        pending: list[asyncio.Task] = []

        async def _read_data_response(response, kind: str) -> None:
            try:
                body = await response.body()
            except Exception as exc:
                print(f"  [capture] could not read body for {response.url}: {exc!r}", file=sys.stderr)
                return
            self._record_data(source_page, response, kind, body)

        async def _read_script_response(response) -> None:
            try:
                body = await response.body()
            except Exception as exc:
                print(f"  [capture] could not read script body for {response.url}: {exc!r}", file=sys.stderr)
                return
            self._record_script(source_page, response, body)

        def on_response(response) -> None:
            url = response.url
            if not is_first_party(url):
                return
            try:
                rtype = response.request.resource_type
            except Exception:
                return
            if rtype in _DATA_RESOURCE_TYPES:
                pending.append(asyncio.ensure_future(_read_data_response(response, rtype)))
            elif rtype == _SCRIPT_RESOURCE_TYPE:
                pending.append(asyncio.ensure_future(_read_script_response(response)))

        def on_websocket(ws) -> None:
            self._record_websocket_open(source_page, ws.url)

            def on_frame_received(payload) -> None:
                self._record_websocket_frame(source_page, ws.url, payload, direction="received")

            def on_frame_sent(payload) -> None:
                self._record_websocket_frame(source_page, ws.url, payload, direction="sent")

            try:
                ws.on("framereceived", on_frame_received)
                ws.on("framesent", on_frame_sent)
            except Exception as exc:
                print(f"  [capture] could not attach websocket frame listeners: {exc!r}", file=sys.stderr)

        page.on("response", on_response)
        page.on("websocket", on_websocket)
        return pending, on_response, on_websocket

    def _record_data(self, source_page: str, response, kind: str, body: bytes) -> None:
        url = response.url
        try:
            method = response.request.method
        except Exception:
            method = "?"
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:
            content_type = ""

        parsed = None
        try:
            parsed = json.loads(body)
        except Exception:
            pass
        summary = summarize_body(parsed, len(body))
        text_for_search = body.decode("utf-8", errors="replace")
        matched = find_keyword_hits(url + "\n" + text_for_search)

        saved_path = self._maybe_save(url, body, ".json")

        self._append(NetworkRecord(
            source_page=source_page, kind=kind, method=method, url=url,
            status=response.status, content_type=content_type, byte_size=len(body),
            summary=summary, matched_keywords=matched, saved_path=saved_path,
        ))

    def _record_script(self, source_page: str, response, body: bytes) -> None:
        url = response.url
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:
            content_type = ""
        text = body.decode("utf-8", errors="replace")
        matched = find_keyword_hits(url + "\n" + text)
        summary = f"first-party JS bundle, {len(text)} chars"
        saved_path = self._maybe_save(url, body, ".js")

        record = NetworkRecord(
            source_page=source_page, kind="script", method="GET", url=url,
            status=response.status, content_type=content_type, byte_size=len(body),
            summary=summary, matched_keywords=matched, saved_path=saved_path,
        )
        self._append(record, extra_lines=self._snippet_lines(text, matched) if matched else None)

    def _record_websocket_open(self, source_page: str, ws_url: str) -> None:
        matched = find_keyword_hits(ws_url)
        self._append(NetworkRecord(
            source_page=source_page, kind="websocket", method="WS", url=ws_url,
            status=0, content_type="", byte_size=0,
            summary="WebSocket connection opened", matched_keywords=matched,
        ))

    def _record_websocket_frame(self, source_page: str, ws_url: str, payload: Any, *, direction: str) -> None:
        text = payload if isinstance(payload, str) else "<binary frame>"
        matched = find_keyword_hits(text)
        if not matched:
            return  # only uninteresting frames are this common; don't log every ping/pong
        self._append(NetworkRecord(
            source_page=source_page, kind="websocket", method=f"WS-{direction}", url=ws_url,
            status=0, content_type="", byte_size=len(text),
            summary=f"WebSocket {direction} frame, {len(text)} char(s)", matched_keywords=matched,
        ))

    def _maybe_save(self, url: str, body: bytes, suffix: str) -> Optional[str]:
        if self.save_dir is None or not body:
            return None
        try:
            name = _safe_filename_for(url, len(self.records), suffix)
            path = self.save_dir / name
            path.write_bytes(body)
            return str(path)
        except Exception as exc:
            print(f"  [capture] could not save body for {url}: {exc!r}", file=sys.stderr)
            return None

    @staticmethod
    def _snippet_lines(text: str, matched: list[str], *, context: int = 60) -> list[str]:
        lines = []
        for kw in matched[:3]:  # cap noise - the flag itself is the important signal
            idx = text.lower().find(kw.lower())
            if idx < 0:
                continue
            start, end = max(0, idx - context), min(len(text), idx + len(kw) + context)
            snippet = text[start:end].replace("\n", " ")
            lines.append(f"      ...{snippet}...")
        return lines

    def _append(self, record: NetworkRecord, extra_lines: Optional[list[str]] = None) -> None:
        self.records.append(record)
        flag = "  *** SALES-KEYWORD MATCH ***" if record.matched_keywords else ""
        print(f"  [{record.kind}] {record.method} {record.url}{flag}")
        if record.status or record.content_type or record.byte_size:
            print(f"      status={record.status} content-type={record.content_type!r} bytes={record.byte_size}")
        print(f"      {record.summary}")
        if record.matched_keywords:
            print(f"      matched keyword(s): {record.matched_keywords}")
        if extra_lines:
            for line in extra_lines:
                print(line)
        if record.saved_path:
            print(f"      saved: {record.saved_path}")


#: Best-effort tab labels to click after each page load - FUT.GG's tabs
#: are client-side, no separate URL, so this is the only way to reach
#: whatever a Prices/Sales/Auctions panel would lazily request.
_TAB_LABEL_PATTERNS = (
    re.compile(r"\bprice", re.IGNORECASE),
    re.compile(r"\b(sale|sales|auction|auctions)\b", re.IGNORECASE),
    re.compile(r"\b(market|popular|trending)\b", re.IGNORECASE),
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


async def _new_page_with_capture(context, source_page: str, capture: "SalesRouteCapture"):
    page = await context.new_page()
    pending, on_response, on_websocket = capture.attach(page, source_page)
    return page, pending, on_response, on_websocket


async def _detach_and_close(page, on_response, on_websocket) -> None:
    for event, handler in (("response", on_response), ("websocket", on_websocket)):
        try:
            page.remove_listener(event, handler)
        except Exception:
            pass
    try:
        await page.close()
    except Exception:
        pass


async def probe_one_url(context, url: str, capture: "SalesRouteCapture", *, settle_ms: int) -> None:
    """Same shape as futgg_r2_capture_probe.py's probe_one_url(): load,
    settle, best-effort tab-click, drain pending body reads, always
    detach+close - crash-guarded throughout (see that function's own
    docstring for the production incident this pattern was hardened
    against: a crash during the plain settle wait must not abort every
    remaining --url)."""
    page, pending, on_response, on_websocket = await _new_page_with_capture(context, url, capture)
    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        except Exception as exc:
            if not is_crashed_page_error(exc):
                print(f"  failed to load {url}: {exc!r} - skipping", file=sys.stderr)
                return
            print(f"  page crashed loading {url} - recreating and retrying once", file=sys.stderr)
            await _detach_and_close(page, on_response, on_websocket)
            page, pending, on_response, on_websocket = await _new_page_with_capture(context, url, capture)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            except Exception as exc2:
                print(f"  failed to load {url} even after recreating the page: {exc2!r} - skipping", file=sys.stderr)
                return

        try:
            await page.wait_for_timeout(settle_ms)
            await _try_click_tabs(page, settle_ms=settle_ms)
        except Exception as exc:
            if is_crashed_page_error(exc):
                print(f"  page crashed after loading {url} (captures already made are kept) - moving on", file=sys.stderr)
            else:
                print(f"  settle/tab-click step failed on {url}: {exc!r} (non-fatal)", file=sys.stderr)

        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=PENDING_DRAIN_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                print(f"  timed out waiting for some captured bodies on {url}", file=sys.stderr)
            except Exception as exc:
                print(f"  error while draining captured bodies on {url}: {exc!r}", file=sys.stderr)
    finally:
        await _detach_and_close(page, on_response, on_websocket)


def print_final_summary(capture: "SalesRouteCapture") -> None:
    print("\n" + "=" * 72)
    print("SALES ROUTE PROBE - SUMMARY")
    print("=" * 72)
    if not capture.records:
        print(
            "No first-party fetch/XHR/script/websocket traffic was observed on "
            "any probed page. Either none of the pages you tried make any such "
            "request, or something prevented the normal page load (see any "
            "warnings above)."
        )
        return

    matched = [r for r in capture.records if r.matched_keywords]
    if matched:
        print(f"\n{len(matched)} response(s)/frame(s) matched a sales-related keyword:\n")
        for r in matched:
            print(f"  [{r.kind}] {r.method} {r.url}")
            print(f"      seen on: {r.source_page}")
            print(f"      keywords: {r.matched_keywords}")
            if r.saved_path:
                print(f"      saved: {r.saved_path}")
    else:
        print(
            "\nNo response, script bundle, or websocket frame matched any of: "
            f"{list(SALES_KEYWORDS)}"
        )

    by_kind: dict[str, int] = {}
    for r in capture.records:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    print(f"\nTotal captured: {len(capture.records)} ({by_kind})")

    print(
        "\nNOTE: this only reports what the pages you probed actually "
        "triggered, via their own real navigation/tab-clicks. It does NOT "
        "prove no other sales route exists - a page shape not yet tried "
        "could behave differently. This script also does not judge whether "
        "any matched route could replace the per-card collector (single vs "
        "multi-card, coverage, rate-limit behaviour) - that needs a human "
        "(or an LLM given this output) to read the actual flagged bodies."
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Passively capture first-party fetch/XHR/script/websocket traffic across "
                    "several real FUT.GG pages, looking for any sales route beyond the known "
                    "per-card signed endpoint (see this file's own module docstring).",
    )
    p.add_argument(
        "--url", action="append", dest="urls", required=True,
        help="A FUT.GG page URL to probe (repeatable - pass once per page reached via the "
             "site's own public navigation: player overview, a search/listing page, a "
             "market/popular-player page, etc. This script does not discover or crawl pages itself.)",
    )
    p.add_argument(
        "--save-dir", type=str, default=None,
        help="Directory to save every captured JSON body and JS bundle into (optional)",
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
    capture = SalesRouteCapture(save_dir)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(user_agent=price_access.DEFAULT_USER_AGENT, locale="en-GB")
            for url in args.urls:
                print(f"\n--- probing {url} ---")
                try:
                    await probe_one_url(context, url, capture, settle_ms=args.settle_ms)
                except Exception as exc:
                    print(f"  unexpected failure probing {url}: {exc!r} - moving on to the next URL", file=sys.stderr)
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
