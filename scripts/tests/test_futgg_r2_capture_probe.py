"""Tests for scripts/futgg_r2_capture_probe.py - the passive r2.fut.gg
reconnaissance probe.

Pure-function pieces (keyword matching, key collection, base-name
grouping, crash-error detection, JSON summarisation) are tested
directly. The capture mechanism itself - a page.on("response", ...)
listener, the same passive-capture pattern
auto_sync/futgg_bulk_price_sync.py's harvest_feed() already uses - is
exercised end-to-end against a real (headless locally) Chromium via
Playwright route interception, mirroring
scripts/tests/test_futgg_price_probe.py's own approach: the MockRouter
serves a fake page whose own JS triggers r2.fut.gg-shaped fetches, and
the probe's real page.on("response") listener observes them off the
wire exactly as it would in production - no mock of the probe's own
logic.
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

import futgg_r2_capture_probe as probe  # noqa: E402


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------
# Pure functions - no browser
# ---------------------------------------------------------------------

class TestBaseResourceName:
    def test_strips_the_republish_content_hash(self):
        assert probe.base_resource_name("player-prices-index.v1.0ecea41c.json") == "player-prices-index"

    def test_strips_a_differently_shaped_hash_too(self):
        assert probe.base_resource_name("player-prices-ps5-dyn.v1.fc8db2c1.json") == "player-prices-ps5-dyn"

    def test_falls_back_to_stripping_the_extension_when_no_hash_suffix_matches(self):
        assert probe.base_resource_name("manifest.json") == "manifest"

    def test_never_returns_empty(self):
        assert probe.base_resource_name("") == ""  # degenerate input, must not raise


class TestIsCrashedPageError:
    def test_target_crashed_is_recognised(self):
        assert probe.is_crashed_page_error(Exception("Page.evaluate: Target crashed"))

    def test_closed_target_is_recognised(self):
        assert probe.is_crashed_page_error(Exception("Target page, context or browser has been closed"))

    def test_an_ordinary_timeout_is_not_a_crash(self):
        assert not probe.is_crashed_page_error(Exception("Timeout 45000ms exceeded"))


class TestCollectKeys:
    def test_collects_top_level_dict_keys(self):
        keys = probe._collect_keys({"a": 1, "b": 2})
        assert set(keys) >= {"a", "b"}

    def test_descends_into_nested_dicts(self):
        keys = probe._collect_keys({"data": {"completedAuctions": []}})
        assert "data" in keys
        assert "completedAuctions" in keys

    def test_descends_into_list_of_dicts_via_first_few_elements(self):
        keys = probe._collect_keys({"items": [{"soldPrice": 1}, {"soldPrice": 2}]})
        assert "items" in keys
        assert "soldPrice" in keys

    def test_is_bounded_and_does_not_hang_on_a_huge_structure(self):
        huge = {f"k{i}": {f"nested{i}": i} for i in range(10_000)}
        keys = probe._collect_keys(huge, limit=50)
        assert len(keys) <= 50

    def test_non_dict_non_list_yields_nothing(self):
        assert probe._collect_keys(42) == []
        assert probe._collect_keys("just a string") == []
        assert probe._collect_keys(None) == []


class TestSummarizeBody:
    def test_dict_summary_lists_top_level_keys(self):
        summary = probe.summarize_body({"id0": 27, "d": [1, 2, 3]}, raw_len=100)
        assert "2 top-level key(s)" in summary
        assert "'d'" in summary or "d" in summary

    def test_list_of_dicts_summary_shows_first_item_keys(self):
        summary = probe.summarize_body([{"soldPrice": 1}, {"soldPrice": 2}], raw_len=50)
        assert "2 item(s)" in summary
        assert "soldPrice" in summary

    def test_plain_list_summary(self):
        summary = probe.summarize_body([1, 2, 3], raw_len=10)
        assert "3 item(s)" in summary

    def test_unparseable_body_reports_byte_length(self):
        summary = probe.summarize_body(None, raw_len=1234)
        assert "1234" in summary
        assert "not JSON" in summary


class TestMatchedKeywordsFor:
    def test_matches_on_filename_alone(self):
        matched = probe.matched_keywords_for("player-prices-index.v1.abc.json", [])
        assert "price" in matched

    def test_matches_on_json_keys_alone(self):
        matched = probe.matched_keywords_for("some-unrelated-blob.json", ["completedAuctions"])
        assert "completed" in matched
        assert "auction" in matched

    def test_no_match_returns_empty_list(self):
        matched = probe.matched_keywords_for("player-teams-index.v1.abc.json", ["teamId", "leagueId"])
        assert matched == []

    def test_case_insensitive(self):
        matched = probe.matched_keywords_for("PLAYER-SALES-FEED.JSON", [])
        assert "sale" in matched


# ---------------------------------------------------------------------
# End-to-end: drive probe_one_url()/probe._run() against a real
# (headless locally) Chromium via route interception, mirroring
# scripts/tests/test_futgg_price_probe.py's MockRouter approach.
# ---------------------------------------------------------------------

ANCHOR_URL = "https://www.fut.gg/players/1114-roberto-baggio/26-1114/"
R2_INDEX_URL = "https://r2.fut.gg/26/player-prices-index.v1.testhash.json"
R2_SALES_URL = "https://r2.fut.gg/26/player-completed-sales-dyn.v1.testhash2.json"

_PAGE_HTML = f"""
<html><body>
<script>
fetch('{R2_INDEX_URL}').catch(function(e) {{}});
fetch('{R2_SALES_URL}').catch(function(e) {{}});
</script>
</body></html>
"""


class MockRouter:
    """Fulfils the fake anchor page (whose inline JS fires the two
    r2.fut.gg-shaped fetches below) and the two fake r2 responses
    themselves. Anything else 599s loudly rather than silently hitting
    real network - the same fail-fast shape
    scripts/tests/test_futgg_price_probe.py's own MockRouter uses."""

    def __init__(self) -> None:
        self.requested_urls: list[str] = []

    async def handle(self, route) -> None:
        url = route.request.url
        self.requested_urls.append(url)
        if url == ANCHOR_URL:
            await route.fulfill(status=200, content_type="text/html", body=_PAGE_HTML)
            return
        if url == R2_INDEX_URL:
            await route.fulfill(
                status=200, content_type="application/json",
                headers={"access-control-allow-origin": "*"},
                body=json.dumps({"id0": 27, "d": [14, 10, 189, 6]}),
            )
            return
        if url == R2_SALES_URL:
            await route.fulfill(
                status=200, content_type="application/json",
                headers={"access-control-allow-origin": "*"},
                body=json.dumps({"data": {"completedAuctions": [{"soldPrice": 5000}]}}),
            )
            return
        await route.fulfill(status=599, content_type="text/plain", body="unmocked request: " + url)


