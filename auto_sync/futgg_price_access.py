"""FUT.GG player-price-detail client: the canonical price-access
signing + proof-of-work + fetch implementation.

WHY THIS MODULE EXISTS
-----------------------
futgg_sales_sync.py has always contained one working implementation of
FUT.GG's signed price-access flow (POST /price-access/sign/, then GET
the signed URL) - see that file's own module docstring for the full
Cloudflare/headless-browser investigation that shaped it. This module
extracts the per-card core of that flow into one place so a second
consumer (scripts/futgg_price_probe.py, the manual diagnostic CLI, and
any future caller such as a triggered detailed-sales queue) can reuse
it exactly rather than reimplementing signing and risking the two
implementations drifting apart. futgg_sales_sync.py's own _FETCH_SCRIPT
is now built from PRICE_ACCESS_CORE_JS defined here, not a separate
copy.

WHY THIS RUNS INSIDE A BROWSER PAGE, NOT A PLAIN HTTP CLIENT
--------------------------------------------------------------
futgg_sales_sync.py's module docstring documents production evidence
that Cloudflare challenges the signed endpoint based on headless-browser
fingerprinting (navigator.userAgentData leaking HeadlessChrome), not on
HTTP headers - a fully organic request with correct headers/cookies/UA
still gets challenged when the browser itself is headless. The deployed
fix is a headed Chromium via Xvfb (see Dockerfile.sales), with the
sign+fetch pair run as JS injected into a live fut.gg page via
Playwright's page.evaluate() - same-origin, carrying the real browser's
cookies and JS/TLS fingerprint. A plain requests/httpx session copying
headers would not carry that fingerprint and is expected to be
challenged. Every function here therefore takes (or opens) a real
Playwright Page and evaluates JS in it - there is no HTTP-only path.

THE PROOF-OF-WORK CHALLENGE (added to FUT.GG's frontend after this
repo's 4 August investigation - confirmed 4 September 2026 against the
currently-deployed frontend bundle, assets.fut.gg/ts/assets/index-
CgyCmute.js)
------------------------------------------------------------------------
POST /api/fut/price-access/sign/ now takes {"url": ..., "proof": null}.
A 403 response can carry TWO different things under challengeRequired,
and they must be told apart:

  1. STRUCTURED price-access proof-of-work - data.challenge exists with
     algorithm "sha256-prefix-v1" (subject/difficulty/issuedAt/salt/
     token). This is expected application behaviour: find a nonce in
     [0, PRICE_ACCESS_POW_MAX_NONCE) such that
     SHA-256(UTF8(f"{subject}:{nonce}")) has at least `difficulty`
     leading zero BITS, then resubmit the sign POST with
     {"difficulty", "issuedAt", "nonce": str(nonce), "salt", "token"}
     as `proof`. Solved here, in-browser (crypto.subtle.digest), same as
     the real frontend.

  2. UNSTRUCTURED Cloudflare/browser challenge - challengeRequired is
     true but there is no data.challenge (or its algorithm is not one
     this module knows). This is NOT solvable by computing anything -
     it is Cloudflare deciding the browser itself looks like a bot. The
     collector's existing fail/backoff/transport-switch behaviour is the
     correct response here, and this module raises
     UnsolvableChallengeError rather than attempting to bypass it.

Never conflate the two: solving (1) when FUT.GG actually meant (2) would
look like an infinite/expensive retry loop against a wall that no
computation gets through.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from playwright_stealth import Stealth

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from futgg_instrumentation import redact_url, redact_value

log = logging.getLogger("futgg_price_access")

# ---------------------------------------------------------------------
# Constants - matching the currently-deployed frontend bundle exactly
# (assets.fut.gg/ts/assets/index-CgyCmute.js, inspected 4 September
# 2026). Env-overridable for the same reason every other tunable in
# this codebase is: a value copied from a live bundle today can drift
# tomorrow without a code change to notice it.
# ---------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


PRICE_ACCESS_SIGN_PATH = "/api/fut/price-access/sign/"
PRICE_ACCESS_POW_MAX_NONCE = _env_int("FUTGG_PRICE_ACCESS_POW_MAX_NONCE", 5_000_000)
PRICE_ACCESS_POW_WORKER_TIMEOUT_MS = _env_int("FUTGG_PRICE_ACCESS_POW_TIMEOUT_MS", 110_000)
PRICE_ACCESS_SIGN_CACHE_MAX_TTL_MS = _env_int("FUTGG_PRICE_ACCESS_CACHE_MAX_TTL_MS", 75_000)
PRICE_ACCESS_SIGN_CACHE_SAFETY_MS = _env_int("FUTGG_PRICE_ACCESS_CACHE_SAFETY_MS", 45_000)

DEFAULT_GAME_YEAR = 27
DEFAULT_PLATFORM = "ps5"

SUPPORTED_CHALLENGE_ALGORITHMS = ("sha256-prefix-v1",)

# Query params stripped when canonicalising a URL for cache-key/logging
# purposes - never persisted, never logged. platform=ps5 is dropped too
# because it is the default the frontend omits from its own cache keys.
_STRIPPED_CANONICAL_PARAMS = ("verify", "priceKey")


class PriceAccessError(Exception):
    """Base class for every error this module raises."""


class SigningError(PriceAccessError):
    """The sign endpoint failed for a reason unrelated to a bot
    challenge or a rate limit (non-2xx, malformed body, etc)."""


class UnsolvableChallengeError(PriceAccessError):
    """challengeRequired was true but there was no structured
    data.challenge (an unstructured Cloudflare/browser challenge), or
    its algorithm isn't one this module knows how to solve. This is the
    genuinely-unsolvable case: no computation gets through it, and the
    caller's existing fail/backoff/transport-switch behaviour is the
    correct response, not a retry loop."""

    def __init__(self, message: str, *, algorithm: Optional[str] = None):
        super().__init__(message)
        self.algorithm = algorithm


class ProofOfWorkExhaustedError(PriceAccessError):
    """A structured sha256-prefix-v1 challenge was attempted, but no
    nonce below PRICE_ACCESS_POW_MAX_NONCE (or within the worker
    timeout) produced a digest meeting the required difficulty."""

    def __init__(self, message: str, *, attempts: Optional[int] = None):
        super().__init__(message)
        self.attempts = attempts


class RateLimitedError(PriceAccessError):
    """FUT.GG returned 429 with data.denied that was not, or was no
    longer, recoverable by the one permitted uncached retry. Carries
    retry_after (seconds) when the response supplied one. Callers must
    honour this and back off - never retry in a loop."""

    def __init__(self, message: str, *, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ExpiredSignatureError(PriceAccessError):
    """The signed URL was rejected (403) when fetched - it expired, or
    was already invalidated, between signing and use."""


class InvalidResponseSchemaError(PriceAccessError):
    """The data endpoint returned 200 but the body didn't match the
    expected {"data": {...}} shape - never silently coerced into one."""


class InvalidEaIdError(PriceAccessError):
    """ea_id could not be parsed as a positive integer."""


@dataclass(frozen=True)
class PlayerPriceDetail:
    """Parsed detailed price-endpoint response for one card.

    `raw` keeps the complete, unmodified `data` object so a caller that
    needs a field this dataclass doesn't name explicitly still has it,
    without this module needing to enumerate FUT.GG's entire schema up
    front - see Part 3's audit for which of these fields this codebase
    actually persists today versus discards.
    """

    ea_id: int
    game_year: int
    platform: str
    current_price: Optional[int]
    completed_auctions: list
    live_auctions: list
    history: Any
    price_range: Any
    overview: Any
    momentum: Any
    raw: dict
    fetched_at: datetime
    challenge_solved: bool
    challenge_attempts: Optional[int]
    signed_url_expires_in: Optional[float]

    @classmethod
    def from_response(
        cls,
        ea_id: int,
        game_year: int,
        platform: str,
        data: dict,
        *,
        challenge_solved: bool,
        challenge_attempts: Optional[int],
        signed_url_expires_in: Optional[float],
    ) -> "PlayerPriceDetail":
        return cls(
            ea_id=ea_id,
            game_year=game_year,
            platform=platform,
            current_price=data.get("currentPrice"),
            completed_auctions=list(data.get("completedAuctions") or []),
            live_auctions=list(data.get("liveAuctions") or []),
            history=data.get("history"),
            price_range=data.get("priceRange"),
            overview=data.get("overview"),
            momentum=data.get("momentum"),
            raw=data,
            fetched_at=datetime.now(timezone.utc),
            challenge_solved=challenge_solved,
            challenge_attempts=challenge_attempts,
            signed_url_expires_in=signed_url_expires_in,
        )


def canonical_endpoint_path(ea_id: int, game_year: int = DEFAULT_GAME_YEAR) -> str:
    """`/api/fut/player-prices/{game_year}/{ea_id}/` - the endpoint
    passed to the sign request, never a URL with a verify token."""
    return f"/api/fut/player-prices/{game_year}/{ea_id}/"


def canonical_cache_key(ea_id: int, game_year: int, platform: str) -> str:
    """Canonicalised identity for a (card, platform) pair, matching the
    frontend's own cache-key rule: platform=ps5 (the default) is
    omitted, and verify/priceKey never appear here in the first place
    since this is built from inputs, not a signed URL."""
    base = canonical_endpoint_path(ea_id, game_year)
    if platform and platform != DEFAULT_PLATFORM:
        return f"{base}?platform={platform}"
    return base


def redact_signed_url(url: Optional[str]) -> Optional[str]:
    """Redact verify/priceKey (and any other token-shaped query value)
    from a signed URL before it can reach a log line. Reuses the same
    redact_url() futgg_sales_sync.py's own diagnostics already rely on -
    one redaction implementation, not a second."""
    if not url:
        return url
    return redact_url(url)


class SignedUrlCache:
    """Optional in-memory cache mirroring the frontend's own signed-URL
    cache constants (PRICE_ACCESS_SIGN_CACHE_MAX_TTL_MS=75s,
    PRICE_ACCESS_SIGN_CACHE_SAFETY_MS=45s off a 120s expiresIn -> 75s
    usable window). NOT used by fetch_player_price_detail() by default:
    that function's contract is "always return fresh, complete data",
    which is what a manual diagnostic and a triggered detailed-sales
    refresh both actually want. This exists for a future caller that
    legitimately re-reads the same card within a short window (e.g. a
    batch that touches one card from two different trigger reasons in
    the same pass) and wants to avoid re-signing needlessly - opt-in,
    never silently applied.
    """

    def __init__(
        self,
        *,
        max_ttl_ms: int = PRICE_ACCESS_SIGN_CACHE_MAX_TTL_MS,
        safety_margin_ms: int = PRICE_ACCESS_SIGN_CACHE_SAFETY_MS,
    ) -> None:
        self._max_ttl_ms = max_ttl_ms
        self._safety_margin_ms = safety_margin_ms
        self._entries: dict[str, tuple[str, float, float]] = {}

    def _usable_ms(self, expires_in_seconds: Optional[float]) -> float:
        if expires_in_seconds is None:
            return float(self._max_ttl_ms)
        raw_ms = expires_in_seconds * 1000.0 - self._safety_margin_ms
        return max(0.0, min(raw_ms, float(self._max_ttl_ms)))

    def put(self, key: str, signed_url: str, expires_in_seconds: Optional[float], *, now_ms: float) -> None:
        usable = self._usable_ms(expires_in_seconds)
        self._entries[key] = (signed_url, now_ms, now_ms + usable)

    def get(self, key: str, *, now_ms: float) -> Optional[str]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        signed_url, _, valid_until_ms = entry
        if now_ms >= valid_until_ms:
            self._entries.pop(key, None)
            return None
        return signed_url

    def clear(self) -> None:
        self._entries.clear()


# =============================================================================
# The JS core - the ONE implementation of sign -> (solve challenge if
# structured PoW) -> resubmit -> fetch, shared by futgg_sales_sync.py's
# batch script and this module's single-card script.
# =============================================================================
#
# Placeholders (__MAX_NONCE__ / __TIMEOUT_MS__) are substituted at
# import time rather than via str.format()/f-string, because this
# source is dense with JS object/template-literal braces that would
# otherwise have to be escaped throughout - substituting two uniquely-
# named tokens is far less error-prone than doubling every `{`/`}`.
_CORE_JS_TEMPLATE = r"""
async function priceAccessSignAndFetch(eaId, gameYear, platform, diagnostic, revealSignedUrl) {
    // revealSignedUrl: the ONE deliberate exception to "never return the
    // signed URL" - opt-in only, used solely by the CLI probe's
    // --show-signed-url debug flag (see sign_player_price_url() in
    // futgg_price_access.py), never by fetch_player_price_detail()'s
    // normal path or by futgg_sales_sync.py's batch script, both of
    // which always pass this as falsy/omitted.
    const diag = {};
    const path = `/api/fut/player-prices/${gameYear}/${eaId}/`;
    const target = `${path}?platform=${platform}`;

    async function signOnce(proof) {
        const res = await fetch('/api/fut/price-access/sign/', {
            method: 'POST',
            headers: { 'content-type': 'application/json', 'accept': 'application/json' },
            body: JSON.stringify({ url: target, proof: proof || null }),
            credentials: 'same-origin',
        });
        let body = null;
        try { body = await res.json(); } catch (e) { /* non-JSON body - status alone still usable */ }
        return { status: res.status, body };
    }

    function leadingZeroBits(bytes) {
        let bits = 0;
        for (let i = 0; i < bytes.length; i++) {
            const b = bytes[i];
            if (b === 0) { bits += 8; continue; }
            for (let bit = 7; bit >= 0; bit--) {
                if (((b >> bit) & 1) === 0) { bits++; } else { return bits; }
            }
        }
        return bits;
    }

    async function sha256Bytes(text) {
        const data = new TextEncoder().encode(text);
        const digest = await crypto.subtle.digest('SHA-256', data);
        return new Uint8Array(digest);
    }

    async function solveChallenge(challenge) {
        if (!challenge || challenge.algorithm !== 'sha256-prefix-v1') {
            return { error: 'unknown_challenge_algorithm', algorithm: challenge && challenge.algorithm };
        }
        const difficulty = Number(challenge.difficulty);
        const maxNonce = __MAX_NONCE__;
        const deadline = Date.now() + __TIMEOUT_MS__;
        for (let nonce = 0; nonce < maxNonce; nonce++) {
            // Date.now() itself has overhead - only checked every 4096
            // attempts, matching the spirit of the frontend's own
            // worker-timeout guard without paying a syscall per nonce.
            if ((nonce & 0xFFF) === 0 && Date.now() > deadline) {
                return { error: 'pow_timeout', attempts: nonce };
            }
            const bytes = await sha256Bytes(`${challenge.subject}:${nonce}`);
            if (leadingZeroBits(bytes) >= difficulty) {
                return {
                    proof: {
                        difficulty: challenge.difficulty,
                        issuedAt: challenge.issuedAt,
                        nonce: String(nonce),
                        salt: challenge.salt,
                        token: challenge.token,
                    },
                    attempts: nonce + 1,
                };
            }
        }
        return { error: 'pow_exhausted', attempts: maxNonce };
    }

    function signedUrlShapeDiag(signedUrl) {
        const out = {};
        out.signedUrlIsAbsolute = /^https?:\/\//i.test(signedUrl);
        out.signedUrlHasVerifyParam = signedUrl.indexOf('verify=') !== -1;
        try {
            const parsed = new URL(signedUrl, location.href);
            out.signedUrlOrigin = parsed.origin;
            out.signedUrlPath = parsed.pathname;
            out.sameOriginAsPage = parsed.origin === location.origin;
        } catch (e) {
            out.signedUrlOrigin = null;
            out.signedUrlPath = null;
            out.sameOriginAsPage = null;
        }
        return out;
    }

    let signResult;
    try {
        signResult = await signOnce(null);
    } catch (e) {
        return { eaId, error: 'exception:' + String(e && e.message ? e.message : e), ...diag };
    }
    diag.signStatus = signResult.status;

    let signedUrl = null;
    let expiresIn = null;
    let challengeSolved = false;
    let challengeAttempts = null;

    const signedOk = (r) => r.status >= 200 && r.status < 300 && r.body && r.body.data && r.body.data.url;

    if (signedOk(signResult)) {
        signedUrl = signResult.body.data.url;
        expiresIn = signResult.body.data.expiresIn != null ? signResult.body.data.expiresIn : null;
    } else if (signResult.status === 403 && signResult.body && signResult.body.data && signResult.body.data.challengeRequired) {
        const challenge = signResult.body.data.challenge;
        diag.challengeRequired = true;
        if (!challenge) {
            return { eaId, error: 'unstructured_challenge', ...diag };
        }
        diag.challengeAlgorithm = challenge.algorithm;
        const solved = await solveChallenge(challenge);
        if (solved.error) {
            return { eaId, error: solved.error, challengeAttempts: solved.attempts != null ? solved.attempts : null, ...diag };
        }
        challengeSolved = true;
        challengeAttempts = solved.attempts;
        let retryResult;
        try {
            retryResult = await signOnce(solved.proof);
        } catch (e) {
            return { eaId, error: 'exception:' + String(e && e.message ? e.message : e), challengeAttempts, ...diag };
        }
        diag.signRetryStatus = retryResult.status;
        if (signedOk(retryResult)) {
            signedUrl = retryResult.body.data.url;
            expiresIn = retryResult.body.data.expiresIn != null ? retryResult.body.data.expiresIn : null;
        } else {
            return { eaId, error: 'sign_after_pow_' + retryResult.status, challengeAttempts, ...diag };
        }
    } else if (signResult.status === 429 && signResult.body && signResult.body.data && signResult.body.data.denied === true) {
        // Exactly one uncached retry, per the frontend's own handling of
        // 429 + data.denied. Never looped.
        let retryResult;
        try {
            retryResult = await signOnce(null);
        } catch (e) {
            return { eaId, error: 'exception:' + String(e && e.message ? e.message : e), ...diag };
        }
        diag.deniedRetryStatus = retryResult.status;
        if (signedOk(retryResult)) {
            signedUrl = retryResult.body.data.url;
            expiresIn = retryResult.body.data.expiresIn != null ? retryResult.body.data.expiresIn : null;
        } else if (retryResult.status === 429) {
            // Still 429 after the one permitted retry - do not loop
            // further. Named distinctly from a plain sign_429 for
            // observability, but keeps "429" in the string so it still
            // reads as throttle-shaped to is_throttle_signal() below.
            return { eaId, error: 'sign_429_denied_retry_exhausted', ...diag };
        } else {
            return { eaId, error: 'sign_' + retryResult.status, ...diag };
        }
    } else if (signResult.status === 429) {
        return { eaId, error: 'sign_429', ...diag };
    } else {
        return { eaId, error: 'sign_' + signResult.status, ...diag };
    }

    if (!signedUrl) {
        return { eaId, error: 'no_signed_url', challengeSolved, challengeAttempts, ...diag };
    }
    diag.expiresIn = expiresIn;
    Object.assign(diag, signedUrlShapeDiag(signedUrl));

    if (revealSignedUrl) {
        // The one deliberate, opt-in exception - see the parameter
        // comment above. Stops here: never fetches, never solves
        // anything further, just hands back what signing produced.
        return { eaId, signedUrl, expiresIn, challengeSolved, challengeAttempts, ...diag };
    }

    let dataRes;
    try {
        dataRes = await fetch(signedUrl, { headers: { 'accept': 'application/json' }, credentials: 'same-origin' });
    } catch (e) {
        return { eaId, error: 'exception:' + String(e && e.message ? e.message : e), challengeSolved, challengeAttempts, ...diag };
    }
    diag.dataStatus = dataRes.status;
    diag.dataContentType = dataRes.headers.get('content-type') || '';

    if (dataRes.status === 429) {
        // Kept as data_429 (not a semantic name) so it keeps matching
        // is_throttle_signal()'s existing "429" substring check exactly
        // as the pre-proof-of-work script's data_429 already did.
        const retryAfterHeader = dataRes.headers.get('retry-after');
        return { eaId, error: 'data_429', retryAfter: retryAfterHeader, challengeSolved, challengeAttempts, ...diag };
    }
    if (dataRes.status === 403) {
        // Kept as data_403 (not "expired_or_forbidden") for the same
        // reason - this is exactly the case the pre-proof-of-work script
        // already called data_403, and both is_throttle_signal() and
        // is_blocking_signal() key off that literal "403" substring.
        return { eaId, error: 'data_403', challengeSolved, challengeAttempts, ...diag };
    }
    if (!dataRes.ok) {
        if (diagnostic) {
            try { diag.dataBodyPreview = (await dataRes.text()).slice(0, 500); } catch (e) { /* body already consumed/unreadable */ }
        }
        return { eaId, error: 'data_' + dataRes.status, challengeSolved, challengeAttempts, ...diag };
    }

    let payload;
    try {
        payload = await dataRes.json();
    } catch (e) {
        if (diagnostic) {
            try { diag.dataBodyPreview = (await dataRes.clone().text()).slice(0, 500); } catch (e2) { /* clone unreadable */ }
        }
        return { eaId, error: 'malformed_json', challengeSolved, challengeAttempts, ...diag };
    }
    if (!payload || typeof payload !== 'object' || !('data' in payload)) {
        if (diagnostic) diag.dataBodyPreview = JSON.stringify(payload).slice(0, 500);
        return { eaId, error: 'malformed_payload', challengeSolved, challengeAttempts, ...diag };
    }

    return {
        eaId,
        data: payload.data,
        challengeSolved,
        challengeAttempts,
        ...diag,
    };
}
"""

PRICE_ACCESS_CORE_JS = (
    _CORE_JS_TEMPLATE
    .replace("__MAX_NONCE__", str(PRICE_ACCESS_POW_MAX_NONCE))
    .replace("__TIMEOUT_MS__", str(PRICE_ACCESS_POW_WORKER_TIMEOUT_MS))
)


# Single-card script: evaluated with {eaId, gameYear, platform, diagnostic}.
# Returns the raw JS result object (see priceAccessSignAndFetch above) -
# fetch_player_price_detail() below is what turns that into a
# PlayerPriceDetail or raises a typed exception.
#
# Page.evaluate() requires the passed source to be a SINGLE top-level
# expression (one function), not a function declaration followed by a
# separate statement - the same reason futgg_sales_sync.py's original
# _FETCH_SCRIPT always defined its per-card helper (`const one = async
# (eaId) => {...}`) INSIDE the outer arrow function's body rather than
# beside it. PRICE_ACCESS_CORE_JS is interpolated into the body here for
# the same reason: one canonical implementation, nested wherever a
# caller needs it wrapped.
DETAIL_FETCH_SCRIPT = f"""
async ({{ eaId, gameYear, platform, diagnostic }}) => {{
{PRICE_ACCESS_CORE_JS}
    return await priceAccessSignAndFetch(eaId, gameYear, platform, diagnostic);
}}
"""

# Debug-only variant: stops after signing (never fetches the data
# endpoint) and reveals the signed URL - the single, explicit,
# opt-in exception to this module's "never return the signed URL" rule.
# Used only by sign_player_price_url() below, itself used only by the
# CLI probe's --show-signed-url flag. Never used by the production
# collector or by fetch_player_price_detail()'s normal path.
REVEAL_SIGNED_URL_SCRIPT = f"""
async ({{ eaId, gameYear, platform, diagnostic }}) => {{
{PRICE_ACCESS_CORE_JS}
    return await priceAccessSignAndFetch(eaId, gameYear, platform, diagnostic, true);
}}
"""


def build_batch_script() -> str:
    """The batch variant futgg_sales_sync.py's _FETCH_SCRIPT is built
    from - same core, same result shape it has always returned
    (completedAuctions/currentPrice at the top level, not nested under
    `data`) so apply_batch_results() needed no changes. Kept as a
    function (not a second module-level constant) so it's unmistakably
    "the batch wrapper around the one core", not an independent script.
    """
    return f"""
