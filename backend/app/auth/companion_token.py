# app/auth/companion_token.py
#
# A short-lived, read-only credential for the browser extension and the
# userscript.
#
# WHY A TOKEN AT ALL, GIVEN THE SESSION COOKIE
#
# The companion runs inside the EA Web App page on www.ea.com and calls
# api.eafc.live. That is a cross-site request, so it depends on the
# session cookie being sent third-party:
#
#   * The Chrome extension fetches from its MV3 service worker with
#     `credentials: "include"` and `https://api.eafc.live/*` in
#     host_permissions. That is the strongest position of the two, but it
#     rests on browser behaviour that has changed repeatedly and is
#     changing again as third-party cookies are phased out.
#
#   * The userscript uses GM_xmlhttpRequest with `withCredentials: true`.
#     Support for that flag is inconsistent BETWEEN USERSCRIPT MANAGERS -
#     this codebase has already been bitten once by exactly that class of
#     difference (see api-client.js on GM.xmlHttpRequest vs
#     GM_xmlhttpRequest, found live on iOS). There is no version of "test
#     it once and rely on it" that is honest here.
#
# So the cookie stays as the convenient path, and this exists as the
# reliable one: an explicit credential the member obtains from the app,
# where the cookie is first-party and unambiguous.
#
# WHAT THIS IS NOT
#
# Not a permanent API key sitting in userscript storage. The token is
# short-lived (COMPANION_TOKEN_TTL_HOURS, default 24) and read-only:
# scope "companion:read" grants exactly one thing - the same card payload
# the overlay already renders, serialised for whoever the token belongs
# to. It cannot write, cannot spend, and cannot reach anything else.
#
# It is deliberately NOT the existing extension token either. That one is
# scoped "trade:ingest", carries a 30-day TTL, and is a write credential;
# widening it to cover read traffic would make a long-lived write token
# the thing every companion user carries around.
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import jwt

log = logging.getLogger("companion_token")

SCOPE = "companion:read"
ISSUER = os.getenv("JWT_ISSUER", "eafc-live")
TTL_HOURS = int(os.getenv("COMPANION_TOKEN_TTL_HOURS", "24"))


def _signing_key() -> str:
    # The same key the extension token uses. Rotating it revokes every
    # outstanding companion token at once, which is the only revocation
    # mechanism a stateless token has and is worth being able to reach for.
    return os.getenv("JWT_PRIVATE_KEY", "dev-secret-change-me")


def issue(user_id: str, *, ttl_hours: Optional[int] = None) -> Dict[str, Any]:
    """Mint a companion token for one account."""
    ttl = TTL_HOURS if ttl_hours is None else ttl_hours
    now = int(time.time())
    expires = now + ttl * 3600
    payload = {
        "sub": str(user_id),
        "scope": SCOPE,
        "iat": now,
        "exp": expires,
        "iss": ISSUER,
    }
    return {
        "token": jwt.encode(payload, _signing_key(), algorithm="HS256"),
        "expiresAt": expires,
        "ttlHours": ttl,
        "scope": SCOPE,
    }


def verify(token: str) -> Optional[str]:
    """The user id this token belongs to, or None.

    Returns None for every failure rather than raising or distinguishing
    them: an expired token, a forged one and one signed with a rotated key
    are all "we cannot identify this caller", and telling a caller which
    of those it was helps only an attacker.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=["HS256"], issuer=ISSUER)
    except Exception:
        return None
    if payload.get("scope") != SCOPE:
        # A trade:ingest token must not work here. Scope is checked
        # explicitly rather than assumed from the signature: the signing
        # key is shared, so a valid signature says nothing about what the
        # token was issued FOR.
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None


def bearer_from(request) -> Optional[str]:
    """Pull the token out of an Authorization header, if there is one."""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None
