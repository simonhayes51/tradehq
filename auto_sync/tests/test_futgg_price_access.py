"""Tests for auto_sync/futgg_price_access.py - the reusable FUT.GG
price-access signing + proof-of-work + fetch client.

These drive the REAL JS (DETAIL_FETCH_SCRIPT) inside a real Chromium via
Playwright's page.route() interception - fixture JSON responses, never
a real request to fut.gg (every route is fulfilled locally; nothing
here can reach the network even if it tried). Deliberately NOT mocking
page.evaluate() itself: that would only prove the Python glue calls
page.evaluate() with some script, not that the script itself is
correct - and the script (signing, proof-of-work solving via
crypto.subtle, leading-zero-bit checking) is exactly the part with the
highest cost of being subtly wrong. Headless is fine here even though
production uses headed Chromium specifically to defeat Cloudflare
fingerprinting - no Cloudflare is involved when every request is
intercepted and fulfilled locally.
"""
from __future__ import annotations

import asyncio
import functools
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import futgg_price_access as mod

ANCHOR_URL = "https://www.fut.gg/players/1114-roberto-baggio/26-1114/"
SIGN_URL_RE = re.compile(r"https://www\.fut\.gg/api/fut/price-access/sign/$")
DATA_URL_RE = re.compile(r"https://www\.fut\.gg/api/fut/player-prices/\d+/\d+/\?.*")


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


def _sandbox_chromium_path() -> Optional[str]:
    """This sandbox's globally-installed Playwright package can predate
    (or postdate) the browser revision actually present under
    PLAYWRIGHT_BROWSERS_PATH, so p.chromium.launch()'s default lookup
    can fail here even though nothing is actually wrong with either the
    package or the browser. Only used as a fallback for running THESE
    tests inside this specific sandbox; never touches production, which
    resolves its own matched browser normally (or overrides via
    FUTGG_TEST_CHROMIUM_PATH in whatever environment needs it)."""
    override = os.getenv("FUTGG_TEST_CHROMIUM_PATH")
    if override:
        return override
    base = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    candidates = sorted(glob.glob(os.path.join(base, "chromium-*/chrome-linux*/chrome")))
    return candidates[-1] if candidates else None


class MockRouter:
    """Queued fixture responses per URL pattern, fulfilled entirely
    locally - route.fulfill() short-circuits before anything reaches a
    real network stack."""

    def __init__(self) -> None:
        self._queues: dict[re.Pattern, list[dict[str, Any]]] = {}

    def queue(self, pattern: re.Pattern, *, status: int, body: Any, headers: Optional[dict] = None) -> "MockRouter":
        self._queues.setdefault(pattern, []).append(
            {"status": status, "body": body, "headers": headers or {}}
        )
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
                    status=fixture["status"],
                    content_type="application/json",
                    body=json.dumps(fixture["body"]),
                    headers={k: str(v) for k, v in fixture["headers"].items()},
                )
                return
        # Anything unrouted fails loudly rather than silently reaching
        # (blocked, but still wrong) real network.
        await route.fulfill(status=599, content_type="text/plain", body="unmocked request: " + url)


async def _open_page(playwright, router: MockRouter):
    launch_kwargs: dict[str, Any] = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    chromium_path = _sandbox_chromium_path()
    if chromium_path:
        launch_kwargs["executable_path"] = chromium_path
    browser = await playwright.chromium.launch(**launch_kwargs)
    context = await browser.new_context(user_agent=mod._DEFAULT_USER_AGENT, locale="en-GB")
    page = await context.new_page()
    await page.route("**/*", router.handle)
    await page.goto(ANCHOR_URL, wait_until="domcontentloaded", timeout=15_000)
    return browser, page


def _leading_zero_bits(data: bytes) -> int:
    bits = 0
    for byte in data:
        if byte == 0:
            bits += 8
            continue
        for bit in range(7, -1, -1):
            if not (byte >> bit) & 1:
                bits += 1
            else:
                return bits
        return bits
    return bits


