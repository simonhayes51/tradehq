from __future__ import annotations

"""
Short redirect endpoints for /feed's "Continue to payment" and "Manage
billing" link buttons.

Discord message-component (link button) URLs have a hard 512-character
limit. Stripe's own Checkout Session and Billing Portal Session URLs are
long, single-use, Stripe-signed URLs that routinely exceed it - posting
one directly as a button URL fails Discord's own validation
(BASE_TYPE_MAX_LENGTH) and the interaction's follow-up reply is rejected,
leaving the command stuck on "thinking..." forever (this is exactly what
happened - see the discord_interactions follow-up PATCH 400 in the logs).

Instead, the button links to one of these short, our-domain URLs,
carrying a signed, short-lived token (itsdangerous, keyed off the app's
own SECRET_KEY - the same signing mechanism already used for this app's
session cookies) that names only the guild_id and which kind of session
to create. Never a raw database id, Stripe id, or secret - and never the
long Stripe URL itself until the one Discord user who could see the
ephemeral message actually clicks through. The real Stripe session is
created (or re-fetched) fresh at redirect time and the browser is
302'd straight to it.
"""

import asyncio
import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.services import discord_feed_billing as feed_billing

router = APIRouter(prefix="/api/discord/feed", tags=["discord"])

_SALT_CHECKOUT = "discord-feed-checkout"
_SALT_BILLING = "discord-feed-billing"
# The Stripe Checkout Session itself is also short-lived (defaults to 24h,
# but a payment link should be acted on promptly); a fresh Billing Portal
# session is created at click time regardless, so its token can safely
# outlive a single sitting.
_CHECKOUT_MAX_AGE_SECONDS = 900
_BILLING_MAX_AGE_SECONDS = 86400


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(os.environ["SECRET_KEY"])


def api_base_url() -> str:
    return (os.getenv("DISCORD_FEED_API_BASE_URL") or os.getenv("VITE_API_URL") or "https://api.eafc.live").rstrip("/")


def make_redirect_url(guild_id: str, kind: Literal["checkout", "billing"]) -> str:
    salt = _SALT_CHECKOUT if kind == "checkout" else _SALT_BILLING
    token = _serializer().dumps({"g": guild_id}, salt=salt)
    return f"{api_base_url()}/api/discord/feed/{kind}/{token}"


def _verify(token: str, kind: Literal["checkout", "billing"]) -> str:
    salt = _SALT_CHECKOUT if kind == "checkout" else _SALT_BILLING
    max_age = _CHECKOUT_MAX_AGE_SECONDS if kind == "checkout" else _BILLING_MAX_AGE_SECONDS
    try:
        data = _serializer().loads(token, salt=salt, max_age=max_age)
    except SignatureExpired:
        raise HTTPException(status_code=410, detail="This link has expired - run the /feed command again to get a fresh one.")
    except BadSignature:
        raise HTTPException(status_code=400, detail="Invalid link.")
    return data["g"]


@router.get("/checkout/{token}")
async def checkout_redirect(token: str) -> RedirectResponse:
    guild_id = _verify(token, "checkout")
    feed = await feed_billing.get_guild_feed(guild_id)
    if not feed or not feed.get("stripe_checkout_session_id"):
        raise HTTPException(status_code=404, detail="No pending checkout for this server - run /feed setup again.")
    import stripe

    session = await asyncio.to_thread(stripe.checkout.Session.retrieve, feed["stripe_checkout_session_id"])
    if session.status == "expired":
        raise HTTPException(status_code=410, detail="This checkout session expired - run /feed setup again.")
    return RedirectResponse(session.url, status_code=302)


@router.get("/billing/{token}")
async def billing_redirect(token: str) -> RedirectResponse:
    guild_id = _verify(token, "billing")
    app_url = os.getenv("FRONTEND_URL", "https://app.eafc.live").rstrip("/")
    return_url = f"{app_url}/#/discord/feed/billing?guild_id={guild_id}"
    portal_url = await feed_billing.create_billing_portal_session(guild_id=guild_id, return_url=return_url)
    return RedirectResponse(portal_url, status_code=302)
