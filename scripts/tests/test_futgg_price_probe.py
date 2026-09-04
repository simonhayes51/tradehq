"""Tests for scripts/futgg_price_probe.py - the manual FUT.GG
price-detail diagnostic CLI.

Pure-function pieces (URL/target parsing, summary formatting, exit-code
mapping) are tested directly. The full --ea-id/--url end-to-end flow is
tested by driving _run() against a real (headless locally) Chromium via
Playwright route interception - the same approach
auto_sync/tests/test_futgg_price_access.py uses, and for the same
reason: this exercises the actual browser-driven code path, not a mock
of it.
"""
from __future__ import annotations

import asyncio
import functools
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR.parent / "auto_sync"))

import futgg_price_probe as probe  # noqa: E402
import futgg_price_access as price_access  # noqa: E402


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


class TestParseTarget:
    def test_ea_id_flag_wins_outright(self):
        ea_id, game_year, verify_url = probe.parse_target(ea_id=117636376, url=None, default_game_year=26)
        assert (ea_id, game_year, verify_url) == (117636376, 26, None)

    def test_rejects_a_non_positive_ea_id(self):
        with pytest.raises(probe.ProbeArgError):
            probe.parse_target(ea_id=0, url=None, default_game_year=26)
        with pytest.raises(probe.ProbeArgError):
            probe.parse_target(ea_id=-5, url=None, default_game_year=26)

    def test_neither_flag_is_an_error(self):
        with pytest.raises(probe.ProbeArgError):
            probe.parse_target(ea_id=None, url=None, default_game_year=26)

    def test_extracts_from_the_raw_api_url(self):
        ea_id, game_year, verify_url = probe.parse_target(
            ea_id=None, url="https://www.fut.gg/api/fut/player-prices/26/117636376/",
            default_game_year=99,
        )
        assert (ea_id, game_year) == (117636376, 26)
        assert verify_url is None  # no verify= param present

    def test_extracts_from_the_player_page_url(self):
        ea_id, game_year, verify_url = probe.parse_target(
            ea_id=None, url="https://www.fut.gg/players/195864-paul-pogba/26-117636376/",
            default_game_year=99,
        )
        assert (ea_id, game_year) == (117636376, 26)
        assert verify_url is None

    def test_a_url_with_a_verify_token_is_returned_for_one_attempt(self):
        url = "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=abc123"
        ea_id, game_year, verify_url = probe.parse_target(ea_id=None, url=url, default_game_year=26)
        assert verify_url == url

    def test_an_unparseable_url_raises(self):
        with pytest.raises(probe.ProbeArgError):
            probe.parse_target(ea_id=None, url="https://www.fut.gg/some/other/page/", default_game_year=26)

    def test_the_error_message_never_contains_the_verify_token(self):
        url = "https://www.fut.gg/some/other/page/?verify=SUPERSECRETTOKEN12345678"
        try:
            probe.parse_target(ea_id=None, url=url, default_game_year=26)
            pytest.fail("expected ProbeArgError")
        except probe.ProbeArgError as exc:
            assert "SUPERSECRETTOKEN12345678" not in str(exc)


class TestExitCodeMapping:
    @pytest.mark.parametrize(
        "exc,expected_code",
        [
            (price_access.RateLimitedError("x"), 6),
            (price_access.ExpiredSignatureError("x"), 5),
            (price_access.ProofOfWorkExhaustedError("x"), 4),
            (price_access.UnsolvableChallengeError("x"), 4),
            (price_access.InvalidResponseSchemaError("x"), 7),
            (price_access.InvalidEaIdError("x"), 2),
            (price_access.SigningError("x"), 3),
            (price_access.PriceAccessError("x"), 1),
        ],
    )
    def test_each_error_type_maps_to_its_documented_code(self, exc, expected_code):
        assert probe.exit_code_for_exception(exc) == expected_code

    def test_codes_are_all_distinct_except_the_documented_overlap(self):
        # UnsolvableChallengeError and ProofOfWorkExhaustedError share
        # code 4 deliberately (both mean "a bot challenge could not be
        # solved") - every other mapped exception type must have its own.
        codes = [code for _, code in probe._EXIT_BY_EXCEPTION[:-1]]  # exclude the base-class fallback
        assert len(codes) == len(set(codes)) + 1, "expected exactly one deliberate duplicate (challenge-related codes)"