def _find_nonce(subject: str, difficulty: int, *, limit: int = 200_000) -> int:
    """Python-side reference solver, used only to construct fixtures
    (a real, valid proof for a given subject/difficulty) - never
    imported by production code, which only ever solves this in-browser
    via crypto.subtle, matching the real frontend."""
    for nonce in range(limit):
        digest = hashlib.sha256(f"{subject}:{nonce}".encode("utf-8")).digest()
        if _leading_zero_bits(digest) >= difficulty:
            return nonce
    raise AssertionError(f"no nonce found under {limit} for difficulty={difficulty}")


class TestImmediateSuccess:
    @run_async
    async def test_no_challenge_returns_full_detail(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=tok123", "expiresIn": 120}},
            )
            router.queue(
                DATA_URL_RE, status=200,
                body={"data": {
                    "eaId": 117636376, "currentPrice": 300000,
                    "completedAuctions": [{"soldPrice": 324000, "soldDate": "2026-09-04T09:31:00Z"}],
                    "liveAuctions": [], "history": [], "priceRange": {"min": 1, "max": 2},
                    "overview": {}, "momentum": {},
                }},
            )
            browser, page = await _open_page(pw, router)
            try:
                detail = await mod.fetch_player_price_detail(117636376, page=page)
                assert detail.current_price == 300000
                assert detail.challenge_solved is False
                assert detail.completed_auctions[0]["soldPrice"] == 324000
            finally:
                await browser.close()


class TestStructuredProofOfWork:
    @run_async
    async def test_challenge_is_solved_and_retried_successfully(self):
        from playwright.async_api import async_playwright

        subject = "card:117636376:1788514290"
        difficulty = 8
        nonce = _find_nonce(subject, difficulty)

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=403,
                body={"data": {"challengeRequired": True, "challenge": {
                    "algorithm": "sha256-prefix-v1", "subject": subject, "difficulty": difficulty,
                    "issuedAt": 1788514290, "salt": "s", "token": "t",
                }}},
            )
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=aftertok", "expiresIn": 120}},
            )
            router.queue(DATA_URL_RE, status=200, body={"data": {"eaId": 117636376, "currentPrice": 300000}})
            browser, page = await _open_page(pw, router)
            try:
                detail = await mod.fetch_player_price_detail(117636376, page=page)
                assert detail.challenge_solved is True
                assert detail.challenge_attempts == nonce + 1
                assert detail.current_price == 300000
            finally:
                await browser.close()

    @run_async
    async def test_leading_zero_bit_validation_rejects_a_short_nonce_search(self):
        """A difficulty that CANNOT be met inside a tiny nonce ceiling
        proves the check is a real bit-count comparison, not something
        that accidentally accepts the first nonce it tries."""
        from playwright.async_api import async_playwright

        tiny_script = mod.DETAIL_FETCH_SCRIPT.replace(
            f"const maxNonce = {mod.PRICE_ACCESS_POW_MAX_NONCE};",
            "const maxNonce = 20;",
        )
        assert "const maxNonce = 20;" in tiny_script, "test setup: replacement token not found"

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=403,
                body={"data": {"challengeRequired": True, "challenge": {
                    "algorithm": "sha256-prefix-v1", "subject": "unlikely-in-20-tries",
                    "difficulty": 32, "issuedAt": 1, "salt": "s", "token": "t",
                }}},
            )
            browser, page = await _open_page(pw, router)
            try:
                result = await page.evaluate(tiny_script, {"eaId": 1, "gameYear": 26, "platform": "ps5", "diagnostic": False})
                assert result["error"] == "pow_exhausted"
                assert result["challengeAttempts"] == 20
            finally:
                await browser.close()