def _launch_kwargs() -> dict[str, Any]:
    """Same sandbox-only Chromium-path fallback as
    scripts/tests/test_futgg_price_probe.py's own helper - see that
    file's copy for why this is needed here and never in production."""
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
    """Makes the REAL probe._run() usable in this sandbox and fully
    offline: injects the sandbox-only Chromium executable_path at
    launch and auto-attaches `router` to every page a context creates,
    before probe._run() ever navigates - same technique as
    scripts/tests/test_futgg_price_probe.py's identically-named helper,
    duplicated here (not imported) since these are independent CLI
    tools with no shared test-support module."""
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
    async def test_captures_every_r2_response_not_just_known_price_filenames(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        result = await probe._run(_Args())

        assert result == 0
        # _run() owns its own R2Capture instance internally with no
        # injection seam (mirrors how production actually calls it) -
        # so the assertions are against stdout, which every capture is
        # printed to.
        out = capsys.readouterr().out
        assert R2_INDEX_URL in out
        assert R2_SALES_URL in out

    @run_async
    async def test_flags_the_unknown_sales_shaped_feed_as_interesting(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        result = await probe._run(_Args())

        assert result == 0
        out = capsys.readouterr().out
        # The sales-shaped response must be called out explicitly.
        sales_section = out[out.index(R2_SALES_URL):out.index(R2_SALES_URL) + 400]
        assert "INTERESTING" in sales_section
        assert "player-completed-sales-dyn" in out  # base name appears in final summary

    @run_async
    async def test_final_summary_lists_every_distinct_resource_grouped_by_base_name(self, monkeypatch, capsys):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        await probe._run(_Args())

        out = capsys.readouterr().out
        assert "R2 CAPTURE PROBE - SUMMARY" in out
        assert "player-prices-index" in out
        assert "player-completed-sales-dyn" in out
        assert "does NOT prove no bulk sales feed exists" in out

    @run_async
    async def test_save_dir_writes_every_captured_body_to_disk(self, monkeypatch, tmp_path):
        router = MockRouter()
        _patch_playwright_for_router(monkeypatch, router)

        save_dir = tmp_path / "captures"
        await probe._run(_Args(save_dir=str(save_dir)))

        saved = list(save_dir.glob("*.json"))
        assert len(saved) == 2
        bodies = [json.loads(p.read_text()) for p in saved]
        assert any("id0" in b for b in bodies)
        assert any(
            isinstance(b.get("data"), dict) and "completedAuctions" in b["data"] for b in bodies
        )

    @run_async
    async def test_a_page_with_no_r2_traffic_at_all_reports_that_plainly(self, monkeypatch, capsys):
        router = MockRouter()

        async def handle_no_r2(route):
            url = route.request.url
            if url == ANCHOR_URL:
                await route.fulfill(status=200, content_type="text/html", body="<html><body>no fetches here</body></html>")
                return
            await route.fulfill(status=599, content_type="text/plain", body="unmocked: " + url)

        router.handle = handle_no_r2
        _patch_playwright_for_router(monkeypatch, router)

        result = await probe._run(_Args())

        assert result == 0
        out = capsys.readouterr().out
        assert "No r2.fut.gg/26/ requests were observed" in out


class TestCrashRecoveryLogic:
    """Unit-level (fake context/page, no real browser) coverage of
    probe_one_url()'s retry-after-crash path - forcing a real Chromium
    renderer crash deterministically from a test is impractical, so
    this exercises the same control flow the production
    SignedPageHandle-style recovery in auto_sync/futgg_sales_sync.py
    is tested with: a fake page whose first goto() raises a
    crash-shaped error and whose second (recreated) page succeeds."""

    @run_async
    async def test_recreates_the_page_once_after_a_crash_and_continues(self):
        created_pages = []

        class _FakePage:
            def __init__(self, should_crash: bool):
                self.should_crash = should_crash
                self.closed = False
                self._listeners = {}

            def on(self, event, handler):
                self._listeners.setdefault(event, []).append(handler)

            def remove_listener(self, event, handler):
                self._listeners.get(event, []).remove(handler)

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

        capture = probe.R2Capture(None)
        await probe.probe_one_url(_FakeContext(), ANCHOR_URL, capture, settle_ms=1)

        assert len(created_pages) == 2
        assert created_pages[0].closed
        assert created_pages[1].closed

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

        capture = probe.R2Capture(None)
        await probe.probe_one_url(_FakeContext(), ANCHOR_URL, capture, settle_ms=1)

        assert len(created_pages) == 1  # no retry for a non-crash failure
        assert created_pages[0].closed
