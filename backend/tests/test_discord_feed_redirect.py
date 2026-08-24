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


def test_checkout_redirect_404s_with_no_pending_checkout(monkeypatch):
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "checkout").rsplit("/", 1)[1]
    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value=None))
    resp = client.get(f"/api/discord/feed/checkout/{token}", follow_redirects=False)
    assert resp.status_code == 404


def test_billing_redirect_creates_a_fresh_portal_session_each_time(monkeypatch):
    client = _client(monkeypatch)
    token = redirect.make_redirect_url("g1", "billing").rsplit("/", 1)[1]
    create_portal = AsyncMock(return_value="https://billing.stripe.com/p/session/fresh")
    monkeypatch.setattr(feed_billing, "create_billing_portal_session", create_portal)
    resp = client.get(f"/api/discord/feed/billing/{token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://billing.stripe.com/p/session/fresh"
    create_portal.assert_awaited_once()
