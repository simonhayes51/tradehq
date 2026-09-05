"""Tests for scripts/futgg_sales_route_probe.py - the site-wide
sales-route reconnaissance probe (phase 2, after
futgg_r2_capture_probe.py ruled out a manifest-backed bulk feed).

Pure-function pieces (keyword matching, first-party host detection)
are tested directly. The capture mechanism - page.on("response", ...)
for fetch/XHR/script, page.on("websocket", ...) for WebSocket frames -
is exercised end-to-end against a real (headless locally) Chromium via
Playwright route interception, mirroring
scripts/tests/test_futgg_r2_capture_probe.py's own approach.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR.parent / "auto_sync"))

import futgg_sales_route_probe as probe  # noqa: E402


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------
# Pure functions - no browser
# ---------------------------------------------------------------------

class TestFindKeywordHits:
    def test_matches_a_camelcase_json_key_case_insensitively(self):
        assert "completedAuctions" in probe.find_keyword_hits('{"completedauctions": []}')

    def test_matches_soldprice_and_solddate(self):
        hits = probe.find_keyword_hits("soldPrice=100 soldDate=2026-09-05")
        assert "soldPrice" in hits
        assert "soldDate" in hits

    def test_matches_url_shaped_keywords(self):
        hits = probe.find_keyword_hits("https://www.fut.gg/api/fut/price-access/sign/")
        assert "price-access/sign" in hits

    def test_matches_r2_fut_gg(self):
        assert "r2.fut.gg" in probe.find_keyword_hits("see https://r2.fut.gg/26/manifest.json")

    def test_no_match_returns_empty(self):
        assert probe.find_keyword_hits("just some ordinary text about teams and leagues") == []


class TestIsFirstParty:
    def test_main_site_is_first_party(self):
        assert probe.is_first_party("https://www.fut.gg/players/1114-x/26-1114/")

    def test_cdn_subdomain_is_first_party(self):
        assert probe.is_first_party("https://r2.fut.gg/26/manifest.json")

    def test_bare_apex_domain_is_first_party(self):
        assert probe.is_first_party("https://fut.gg/")

    def test_third_party_host_is_not_first_party(self):
        assert not probe.is_first_party("https://analytics.example.com/collect")

    def test_lookalike_host_is_not_first_party(self):
        # "notfut.gg" or "fut.gg.evil.com" must not be treated as fut.gg.
        assert not probe.is_first_party("https://notfut.gg/")
        assert not probe.is_first_party("https://fut.gg.evil.com/")

    def test_malformed_url_is_not_first_party(self):
        assert not probe.is_first_party("not a url at all")


# ---------------------------------------------------------------------
# End-to-end: drive probe_one_url()/probe._run() against a real
# (headless locally) Chromium via route interception.
# ---------------------------------------------------------------------

ANCHOR_URL = "https://www.fut.gg/players/1114-roberto-baggio/26-1114/"
BUNDLE_URL = "https://www.fut.gg/static/bundle.testhash.js"
SALES_XHR_URL = "https://www.fut.gg/api/fut/fake-sales-data/"
PLAIN_XHR_URL = "https://www.fut.gg/api/fut/fake-teams-data/"
THIRD_PARTY_URL = "https://analytics.example.com/collect"

_PAGE_HTML = f"""
<html><body>
<script src="{BUNDLE_URL}"></script>
<script>
fetch('{SALES_XHR_URL}').catch(function(e) {{}});
fetch('{PLAIN_XHR_URL}').catch(function(e) {{}});
fetch('{THIRD_PARTY_URL}').catch(function(e) {{}});
</script>
</body></html>
"""

_BUNDLE_JS = "function loadPrices() { return fetchSoldPrice(); } // soldPrice lives here"


class MockRouter:
    def __init__(self) -> None:
        self.requested_urls: list[str] = []

    async def handle(self, route) -> None:
        url = route.request.url
        self.requested_urls.append(url)
        if url == ANCHOR_URL:
            await route.fulfill(status=200, content_type="text/html", body=_PAGE_HTML)
            return
        if url == BUNDLE_URL:
            await route.fulfill(status=200, content_type="application/javascript", body=_BUNDLE_JS)
            return
        if url == SALES_XHR_URL:
            await route.fulfill(
                status=200, content_type="application/json",
                headers={"access-control-allow-origin": "*"},
                body=json.dumps({"completedAuctions": [{"soldPrice": 12345}]}),
            )
            return
        if url == PLAIN_XHR_URL:
            await route.fulfill(
                status=200, content_type="application/json",
                headers={"access-control-allow-origin": "*"},
                body=json.dumps({"teams": ["Arsenal", "Chelsea"]}),
            )
            return
        if url == THIRD_PARTY_URL:
            await route.fulfill(
                status=200, content_type="application/json",
                headers={"access-control-allow-origin": "*"},
                body="{}",
            )
            return
        await route.fulfill(status=599, content_type="text/plain", body="unmocked request: " + url)


def _launch_kwargs() -> dict[str, Any]:
    import glob

    kwargs: dict[str, Any] = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    override = os.getenv("FUTGG_TEST_CHROMIUM_PATH")
    if override:
        kwargs["executable_path"] = override
        return kwargs
    base = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    candidates = sorted(glob.glob(os.path.join(base, "chromium-*/chrome-linux*/chrome")))
    if candidates:
        kwargs["executable_path"] = candidates[-1]
    return kwargs


def _patch_playwright_for_router(monkeypatch, router: MockRouter) -> None:
    import playwright.async_api as pw_api

    real_async_playwright = pw_api.async_playwright

    class _PatchedPlaywrightContext:
        async def __aenter__(self):
            self._cm = real_async_playwright()
            pw = await self._cm.__aenter__()
            original_launch = pw.chromium.launch

            async def patched_launch(**kwargs):
                browser = await original_launch(**{**_launch_kwargs(), **kwargs})
                original_new_context = browser.new_context

                async def patched_new_context(**ctx_kwargs):
                    context = await original_new_context(**ctx_kwargs)
                    original_new_page = context.new_page

                    async def patched_new_page(*page_args, **page_kwargs):
                        page = await original_new_page(*page_args, **page_kwargs)
                        await page.route("**/*", router.handle)
                        return page

                    context.new_page = patched_new_page
                    return context

                browser.new_context = patched_new_context
                return browser

            pw.chromium.launch = patched_launch
            return pw

        async def __aexit__(self, *exc_info):
            return await self._cm.__aexit__(*exc_info)

    monkeypatch.setattr(pw_api, "async_playwright", lambda: _PatchedPlaywrightContext())


class _Args:
    def __init__(self, **kw):
        self.urls = kw.get("urls", [ANCHOR_URL])
        self.save_dir = kw.get("save_dir", None)
        self.settle_ms = kw.get("settle_ms", 300)


class TestProbeRunEndToEnd:
    @run_async
    async def test_flags_the_sales_shaped_xhr_response(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        result = await probe._run(_Args())

        assert result == 0
        out = capsys.readouterr().out
        assert SALES_XHR_URL in out
        section = out[out.index(SALES_XHR_URL):out.index(SALES_XHR_URL) + 400]
        assert "SALES-KEYWORD MATCH" in section
        assert "completedAuctions" in section or "soldPrice" in section

    @run_async
    async def test_flags_the_js_bundle_containing_a_sales_keyword(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        await probe._run(_Args())

        out = capsys.readouterr().out
        assert BUNDLE_URL in out
        section = out[out.index(BUNDLE_URL):out.index(BUNDLE_URL) + 500]
        assert "SALES-KEYWORD MATCH" in section
        assert "soldPrice" in section
        # A context snippet around the hit should be printed, not just the flag.
        assert "fetchSoldPrice" in section or "soldPrice" in section

    @run_async
    async def test_a_plain_first_party_response_is_captured_but_not_flagged(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        await probe._run(_Args())

        out = capsys.readouterr().out
        assert PLAIN_XHR_URL in out
        section = out[out.index(PLAIN_XHR_URL):out.index(PLAIN_XHR_URL) + 300]
        assert "SALES-KEYWORD MATCH" not in section

    @run_async
    async def test_third_party_requests_are_never_captured(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        await probe._run(_Args())

        out = capsys.readouterr().out
        assert THIRD_PARTY_URL not in out

    @run_async
    async def test_final_summary_reports_matched_and_total_counts(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        await probe._run(_Args())

        out = capsys.readouterr().out
        assert "SALES ROUTE PROBE - SUMMARY" in out
        assert "matched a sales-related keyword" in out
        assert "does NOT prove no other sales route exists" in out

    @run_async
    async def test_save_dir_writes_captured_bodies_and_bundles(self, monkeypatch, tmp_path):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        save_dir = tmp_path / "captures"
        await probe._run(_Args(save_dir=str(save_dir)))

        saved = list(save_dir.iterdir())
        assert any(p.suffix == ".js" for p in saved)
        assert any(p.suffix == ".json" for p in saved)


# ---------------------------------------------------------------------
# WebSocket record logic - unit-level (a real WS handshake needs a real
# server; these exercise the same matching/record logic directly
# against fake payloads, which is what actually decides whether a
# frame gets flagged).
# ---------------------------------------------------------------------

class TestWebSocketRecording:
    def test_a_sales_shaped_connection_url_is_flagged_on_open(self):
        capture = probe.SalesRouteCapture(None)
        capture._record_websocket_open("page1", "wss://www.fut.gg/ws/completedAuctions")
        assert len(capture.records) == 1
        # "completedAuctions" itself contains the substring "auction", so
        # both keywords legitimately match here.
        assert capture.records[0].matched_keywords == ["completedAuctions", "auction"]

    def test_an_ordinary_connection_url_produces_no_flag_but_is_recorded(self):
        capture = probe.SalesRouteCapture(None)
        capture._record_websocket_open("page1", "wss://www.fut.gg/ws/notifications")
        assert len(capture.records) == 1
        assert capture.records[0].matched_keywords == []

    def test_a_frame_mentioning_soldprice_is_recorded(self):
        capture = probe.SalesRouteCapture(None)
        capture._record_websocket_frame("page1", "wss://www.fut.gg/ws/x", '{"soldPrice": 999}', direction="received")
        assert len(capture.records) == 1
        assert "soldPrice" in capture.records[0].matched_keywords

    def test_an_uninteresting_frame_is_not_recorded_at_all(self):
        # Deliberate - logging every ping/pong frame would be pure noise;
        # only a frame that actually matches a sales keyword is worth a line.
        capture = probe.SalesRouteCapture(None)
        capture._record_websocket_frame("page1", "wss://www.fut.gg/ws/x", "ping", direction="sent")
        assert capture.records == []

    def test_a_binary_frame_is_never_mistaken_for_a_match(self):
        capture = probe.SalesRouteCapture(None)
        capture._record_websocket_frame("page1", "wss://www.fut.gg/ws/x", b"\x00\x01\x02", direction="received")
        assert capture.records == []


# ---------------------------------------------------------------------
# Crash recovery - fake context/page objects, mirroring
# scripts/tests/test_futgg_r2_capture_probe.py's own coverage of the
# same control flow.
# ---------------------------------------------------------------------

class TestCrashRecoveryLogic:
    @run_async
    async def test_recreates_the_page_once_after_a_crash_and_continues(self):
        created_pages = []

        class _FakePage:
            def __init__(self, should_crash: bool):
                self.should_crash = should_crash
                self.closed = False

            def on(self, event, handler):
                pass

            def remove_listener(self, event, handler):
                pass

            async def goto(self, url, **kwargs):
                if self.should_crash:
                    raise Exception("Page.goto: Target crashed")

            async def wait_for_timeout(self, ms):
                pass

            def get_by_text(self, pattern):
                class _Locator:
                    async def count(self_inner):
                        return 0
                return _Locator()

            async def close(self):
                self.closed = True

        class _FakeContext:
            def __init__(self):
                self.pages = [_FakePage(should_crash=True), _FakePage(should_crash=False)]

            async def new_page(self):
                page = self.pages[len(created_pages)]
                created_pages.append(page)
                return page

        capture = probe.SalesRouteCapture(None)
        await probe.probe_one_url(_FakeContext(), ANCHOR_URL, capture, settle_ms=1)

        assert len(created_pages) == 2
        assert created_pages[0].closed and created_pages[1].closed

    @run_async
    async def test_a_non_crash_failure_does_not_retry(self):
        created_pages = []

        class _FakePage:
            def __init__(self):
                self.closed = False

            def on(self, event, handler):
                pass

            def remove_listener(self, event, handler):
                pass

            async def goto(self, url, **kwargs):
                raise Exception("Timeout 45000ms exceeded")

            async def close(self):
                self.closed = True

        class _FakeContext:
            async def new_page(self):
                page = _FakePage()
                created_pages.append(page)
                return page

        capture = probe.SalesRouteCapture(None)
        await probe.probe_one_url(_FakeContext(), ANCHOR_URL, capture, settle_ms=1)

        assert len(created_pages) == 1
        assert created_pages[0].closed