class TestFormatSummary:
    def _detail(self, **overrides):
        defaults = dict(
            ea_id=117636376, game_year=26, platform="ps5", current_price=300000,
            completed_auctions=[
                {"soldPrice": 324000, "soldDate": "2026-09-04T09:31:38Z"},
                {"soldPrice": 280000, "soldDate": "2026-09-01T00:00:00Z"},
            ],
            live_auctions=[{"x": 1}, {"x": 2}], history=[1, 2, 3], price_range=None, overview=None, momentum=None,
            raw={}, fetched_at=datetime(2026, 9, 4, 9, 31, 38, tzinfo=timezone.utc),
            challenge_solved=False, challenge_attempts=None, signed_url_expires_in=None,
        )
        defaults.update(overrides)
        return price_access.PlayerPriceDetail(**defaults)

    def test_matches_the_documented_shape(self):
        text = probe.format_summary(self._detail())
        assert "EA ID: 117636376" in text
        assert "Platform: ps5" in text
        assert "Current price: 300,000" in text
        assert "Price updated: 2026-09-04T09:31:38Z" in text
        assert "Completed auctions: 2" in text
        assert "Live auctions: 2" in text
        assert "History points: 3" in text
        assert "Latest completed sale: 324,000 at 2026-09-04T09:31:38Z" in text
        assert "Oldest completed sale: 280,000 at 2026-09-01T00:00:00Z" in text

    def test_missing_current_price_is_reported_plainly_not_as_zero(self):
        text = probe.format_summary(self._detail(current_price=None))
        assert "Current price: (none reported)" in text
        assert "Current price: 0" not in text

    def test_proof_of_work_line_only_appears_when_solved(self):
        unsolved = probe.format_summary(self._detail())
        assert "Proof-of-work solved" not in unsolved
        solved = probe.format_summary(self._detail(challenge_solved=True, challenge_attempts=4821))
        assert "Proof-of-work solved: yes (4821 attempt(s))" in solved

    def test_never_prints_a_signed_url(self):
        text = probe.format_summary(self._detail())
        assert "verify=" not in text


class TestDetailToJsonable:
    def test_round_trips_through_json(self):
        detail = TestFormatSummary()._detail()
        payload = probe.detail_to_jsonable(detail)
        # Must be genuinely JSON-serialisable (datetime already isoformat()'d).
        json.dumps(payload)
        assert payload["eaId"] == 117636376
        assert payload["completedAuctions"][0]["soldPrice"] == 324000

    def test_never_contains_a_signed_url(self):
        detail = TestFormatSummary()._detail()
        payload = probe.detail_to_jsonable(detail)
        blob = json.dumps(payload)
        assert "verify=" not in blob


# ---------------------------------------------------------------------
# End-to-end: drive _run() against a real (headless locally) Chromium
# via route interception, mirroring auto_sync/tests/test_futgg_price_access.py.
# ---------------------------------------------------------------------

ANCHOR_URL = price_access.DEFAULT_ANCHOR_URL
SIGN_URL_RE = re.compile(r"https://www\.fut\.gg/api/fut/price-access/sign/$")
DATA_URL_RE = re.compile(r"https://www\.fut\.gg/api/fut/player-prices/\d+/\d+/\?.*")


class MockRouter:
    def __init__(self) -> None:
        self._queues: dict[re.Pattern, list[dict[str, Any]]] = {}

    def queue(self, pattern: re.Pattern, *, status: int, body: Any, headers: Optional[dict] = None) -> "MockRouter":
        self._queues.setdefault(pattern, []).append({"status": status, "body": body, "headers": headers or {}})
        return self

    async def handle(self, route) -> None:
        url = route.request.url
        if url == ANCHOR_URL or url.startswith(ANCHOR_URL):
            await route.fulfill(status=200, content_type="text/html", body="<html><body>anchor</body></html>")
            return
        for pattern, queue in self._queues.items():
            if pattern.search(url):
                if not queue:
                    await route.fulfill(status=500, content_type="application/json", body="{}")
                    return
                fixture = queue.pop(0)
                await route.fulfill(
                    status=fixture["status"], content_type="application/json",
                    body=json.dumps(fixture["body"]), headers={k: str(v) for k, v in fixture["headers"].items()},
                )
                return
        await route.fulfill(status=599, content_type="text/plain", body="unmocked request: " + url)