async ({{ ids, gameYear, platform, diagnostic }}) => {{
{PRICE_ACCESS_CORE_JS}
    const results = await Promise.all(
        ids.map((eaId) => priceAccessSignAndFetch(eaId, gameYear, platform, diagnostic))
    );
    return results.map((result) => {{
        if (result.error) return result;
        const d = result.data || {{}};
        return {{
            eaId: result.eaId,
            completedAuctions: Array.isArray(d.completedAuctions) ? d.completedAuctions : [],
            currentPrice: d.currentPrice != null ? d.currentPrice : null,
            challengeSolved: result.challengeSolved,
            challengeAttempts: result.challengeAttempts,
            signStatus: result.signStatus,
            signRetryStatus: result.signRetryStatus,
            deniedRetryStatus: result.deniedRetryStatus,
            expiresIn: result.expiresIn,
            dataStatus: result.dataStatus,
            dataContentType: result.dataContentType,
            challengeRequired: result.challengeRequired,
            challengeAlgorithm: result.challengeAlgorithm,
            signedUrlIsAbsolute: result.signedUrlIsAbsolute,
            signedUrlOrigin: result.signedUrlOrigin,
            signedUrlPath: result.signedUrlPath,
            sameOriginAsPage: result.sameOriginAsPage,
            signedUrlHasVerifyParam: result.signedUrlHasVerifyParam,
            dataBodyPreview: result.dataBodyPreview,
        }};
    }});
}}
"""


def _raise_for_error(ea_id: int, result: dict) -> None:
    """Translate one JS result's `error` field into the matching typed
    exception. Never includes the signed URL - only the already-safe
    diagnostic fields the JS itself computed."""
    error = result.get("error")
    if error == "unstructured_challenge":
        raise UnsolvableChallengeError(
            f"ea_id={ea_id}: FUT.GG returned an unstructured bot challenge "
            "(challengeRequired with no data.challenge) - this is Cloudflare "
            "deciding the browser looks like a bot, not something solvable "
            "by computing a proof.",
        )
    if error == "unknown_challenge_algorithm":
        raise UnsolvableChallengeError(
            f"ea_id={ea_id}: FUT.GG returned a challenge with an unrecognised "
            f"algorithm ({result.get('challengeAlgorithm')!r}) - only "
            f"{SUPPORTED_CHALLENGE_ALGORITHMS} are implemented.",
            algorithm=result.get("challengeAlgorithm"),
        )
    if error in ("pow_exhausted", "pow_timeout"):
        raise ProofOfWorkExhaustedError(
            f"ea_id={ea_id}: proof-of-work not solved "
            f"({'exhausted ' + str(PRICE_ACCESS_POW_MAX_NONCE) + ' nonces' if error == 'pow_exhausted' else 'exceeded worker timeout'}) "
            f"after {result.get('challengeAttempts')} attempts.",
            attempts=result.get("challengeAttempts"),
        )
    if error == "sign_after_pow_403":
        raise UnsolvableChallengeError(
            f"ea_id={ea_id}: proof was solved and resubmitted, but FUT.GG "
            "rejected it (403) - the challenge likely expired between "
            "solving and resubmission, or the proof was rejected outright.",
        )
    if error in ("sign_429", "sign_429_denied_retry_exhausted", "data_429"):
        # Deliberately named to keep the "429" substring
        # is_throttle_signal()/is_blocking_signal() in futgg_sales_sync.py
        # already key off - see that module's own _THROTTLE_ERROR_MARKERS/
        # _BLOCKING_ERROR_MARKERS comments for why this naming is load-
        # bearing, not cosmetic.
        retry_after_raw = result.get("retryAfter")
        try:
            retry_after = float(retry_after_raw) if retry_after_raw is not None else None
        except (TypeError, ValueError):
            retry_after = None
        raise RateLimitedError(
            f"ea_id={ea_id}: FUT.GG returned 429" + (f" (Retry-After: {retry_after}s)" if retry_after else ""),
            retry_after=retry_after,
        )
    if error == "data_403":
        # Same rejected-signed-URL case the pre-proof-of-work script
        # already called data_403 - named to match for the same
        # substring-marker reason as the 429 cases above.
        raise ExpiredSignatureError(
            f"ea_id={ea_id}: signed URL was rejected (403) when fetched - "
            "it expired or was invalidated between signing and use.",
        )
    if error and error.startswith("data_"):
        raise InvalidResponseSchemaError(
            f"ea_id={ea_id}: price-detail endpoint returned {error[5:]}.",
        )
    if error in ("malformed_json", "malformed_payload", "no_signed_url"):
        raise InvalidResponseSchemaError(
            f"ea_id={ea_id}: unexpected response shape ({error}) from the "
            "sign or data endpoint.",
        )
    if error and error.startswith("sign_"):
        raise SigningError(f"ea_id={ea_id}: sign request failed ({error}).")
    if error and error.startswith("exception:"):
        raise PriceAccessError(f"ea_id={ea_id}: {error}")
    raise PriceAccessError(f"ea_id={ea_id}: unrecognised failure ({error!r}).")


@asynccontextmanager
async def _open_diagnostic_page(anchor_url: str, *, headless: bool, user_agent: str, timeout_ms: int):
    """Launches a one-shot browser + context + page for a standalone
    call (no page supplied). Resources remain alive until the caller leaves this async context."""
    from playwright.async_api import async_playwright

    async with Stealth().use_async(async_playwright()) as patched_playwright:
        browser = await patched_playwright.chromium.launch(headless=headless, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale='en-GB',
        )
        page = await context.new_page()
        await page.goto(anchor_url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            yield page
        finally:
            await browser.close()

# A real, stable fut.gg player page - only used as a same-origin anchor
# when the caller doesn't supply their own open page. Matches
# futgg_sales_sync.py's own run_diagnostic_card() fallback anchor.
DEFAULT_ANCHOR_URL = "https://www.fut.gg/players/"


async def fetch_player_price_detail(
    ea_id: int,
    game_year: int = DEFAULT_GAME_YEAR,
    platform: str = DEFAULT_PLATFORM,
    *,
    page=None,
    anchor_url: str = DEFAULT_ANCHOR_URL,
    headless: bool = True,
    user_agent: Optional[str] = None,
    timeout_ms: int = 45_000,
    diagnostic: bool = False,
) -> PlayerPriceDetail:
    """Fetch one card's complete detailed price-access response.

    Signature matches the brief: `ea_id`, `game_year`, `platform` are
    positional/keyword with sensible defaults, so a simple call site
    works with no browser plumbing at all. `page` is the one addition -
    when a caller already has an open Playwright Page (the production
    collector, mid-batch; a future triggered-refresh worker processing
    several cards) it is reused directly, matching "reuse the existing
    session/transport conventions where appropriate"; when omitted, this
    function opens and cleanly closes its own one-shot headed browser.

    Raises a typed PriceAccessError subclass on any failure - never
    returns a partial/None result. Never retries on its own beyond the
    single frontend-documented 429-denied retry and the in-browser PoW
    attempt loop; a 429 always surfaces as RateLimitedError for the
    caller to honour (see the CLI's --retry-after handling), not
    something this function loops on itself.
    """
    if not isinstance(ea_id, int) or isinstance(ea_id, bool) or ea_id <= 0:
        raise InvalidEaIdError(f"ea_id must be a positive integer, got {ea_id!r}")

    page_scope = nullcontext(page) if page is not None else _open_diagnostic_page(
        anchor_url,
        headless=headless,
        user_agent=user_agent or DEFAULT_USER_AGENT,
        timeout_ms=timeout_ms,
    )
    async with page_scope as page:
        result = await page.evaluate(
            DETAIL_FETCH_SCRIPT,
            {"eaId": ea_id, "gameYear": game_year, "platform": platform, "diagnostic": diagnostic},
        )

    if result.get("error"):
        log.warning(
            "price-detail fetch failed ea_id=%s error=%s signStatus=%s dataStatus=%s "
            "challengeRequired=%s challengeAlgorithm=%s challengeSolved=%s challengeAttempts=%s",
            ea_id, result.get("error"), result.get("signStatus"), result.get("dataStatus"),
            result.get("challengeRequired"), result.get("challengeAlgorithm"),
            result.get("challengeSolved"), result.get("challengeAttempts"),
        )
        _raise_for_error(ea_id, result)

    data = result.get("data")
    if not isinstance(data, dict):
        raise InvalidResponseSchemaError(f"ea_id={ea_id}: response had no usable `data` object.")

    return PlayerPriceDetail.from_response(
        ea_id, game_year, platform, data,
        challenge_solved=bool(result.get("challengeSolved")),
        challenge_attempts=result.get("challengeAttempts"),
        signed_url_expires_in=result.get("expiresIn"),
    )


async def sign_player_price_url(
    ea_id: int,
    game_year: int = DEFAULT_GAME_YEAR,
    platform: str = DEFAULT_PLATFORM,
    *,
    page=None,
    anchor_url: str = DEFAULT_ANCHOR_URL,
    headless: bool = True,
    user_agent: Optional[str] = None,
    timeout_ms: int = 45_000,
) -> tuple[str, Optional[float]]:
    """Debug-only: sign a card's price-access URL (solving a structured
    proof-of-work challenge if one is returned) and hand back the raw
    signed URL and its advertised expiresIn, WITHOUT fetching the data
    endpoint. This is the one function in this module that returns an
    actual signed URL to its caller - it exists solely for the CLI
    probe's --show-signed-url flag, which has an explicit, informed-
    consent reason to display it. Callers must not log the returned URL
    at anything other than a level the operator explicitly asked for,
    and must never persist it - it is valid for a very short window
    (see PRICE_ACCESS_SIGN_CACHE_MAX_TTL_MS) and is worthless afterward
    regardless.
    """
    if not isinstance(ea_id, int) or isinstance(ea_id, bool) or ea_id <= 0:
        raise InvalidEaIdError(f"ea_id must be a positive integer, got {ea_id!r}")

    page_scope = nullcontext(page) if page is not None else _open_diagnostic_page(
        anchor_url,
        headless=headless,
        user_agent=user_agent or DEFAULT_USER_AGENT,
        timeout_ms=timeout_ms,
    )
    async with page_scope as page:
        result = await page.evaluate(
            REVEAL_SIGNED_URL_SCRIPT,
            {"eaId": ea_id, "gameYear": game_year, "platform": platform, "diagnostic": False},
        )

    if result.get("error"):
        log.warning(
            "sign-only request failed ea_id=%s error=%s signStatus=%s",
            ea_id, result.get("error"), result.get("signStatus"),
        )
        _raise_for_error(ea_id, result)

    signed_url = result.get("signedUrl")
    if not signed_url:
        raise InvalidResponseSchemaError(f"ea_id={ea_id}: signing succeeded but no signedUrl was returned.")
    return signed_url, result.get("expiresIn")


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)

