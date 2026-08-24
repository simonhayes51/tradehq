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
import logging
import os
from typing import Literal, Union

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.services import discord_feed_billing as feed_billing

log = logging.getLogger("discord_feed_redirect")

router = APIRouter(prefix="/api/discord/feed", tags=["discord"])


def _static_page(title: str, message: str) -> HTMLResponse:
    """Minimal, dependency-free landing page for Stripe's post-checkout/
    post-portal redirects. Served directly by the backend rather than a
    frontend SPA route, so it can never 404 regardless of the frontend's
    own routing/deploy state - the only thing these pages need to do is
    tell the user to go back to Discord."""
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title} — EAFC.Live</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ background:#0b0f14; color:#e5e7eb; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; padding:24px; text-align:center; }}
.card {{ max-width:420px; }}
h1 {{ color:#22c55e; font-size:1.5rem; margin-bottom:0.5rem; }}
p {{ color:#9ca3af; line-height:1.5; }}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p></div></body></html>""")

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


@router.get("/checkout/{token}", response_model=None)
async def checkout_redirect(token: str) -> Union[RedirectResponse, HTMLResponse]:
    import stripe

    guild_id = _verify(token, "checkout")
    feed = await feed_billing.get_guild_feed(guild_id)
    if not feed or not feed.get("stripe_checkout_session_id"):
        return _static_page("Nothing to pay for", "No pending checkout for this server - run <code>/feed setup</code> again in Discord.")
    try:
        session = await asyncio.to_thread(stripe.checkout.Session.retrieve, feed["stripe_checkout_session_id"])
    except stripe.error.StripeError:
        log.warning("Stripe error retrieving checkout session for guild=%s", guild_id, exc_info=True)
        return _static_page("Something went wrong", "Couldn't reach Stripe just now - please try the /feed setup link again shortly.")
    if session.status == "expired":
        return _static_page("Link expired", "This checkout session expired - run <code>/feed setup</code> again in Discord for a fresh one.")
    return RedirectResponse(session.url, status_code=302)


@router.get("/billing/{token}", response_model=None)
async def billing_redirect(token: str) -> Union[RedirectResponse, HTMLResponse]:
    import stripe

    guild_id = _verify(token, "billing")
    return_url = f"{api_base_url()}/api/discord/feed/billing-return"
    try:
        portal_url = await feed_billing.create_billing_portal_session(guild_id=guild_id, return_url=return_url)
    except feed_billing.FeedError as exc:
        return _static_page("Nothing to manage yet", str(exc))
    except stripe.error.StripeError:
        log.warning("Stripe error creating billing portal session for guild=%s", guild_id, exc_info=True)
        return _static_page(
            "Something went wrong",
            "Couldn't open the billing portal just now. If this keeps happening, the Stripe Customer Portal "
            "may not be activated yet for this account (Stripe Dashboard → Settings → Billing → "
            "Customer portal) - contact EAFC.Live support otherwise.",
        )
    return RedirectResponse(portal_url, status_code=302)


# ---------------------------------------------------------------------------
# Static landing pages for Stripe's post-checkout / post-portal redirects.
# Served by the backend directly (no frontend SPA route involved) so they
# can never 404 - see _static_page()'s docstring.
# ---------------------------------------------------------------------------


@router.get("/success")
async def checkout_success_page() -> HTMLResponse:
    return _static_page(
        "Payment successful",
        "Your Server Feed subscription is active. Go back to Discord and run "
        "<code>/feed test</code> to send a synthetic post and confirm everything works, "
        "then <code>/feed activate</code> to go live.",
    )


@router.get("/cancel")
async def checkout_cancel_page() -> HTMLResponse:
    return _static_page(
        "Checkout canceled",
        "No payment was taken. You can run <code>/feed setup</code> in Discord again any time.",
    )


@router.get("/billing-return")
async def billing_return_page() -> HTMLResponse:
    return _static_page(
        "Billing updated",
        "Your changes have been saved with Stripe. Go back to Discord and run "
        "<code>/feed status</code> to confirm.",
    )
