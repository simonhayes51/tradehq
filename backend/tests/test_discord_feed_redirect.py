import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.routers.discord_feed_redirect as redirect
from app.services import discord_feed_billing as feed_billing


def run(coro):
    return asyncio.run(coro)


def _client(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    app = FastAPI()
    app.include_router(redirect.router)
    return TestClient(app)


def test_redirect_urls_are_well_under_the_512_char_discord_limit(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    checkout_url = redirect.make_redirect_url("123456789012345678", "checkout")
    billing_url = redirect.make_redirect_url("123456789012345678", "billing")
    assert len(checkout_url) <= 200  # generous margin under Discord's 512 cap
    assert len(billing_url) <= 200
    assert "123456789012345678" not in checkout_url  # guild id is inside the signed token, not the URL path


def test_checkout_and_billing_tokens_are_not_interchangeable(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    checkout_url = redirect.make_redirect_url("g1", "checkout")
    checkout_token = checkout_url.rsplit("/", 1)[1]
    with pytest.raises(Exception):
        redirect._verify(checkout_token, "billing")


def test_tampered_token_is_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    token = redirect.make_redirect_url("g1", "checkout").rsplit("/", 1)[1]
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(Exception):
        redirect._verify(tampered, "checkout")


def test_checkout_redirect_follows_through_to_the_real_stripe_url(monkeypatch):
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "checkout").rsplit("/", 1)[1]

    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value={"stripe_checkout_session_id": "cs_test_123"}))

    fake_session = MagicMock(status="open", url="https://checkout.stripe.com/c/pay/cs_test_123")
    with pytest.MonkeyPatch.context() as mp:
        import stripe
        mp.setattr(stripe.checkout.Session, "retrieve", MagicMock(return_value=fake_session))
        resp = client.get(f"/api/discord/feed/checkout/{token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://checkout.stripe.com/c/pay/cs_test_123"


def test_checkout_redirect_shows_a_friendly_page_with_no_pending_checkout(monkeypatch):
    """A raw 404 with no explanation is unhelpful here - this endpoint is
    only ever reached by a real user clicking a real button, so it must
    render an actionable message instead of crashing or showing a bare
    error code."""
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "checkout").rsplit("/", 1)[1]
    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value=None))
    resp = client.get(f"/api/discord/feed/checkout/{token}", follow_redirects=False)
    assert resp.status_code == 200
    assert "feed setup" in resp.text.lower()


def test_billing_redirect_shows_a_friendly_page_on_feed_error(monkeypatch):
    """Regression test: create_billing_portal_session raising FeedError
    (e.g. no stripe_customer_id yet) used to be uncaught here, crashing
    with a raw 500 Internal Server Error instead of an actionable page."""
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "billing").rsplit("/", 1)[1]

    async def raise_feed_error(**kwargs):
        raise feed_billing.FeedError("No billing account found for this server yet - run /feed setup first.")

    monkeypatch.setattr(feed_billing, "create_billing_portal_session", raise_feed_error)
    resp = client.get(f"/api/discord/feed/billing/{token}", follow_redirects=False)
    assert resp.status_code == 200
    assert "feed setup" in resp.text.lower()


def test_billing_redirect_shows_a_friendly_page_on_stripe_error(monkeypatch):
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "billing").rsplit("/", 1)[1]

    import stripe

    async def raise_stripe_error(**kwargs):
        raise stripe.error.InvalidRequestError("no such customer", param=None)

    monkeypatch.setattr(feed_billing, "create_billing_portal_session", raise_stripe_error)
    resp = client.get(f"/api/discord/feed/billing/{token}", follow_redirects=False)
    assert resp.status_code == 200
    assert "went wrong" in resp.text.lower()


def test_success_cancel_and_billing_return_pages_never_404(monkeypatch):
    """Regression test: these used to point at frontend SPA routes that
    were never built, so a real completed Stripe checkout landed on a
    404. They're now served by the backend directly."""
    client = _client(monkeypatch)
    for path in ("/api/discord/feed/success", "/api/discord/feed/cancel", "/api/discord/feed/billing-return"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


def test_billing_redirect_return_url_points_at_backend_not_frontend(monkeypatch):
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "billing").rsplit("/", 1)[1]
    create_portal = AsyncMock(return_value="https://billing.stripe.com/p/session/fresh")
    monkeypatch.setattr(feed_billing, "create_billing_portal_session", create_portal)
    client.get(f"/api/discord/feed/billing/{token}", follow_redirects=False)
    return_url = create_portal.call_args.kwargs["return_url"]
    assert "/#/" not in return_url  # not a frontend hash route
    assert return_url.endswith("/api/discord/feed/billing-return")


def test_billing_redirect_creates_a_fresh_portal_session_each_time(monkeypatch):
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "billing").rsplit("/", 1)[1]
    create_portal = AsyncMock(return_value="https://billing.stripe.com/p/session/fresh")
    monkeypatch.setattr(feed_billing, "create_billing_portal_session", create_portal)
    resp = client.get(f"/api/discord/feed/billing/{token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://billing.stripe.com/p/session/fresh"
    create_portal.assert_awaited_once()
