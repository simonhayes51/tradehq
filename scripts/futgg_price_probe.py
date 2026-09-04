#!/usr/bin/env python
"""Manual FUT.GG price-detail diagnostic - a one-shot CLI wrapper around
auto_sync/futgg_price_access.py's fetch_player_price_detail(), for
investigating one card by hand without touching the database or the
production sales-sync worker's own scheduling.

    python scripts/futgg_price_probe.py --ea-id 117636376
    python scripts/futgg_price_probe.py --url "https://www.fut.gg/api/fut/player-prices/26/117636376/"
    python scripts/futgg_price_probe.py --url "https://www.fut.gg/players/195864-paul-pogba/26-117636376/"

CANNOT BE RUN FROM A CLAUDE CODE REMOTE SANDBOX SESSION: that
environment's egress proxy explicitly denies fut.gg (confirmed: both
www.fut.gg and r2.fut.gg return a policy-denied 403 on CONNECT). Run
this from the production sales container, a local machine, or any
other environment with real network access to fut.gg - same class of
limitation auto_sync/COOLIFY.md and the Discord smoke-test runbook
already document for this repo's other manual diagnostics.

Requires no DATABASE_URL and touches no database - this is pure
network diagnostics against FUT.GG, reusing exactly the same signing/
proof-of-work implementation the production collector uses (see
futgg_price_access.py's own module docstring for why that has to run
inside a real headed browser rather than a plain HTTP client).

EXIT CODES
----------
  0  success
  2  invalid arguments / invalid ea_id / unparseable --url
  3  signing failed for a reason unrelated to a challenge or rate limit
  4  a bot challenge could not be solved (unstructured Cloudflare
     challenge, unrecognised algorithm, or proof-of-work exhausted)
  5  the signed URL was rejected when fetched (expired/invalidated)
  6  rate limited (429) - never retried in a loop; re-run later,
     honouring any Retry-After this prints
  7  unexpected response schema from FUT.GG
  1  any other unexpected error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, parse_qs

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "auto_sync"))

import futgg_price_access as price_access  # noqa: E402


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


# Same default as futgg_sales_sync.py's HEADLESS constant: safe/portable
# by default (headless=True), but the production sales container sets
# PLAYWRIGHT_HEADLESS=false (with Xvfb - see Dockerfile.sales) because
# that is the configuration proven to get past Cloudflare. Running this
# probe IN that same container picks up the same env var automatically.
HEADLESS = env_bool("PLAYWRIGHT_HEADLESS", True)
TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "45000"))

_PLAYER_PRICE_PATH_RE = re.compile(r"/api/fut/player-prices/(?P<game_year>\d+)/(?P<ea_id>\d+)/?")
_PLAYER_PAGE_PATH_RE = re.compile(r"/players/[^/]+/(?P<game_year>\d+)-(?P<ea_id>\d+)/?")

_EXIT_BY_EXCEPTION = (
    (price_access.RateLimitedError, 6),
    (price_access.ExpiredSignatureError, 5),
    (price_access.ProofOfWorkExhaustedError, 4),
    (price_access.UnsolvableChallengeError, 4),
    (price_access.InvalidResponseSchemaError, 7),
    (price_access.InvalidEaIdError, 2),
    (price_access.SigningError, 3),
    (price_access.PriceAccessError, 1),
)


def exit_code_for_exception(exc: "price_access.PriceAccessError") -> int:
    """Maps a raised PriceAccessError subclass to this CLI's documented
    exit code (see the module docstring's EXIT CODES section) - checked
    most-specific-first, since RateLimitedError/ExpiredSignatureError/
    etc. are themselves PriceAccessError subclasses and would otherwise
    all match the generic fallback."""
    for exc_type, code in _EXIT_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            return code
    return 1


class ProbeArgError(Exception):
    """--ea-id/--url could not be resolved to a usable (ea_id, game_year)."""


def parse_target(*, ea_id: Optional[int], url: Optional[str], default_game_year: int) -> tuple[int, int, Optional[str]]:
    """Returns (ea_id, game_year, verify_url_or_None). `verify_url_or_
    None` is the caller-supplied URL verbatim ONLY when it already
    carries a verify= token worth trying once - never persisted, never
    logged in full."""
    if ea_id is not None:
        if ea_id <= 0:
            raise ProbeArgError(f"--ea-id must be a positive integer, got {ea_id}")
        return ea_id, default_game_year, None

    if not url:
        raise ProbeArgError("one of --ea-id or --url is required")

    parsed = urlsplit(url)
    match = _PLAYER_PRICE_PATH_RE.search(parsed.path) or _PLAYER_PAGE_PATH_RE.search(parsed.path)
    if not match:
        raise ProbeArgError(
            f"could not extract an ea_id/game_year from --url {price_access.redact_url(url)!r} - "
            "expected either /api/fut/player-prices/<gameYear>/<eaId>/ or "
            "/players/<slug>/<gameYear>-<eaId>/"
        )
    extracted_ea_id = int(match.group("ea_id"))
    extracted_game_year = int(match.group("game_year"))

    has_verify = "verify" in parse_qs(parsed.query)
    return extracted_ea_id, extracted_game_year, (url if has_verify else None)


# Deliberately separate from futgg_price_access.py's public API - this
# is a ONE-SHOT, best-effort attempt at a URL the CALLER already had in
# hand (from a browser network tab, say), not a general signed-URL
# fetcher. It is tried exactly once; any failure (expired, 403, wrong
# shape, anything) falls straight through to the canonical sign+fetch
# flow with a FRESH signature - the stale one is discarded, never
# retried, never persisted anywhere.
_TRY_SUPPLIED_URL_SCRIPT = """
async (url) => {
    try {
        const res = await fetch(url, { headers: { accept: 'application/json' }, credentials: 'same-origin' });
        if (!res.ok) return { ok: false, status: res.status };
        let payload;
        try { payload = await res.json(); } catch (e) { return { ok: false, status: res.status, malformed: true }; }
        if (!payload || typeof payload !== 'object' || !('data' in payload)) {
            return { ok: false, status: res.status, malformed: true };
        }
        return { ok: true, status: res.status, data: payload.data };
    } catch (e) {
        return { ok: false, status: 0, error: String(e && e.message ? e.message : e) };
    }
}
"""


async def try_supplied_signed_url(page, url: str) -> Optional[dict]:
    try:
        result = await page.evaluate(_TRY_SUPPLIED_URL_SCRIPT, url)
    except Exception:
        return None
    if result.get("ok") and isinstance(result.get("data"), dict):
        return result["data"]
    return None


def format_summary(detail: "price_access.PlayerPriceDetail") -> str:
    completed = detail.completed_auctions
    lines = [
        f"EA ID: {detail.ea_id}",
        f"Platform: {detail.platform}",
        f"Current price: {detail.current_price:,}" if detail.current_price is not None else "Current price: (none reported)",
        f"Price updated: {detail.fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Completed auctions: {len(completed)}",
        f"Live auctions: {len(detail.live_auctions)}",
        f"History points: {len(detail.history) if isinstance(detail.history, list) else 'n/a'}",
    ]
    if completed:
        latest = completed[0]
        oldest = completed[-1]
        lines.append(f"Latest completed sale: {_describe_auction(latest)}")
        lines.append(f"Oldest completed sale: {_describe_auction(oldest)}")
    if detail.challenge_solved:
        lines.append(f"Proof-of-work solved: yes ({detail.challenge_attempts} attempt(s))")
    if detail.signed_url_expires_in is not None:
        lines.append(f"Signed request expires: {detail.signed_url_expires_in}s from signing")
    return "\n".join(lines)


def _describe_auction(auction: dict) -> str:
    price = auction.get("soldPrice") if isinstance(auction, dict) else None
    when = auction.get("soldDate") if isinstance(auction, dict) else None
    price_text = f"{price:,}" if isinstance(price, (int, float)) else "?"
    return f"{price_text} at {when or '?'}"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Manual FUT.GG player-price-detail diagnostic (see this file's module docstring).",
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--ea-id", type=int, help="EA card id, e.g. 117636376")
    target.add_argument("--url", type=str, help="A player-prices API URL or a fut.gg player page URL")
    p.add_argument("--platform", choices=["ps5", "pc"], default=price_access.DEFAULT_PLATFORM)
    p.add_argument("--game-year", type=int, default=price_access.DEFAULT_GAME_YEAR)
    p.add_argument("--pretty", action="store_true", help="Pretty-print the full JSON detail")
    p.add_argument("--summary", action="store_true", help="Print a concise human-readable summary instead of JSON")
    p.add_argument("--output", type=str, default=None, help="Write the full JSON detail to this file path")
    p.add_argument(
        "--show-signed-url", action="store_true",
        help="Also sign (without fetching) and print the raw signed URL - SENSITIVE, short-lived, off by default",
    )
    return p


def detail_to_jsonable(detail: "price_access.PlayerPriceDetail") -> dict[str, Any]:
    return {
        "eaId": detail.ea_id,
        "gameYear": detail.game_year,
        "platform": detail.platform,
        "currentPrice": detail.current_price,
        "completedAuctions": detail.completed_auctions,
        "liveAuctions": detail.live_auctions,
        "history": detail.history,
        "priceRange": detail.price_range,
        "overview": detail.overview,
        "momentum": detail.momentum,
        "fetchedAt": detail.fetched_at.isoformat(),
        "challengeSolved": detail.challenge_solved,
        "challengeAttempts": detail.challenge_attempts,
        "signedUrlExpiresIn": detail.signed_url_expires_in,
    }


async def _run(args: argparse.Namespace) -> int:
    try:
        ea_id, game_year, verify_url = parse_target(
            ea_id=args.ea_id, url=args.url, default_game_year=args.game_year,
        )
    except ProbeArgError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(user_agent=price_access.DEFAULT_USER_AGENT, locale="en-GB")
            page = await context.new_page()
            await page.goto(price_access.DEFAULT_ANCHOR_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)

            data = None
            if verify_url:
                # One best-effort attempt at the caller's own URL. Any
                # failure discards it silently and falls through below -
                # never retried, never logged, never persisted.
                data = await try_supplied_signed_url(page, verify_url)

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
                    code = exit_code_for_exception(exc)
                    print(f"error: {exc}", file=sys.stderr)
                    retry_after = getattr(exc, "retry_after", None)
                    if retry_after:
                        print(f"Retry-After: {retry_after}s", file=sys.stderr)
                    return code

            if args.show_signed_url:
                try:
                    signed_url, expires_in = await price_access.sign_player_price_url(
                        ea_id, game_year, args.platform, page=page,
                    )
                    print("\n*** SENSITIVE - short-lived signed URL (do not share, do not persist) ***")
                    print(signed_url)
                    if expires_in is not None:
                        print(f"(advertised expiresIn: {expires_in}s from just now)")
                    print("*** end sensitive output ***\n")
                except price_access.PriceAccessError as exc:
                    print(f"warning: could not obtain a signed URL to display: {exc}", file=sys.stderr)
        finally:
            await browser.close()

    payload = detail_to_jsonable(detail)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(payload, f, indent=2 if args.pretty else None, default=str)
        print(f"wrote {args.output}", file=sys.stderr)

    if args.summary:
        print(format_summary(detail))
    elif not args.output:
        print(json.dumps(payload, indent=2 if args.pretty else None, default=str))

    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    import asyncio

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