def _launch_kwargs() -> dict[str, Any]:
    """Same sandbox-only Chromium-path fallback as
    auto_sync/tests/test_futgg_price_access.py - see that file's
    _sandbox_chromium_path() for why this is needed here and never in
    production."""
    import glob
    import os

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


class _Args:
    def __init__(self, **kw):
        self.ea_id = kw.get("ea_id")
        self.url = kw.get("url")
        self.platform = kw.get("platform", "ps5")
        self.game_year = kw.get("game_year", 26)
        self.pretty = kw.get("pretty", False)
        self.summary = kw.get("summary", True)
        self.output = kw.get("output", None)
        self.show_signed_url = kw.get("show_signed_url", False)


def _patch_playwright_for_router(monkeypatch, router: MockRouter) -> None:
    """Makes the REAL probe._run() (not a mirror of its logic) usable in
    this sandbox and fully offline: injects the sandbox-only Chromium
    executable_path at launch (see _launch_kwargs()) and auto-attaches
    `router` to every page a context creates, before _run() ever
    navigates. Patches playwright.async_api.async_playwright itself
    (not probe.async_playwright), since _run() does a LOCAL
    `from playwright.async_api import async_playwright` import inside
    its own body - patching the source module is what actually reaches
    it."""
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


class TestTheRealRunFunctionEndToEnd:
    """Drives probe._run() itself (not the mirrored helper other tests
    in this file use) - exercises the actual production glue, including
    --output file-writing, exactly as `python futgg_price_probe.py`
    would run it."""

    @run_async
    async def test_writes_the_full_detail_to_the_output_file(self, monkeypatch, tmp_path, capsys):
        router = MockRouter()
        router.queue(
            SIGN_URL_RE, status=200,
            body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=tok", "expiresIn": 120}},
        )
        router.queue(
            DATA_URL_RE, status=200,
            body={"data": {"eaId": 117636376, "currentPrice": 300000, "completedAuctions": [{"soldPrice": 1}]}},
        )
        _patch_playwright_for_router(monkeypatch, router)

        output_path = tmp_path / "detail.json"
        args = _Args(ea_id=117636376, summary=False, output=str(output_path))
        result = await probe._run(args)

        assert result == 0
        written = json.loads(output_path.read_text())
        assert written["eaId"] == 117636376
        assert written["currentPrice"] == 300000
        captured = capsys.readouterr()
        assert f"wrote {output_path}" in captured.err
        # summary=False and output given -> no JSON dumped to stdout too.
        assert captured.out.strip() == ""

    @run_async
    async def test_summary_and_output_can_both_be_requested(self, monkeypatch, tmp_path, capsys):
        router = MockRouter()
        router.queue(
            SIGN_URL_RE, status=200,
            body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/1/?verify=tok", "expiresIn": 120}},
        )
        router.queue(DATA_URL_RE, status=200, body={"data": {"eaId": 1, "currentPrice": 42}})
        _patch_playwright_for_router(monkeypatch, router)

        output_path = tmp_path / "detail.json"
        args = _Args(ea_id=1, summary=True, output=str(output_path))
        result = await probe._run(args)

        assert result == 0
        assert output_path.exists()
        captured = capsys.readouterr()
        assert "Current price: 42" in captured.out

    @run_async
    async def test_default_output_is_full_json_on_stdout(self, monkeypatch, capsys):
        router = MockRouter()
        router.queue(
            SIGN_URL_RE, status=200,
            body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/1/?verify=tok", "expiresIn": 120}},
        )
        router.queue(DATA_URL_RE, status=200, body={"data": {"eaId": 1, "currentPrice": 42}})
        _patch_playwright_for_router(monkeypatch, router)

        args = _Args(ea_id=1, summary=False, output=None)
        result = await probe._run(args)

        assert result == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["eaId"] == 1
        assert payload["currentPrice"] == 42