class TestNonceExhaustion:
    @run_async
    async def test_exhausting_the_nonce_ceiling_raises_a_typed_error(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            # difficulty=64 is unreachable inside any small ceiling - a 32-byte
            # digest with 64 leading zero bits essentially never occurs.
            router.queue(
                SIGN_URL_RE, status=403,
                body={"data": {"challengeRequired": True, "challenge": {
                    "algorithm": "sha256-prefix-v1", "subject": "impossible", "difficulty": 64,
                    "issuedAt": 1, "salt": "s", "token": "t",
                }}},
            )
            browser, page = await _open_page(pw, router)
            try:
                # fetch_player_price_detail always uses the real ceiling
                # (5,000,000) - too slow for a unit test - so this test
                # calls a tiny-ceiling variant of the same script directly
                # and asserts on the error TRANSLATION logic
                # (_raise_for_error) separately from the browser call.
                tiny_script = mod.DETAIL_FETCH_SCRIPT.replace(
                    f"const maxNonce = {mod.PRICE_ACCESS_POW_MAX_NONCE};", "const maxNonce = 30;",
                )
                assert "const maxNonce = 30;" in tiny_script, "test setup: replacement token not found"
                result = await page.evaluate(tiny_script, {"eaId": 1, "gameYear": 26, "platform": "ps5", "diagnostic": False})
                assert result["error"] == "pow_exhausted"
                with pytest.raises(mod.ProofOfWorkExhaustedError) as exc_info:
                    mod._raise_for_error(1, result)
                assert exc_info.value.attempts == 30
            finally:
                await browser.close()


class TestUnknownChallengeAlgorithm:
    @run_async
    async def test_unrecognised_algorithm_raises_unsolvable(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=403,
                body={"data": {"challengeRequired": True, "challenge": {
                    "algorithm": "argon2-v2", "subject": "x", "difficulty": 1,
                    "issuedAt": 1, "salt": "s", "token": "t",
                }}},
            )
            browser, page = await _open_page(pw, router)
            try:
                with pytest.raises(mod.UnsolvableChallengeError) as exc_info:
                    await mod.fetch_player_price_detail(1, page=page)
                assert exc_info.value.algorithm == "argon2-v2"
            finally:
                await browser.close()


class TestUnstructuredChallenge:
    @run_async
    async def test_challenge_without_a_challenge_object_is_unsolvable(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(SIGN_URL_RE, status=403, body={"data": {"challengeRequired": True}})
            browser, page = await _open_page(pw, router)
            try:
                with pytest.raises(mod.UnsolvableChallengeError):
                    await mod.fetch_player_price_detail(1, page=page)
            finally:
                await browser.close()


class TestExpiredSignedUrl:
    @run_async
    async def test_a_403_on_the_data_fetch_raises_expired_signature(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/1/?verify=stale", "expiresIn": 120}},
            )
            router.queue(DATA_URL_RE, status=403, body={})
            browser, page = await _open_page(pw, router)
            try:
                with pytest.raises(mod.ExpiredSignatureError):
                    await mod.fetch_player_price_detail(1, page=page)
            finally:
                await browser.close()


class TestOnePermittedDeniedRetry:
    @run_async
    async def test_429_denied_retries_once_and_can_succeed(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(SIGN_URL_RE, status=429, body={"data": {"denied": True}})
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/1/?verify=ok", "expiresIn": 120}},
            )
            router.queue(DATA_URL_RE, status=200, body={"data": {"eaId": 1, "currentPrice": 5}})
            browser, page = await _open_page(pw, router)
            try:
                detail = await mod.fetch_player_price_detail(1, page=page)
                assert detail.current_price == 5
            finally:
                await browser.close()

    @run_async
    async def test_429_denied_twice_does_not_loop_and_raises_rate_limited(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(SIGN_URL_RE, status=429, body={"data": {"denied": True}})
            router.queue(SIGN_URL_RE, status=429, body={"data": {"denied": True}})
            # A THIRD queued response would only be consumed if the code
            # incorrectly looped past the one permitted retry.
            router.queue(SIGN_URL_RE, status=200, body={"data": {"url": "https://www.fut.gg/x?verify=should_not_be_used", "expiresIn": 120}})
            browser, page = await _open_page(pw, router)
            try:
                with pytest.raises(mod.RateLimitedError):
                    await mod.fetch_player_price_detail(1, page=page)
                # The third fixture must still be queued - proof the retry
                # loop stopped after exactly one attempt.
                assert len(router._queues[SIGN_URL_RE]) == 1
            finally:
                await browser.close()


class TestOrdinary429Backoff:
    @run_async
    async def test_a_429_without_denied_is_not_retried_at_all(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(SIGN_URL_RE, status=429, body={"data": {}}, headers={"retry-after": "30"})
            browser, page = await _open_page(pw, router)
            try:
                with pytest.raises(mod.RateLimitedError):
                    await mod.fetch_player_price_detail(1, page=page)
                # No second sign request was ever made.
                assert len(router._queues[SIGN_URL_RE]) == 0
            finally:
                await browser.close()

    @run_async
    async def test_429_on_the_data_fetch_carries_retry_after(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/1/?verify=ok", "expiresIn": 120}},
            )
            router.queue(DATA_URL_RE, status=429, body={}, headers={"retry-after": "12"})
            browser, page = await _open_page(pw, router)
            try:
                with pytest.raises(mod.RateLimitedError) as exc_info:
                    await mod.fetch_player_price_detail(1, page=page)
                assert exc_info.value.retry_after == 12.0
            finally:
                await browser.close()


class TestSignatureRedaction:
    @run_async
    async def test_the_verify_token_never_reaches_a_log_line(self, caplog):
        import logging
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(SIGN_URL_RE, status=403, body={"data": {"challengeRequired": True}})
            browser, page = await _open_page(pw, router)
            try:
                with caplog.at_level(logging.WARNING, logger="futgg_price_access"):
                    with pytest.raises(mod.UnsolvableChallengeError):
                        await mod.fetch_player_price_detail(1, page=page)
                text = " ".join(r.message for r in caplog.records)
                assert "verify=" not in text
            finally:
                await browser.close()

    def test_redact_signed_url_strips_the_verify_token(self):
        redacted = mod.redact_signed_url(
            "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=abcdEFGH12345678"
        )
        assert "verify=abcdEFGH12345678" not in redacted
        assert "player-prices/26/117636376" in redacted

    def test_canonical_cache_key_strips_default_platform(self):
        assert mod.canonical_cache_key(117636376, 26, "ps5") == "/api/fut/player-prices/26/117636376/"
        assert mod.canonical_cache_key(117636376, 26, "pc") == "/api/fut/player-prices/26/117636376/?platform=pc"


class TestFullDetailParsing:
    @run_async
    async def test_every_documented_field_is_captured(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/117636376/?verify=ok", "expiresIn": 120}},
            )
            router.queue(
                DATA_URL_RE, status=200,
                body={"data": {
                    "eaId": 117636376, "currentPrice": 300000,
                    "completedAuctions": [{"soldPrice": 324000}],
                    "liveAuctions": [{"startingBid": 1000}],
                    "history": [{"price": 1, "date": "x"}],
                    "priceRange": {"min": 1, "max": 2},
                    "overview": {"rating": 91},
                    "momentum": {"trend": "up"},
                }},
            )
            browser, page = await _open_page(pw, router)
            try:
                detail = await mod.fetch_player_price_detail(117636376, page=page)
                assert detail.current_price == 300000
                assert detail.completed_auctions == [{"soldPrice": 324000}]
                assert detail.live_auctions == [{"startingBid": 1000}]
                assert detail.history == [{"price": 1, "date": "x"}]
                assert detail.price_range == {"min": 1, "max": 2}
                assert detail.overview == {"rating": 91}
                assert detail.momentum == {"trend": "up"}
                assert detail.raw["eaId"] == 117636376
            finally:
                await browser.close()


class TestInputValidation:
    @run_async
    async def test_invalid_ea_id_never_reaches_the_network(self):
        with pytest.raises(mod.InvalidEaIdError):
            await mod.fetch_player_price_detail(-1)
        with pytest.raises(mod.InvalidEaIdError):
            await mod.fetch_player_price_detail(0)


class TestSignOnlyRevealPath:
    """sign_player_price_url() - the debug-only function backing the
    CLI's --show-signed-url flag. Must be opt-in (default scripts never
    trigger it) and must never fetch the data endpoint."""

    @run_async
    async def test_reveals_the_signed_url_without_fetching_data(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/1/?verify=revealme", "expiresIn": 120}},
            )
            # Deliberately NOT queuing a DATA_URL_RE response - if
            # sign_player_price_url() ever fetches the data endpoint,
            # the unmocked-request fulfil (599) would surface as an
            # error the test doesn't expect, catching the regression.
            browser, page = await _open_page(pw, router)
            try:
                signed_url, expires_in = await mod.sign_player_price_url(1, page=page)
                assert "verify=revealme" in signed_url
                assert expires_in == 120
            finally:
                await browser.close()

    @run_async
    async def test_solves_a_structured_challenge_before_revealing(self):
        from playwright.async_api import async_playwright

        subject = "reveal-subject"
        difficulty = 8
        nonce = _find_nonce(subject, difficulty)

        async with async_playwright() as pw:
            router = MockRouter()
            router.queue(
                SIGN_URL_RE, status=403,
                body={"data": {"challengeRequired": True, "challenge": {
                    "algorithm": "sha256-prefix-v1", "subject": subject, "difficulty": difficulty,
                    "issuedAt": 1, "salt": "s", "token": "t",
                }}},
            )
            router.queue(
                SIGN_URL_RE, status=200,
                body={"data": {"url": "https://www.fut.gg/api/fut/player-prices/26/1/?verify=afterpow", "expiresIn": 120}},
            )
            browser, page = await _open_page(pw, router)
            try:
                signed_url, _ = await mod.sign_player_price_url(1, page=page)
                assert "verify=afterpow" in signed_url
            finally:
                await browser.close()

    def test_normal_scripts_never_pass_the_reveal_flag(self):
        assert "priceAccessSignAndFetch(eaId, gameYear, platform, diagnostic, true)" not in mod.DETAIL_FETCH_SCRIPT
        assert "priceAccessSignAndFetch(eaId, gameYear, platform, diagnostic, true)" not in mod.build_batch_script()
        assert "priceAccessSignAndFetch(eaId, gameYear, platform, diagnostic, true)" in mod.REVEAL_SIGNED_URL_SCRIPT


class TestNoSecondSigningImplementation:
    def test_the_batch_script_and_the_single_card_script_share_the_same_core(self):
        assert mod.PRICE_ACCESS_CORE_JS in mod.DETAIL_FETCH_SCRIPT
        assert mod.PRICE_ACCESS_CORE_JS in mod.build_batch_script()

    def test_fetch_script_never_returns_the_full_signed_url(self):
        """Mirrors futgg_sales_sync.py's own guard on _FETCH_SCRIPT -
        applied here to the canonical core every script is built from.

        Strengthened, not weakened, for revealSignedUrl (see
        sign_player_price_url()'s debug-only reveal path): the ONE
        return block allowed to reference the bare signedUrl identifier
        must also be gated on `revealSignedUrl` in the same block - any
        OTHER return block referencing it fails the test outright, and
        if the guarded one stops mentioning revealSignedUrl (e.g. the
        gate is accidentally removed) this test fails too."""
        source = mod.PRICE_ACCESS_CORE_JS
        return_blocks = re.findall(r"return\s*\{[^}]*\}", source)
        assert return_blocks, "expected at least one return {...} in the core script"
        signed_url_blocks = [b for b in return_blocks if re.search(r"\bsignedUrl\b", b)]
        assert len(signed_url_blocks) == 1, (
            f"expected exactly one return block to reference signedUrl "
            f"(the guarded reveal path), found {len(signed_url_blocks)}: {signed_url_blocks}"
        )
        only_block = signed_url_blocks[0]
        position = source.index(only_block)
        preceding_context = source[max(0, position - 300):position]
        assert "if (revealSignedUrl)" in preceding_context, (
            "the one return block that references signedUrl must be reachable "
            f"only through an immediately-preceding `if (revealSignedUrl)` guard - "
            f"preceding context was: {preceding_context!r}"
        )