class TestFullFlowAgainstAMockedBrowser:
    @run_async
    async def test_immediate_success_via_ea_id(self, capsys):
        router = MockRouter()
        router.queue(
            SIGN_URL_RE, status=200,
            body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=tok", "expiresIn": 120}},
        )
        router.queue(
            DATA_URL_RE, status=200,
            body={"data": {"eaId": 117636376, "currentPrice": 300000, "completedAuctions": [], "liveAuctions": []}},
        )
        result = await self._run_with_router(router, _Args(ea_id=117636376))
        captured = capsys.readouterr()
        assert result == 0
        assert "Current price: 300,000" in captured.out

    async def _run_with_router(self, router: MockRouter, args: "_Args") -> int:
        """Runs the same sequence probe._run() runs, but with an
        explicit router-backed page - _run() itself owns its browser
        end-to-end with no injection seam, so this mirrors its body
        rather than calling it directly. Kept in the test file rather
        than added to the CLI's own code, since production has no need
        for an injectable page (see futgg_price_probe.py's module
        docstring: it always runs as a real one-shot process)."""
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**_launch_kwargs())
            try:
                context = await browser.new_context(user_agent=price_access.DEFAULT_USER_AGENT, locale="en-GB")
                page = await context.new_page()
                await page.route("**/*", router.handle)
                await page.goto(ANCHOR_URL, wait_until="domcontentloaded", timeout=15_000)

                ea_id, game_year, verify_url = probe.parse_target(
                    ea_id=args.ea_id, url=args.url, default_game_year=args.game_year,
                )
                data = None
                if verify_url:
                    data = await probe.try_supplied_signed_url(page, verify_url)
                if data is not None:
                    detail = price_access.PlayerPriceDetail.from_response(
                        ea_id, game_year, args.platform, data,
                        challenge_solved=False, challenge_attempts=None, signed_url_expires_in=None,
                    )
                else:
                    try:
                        detail = await price_access.fetch_player_price_detail(
                            ea_id, game_year, args.platform, page=page, diagnostic=True,
                        )
                    except price_access.PriceAccessError as exc:
                        print(f"error: {exc}", file=sys.stderr)
                        return probe.exit_code_for_exception(exc)
            finally:
                await browser.close()

        if args.summary:
            print(probe.format_summary(detail))
        return 0

    @run_async
    async def test_a_stale_verify_url_falls_back_to_a_fresh_signature(self, capsys):
        """The exact scenario the brief describes: a URL with an
        expired/invalid verify= token is tried once, discarded, and the
        canonical sign flow is used instead - never persisted, never
        retried against the stale token itself."""
        router = MockRouter()
        # The supplied (stale) signed URL 403s on first try.
        router.queue(DATA_URL_RE, status=403, body={})
        # Falls through to a fresh sign + fetch.
        router.queue(
            SIGN_URL_RE, status=200,
            body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=freshtoken", "expiresIn": 120}},
        )
        router.queue(DATA_URL_RE, status=200, body={"data": {"eaId": 117636376, "currentPrice": 5000}})

        stale_url = "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=EXPIREDTOKEN"
        result = await self._run_with_router(router, _Args(url=stale_url))
        captured = capsys.readouterr()
        assert result == 0
        assert "Current price: 5,000" in captured.out
        # The stale token must never appear in anything printed.
        assert "EXPIREDTOKEN" not in captured.out
        assert "EXPIREDTOKEN" not in captured.err

    @run_async
    async def test_rate_limited_maps_to_exit_code_6(self):
        router = MockRouter()
        router.queue(SIGN_URL_RE, status=429, body={"data": {}}, headers={"retry-after": "20"})
        result = await self._run_with_router(router, _Args(ea_id=1))
        assert result == 6

    @run_async
    async def test_unstructured_challenge_maps_to_exit_code_4(self):
        router = MockRouter()
        router.queue(SIGN_URL_RE, status=403, body={"data": {"challengeRequired": True}})
        result = await self._run_with_router(router, _Args(ea_id=1))
        assert result == 4
