import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.routers.discord_feed_web as web
from app.services import discord_feed_billing as feed_billing


def run(coro):
    return asyncio.run(coro)


class _FakeSession(dict):
    """Just needs .get() like a real Starlette session dict."""


class _FakeRequest:
    def __init__(self, discord_id=None, manageable_guild_ids=None):
        self.session = _FakeSession()
        if discord_id is not None:
            self.session["discord_id"] = discord_id
        if manageable_guild_ids is not None:
            self.session[web._SESSION_GUILDS_KEY] = list(manageable_guild_ids)
            self.session[web._SESSION_GUILDS_VERIFIED_KEY] = 1


def _allow(monkeypatch, allowed=True):
    monkeypatch.setattr(web, "user_has_manage_guild_live", AsyncMock(return_value=allowed))


# ---------------------------------------------------------------------------
# Auth gating: OAuth guild proof is accepted, with live Discord permission
# and stored Partner manager retained as fallbacks.
# ---------------------------------------------------------------------------


def test_no_session_is_rejected():
    with pytest.raises(HTTPException) as exc:
        run(web.get_feed("g1", _FakeRequest()))
    assert exc.value.status_code == 401


def test_session_without_manage_guild_is_rejected(monkeypatch):
    _allow(monkeypatch, allowed=False)
    monkeypatch.setattr(web, "_stored_partner_manager_matches", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as exc:
        run(web.get_feed("g1", _FakeRequest(discord_id="42")))
    assert exc.value.status_code == 403


def test_oauth_manageable_guild_session_grants_access(monkeypatch):
    _allow(monkeypatch, allowed=False)
    monkeypatch.setattr(web, "_stored_partner_manager_matches", AsyncMock(return_value=False))
    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "feed_channel_id": None, "status": "active",
    }))
    monkeypatch.setattr(feed_billing, "posts_used_today", AsyncMock(return_value=0))
    result = run(web.get_feed("g1", _FakeRequest(discord_id="42", manageable_guild_ids=["g1"])))
    assert result["posts_used_today"] == 0


def test_get_feed_requires_setup_first(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        run(web.get_feed("g1", _FakeRequest(discord_id="42")))
    assert exc.value.status_code == 404


def test_get_feed_happy_path(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "feed_channel_id": "chan1", "status": "active",
    }))
    monkeypatch.setattr(feed_billing, "posts_used_today", AsyncMock(return_value=3))
    monkeypatch.setattr(feed_billing, "confirm_channel_access", AsyncMock(return_value=(True, None)))
    result = run(web.get_feed("g1", _FakeRequest(discord_id="42")))
    assert result["posts_used_today"] == 3
    assert result["channel_ok"] is True


# ---------------------------------------------------------------------------
# Guild listing accepts OAuth-proven guilds and still supports live checks.
# ---------------------------------------------------------------------------


class _FakeRow(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *a, **kw):
        return self._rows


def _server_row(guild_id, name, plan="server_feed", status="active"):
    return _FakeRow(
        guild_id=guild_id,
        name=name,
        plan=plan,
        access_level="paid_partner",
        status=status,
        subscription_status="active",
        owner_discord_id=None,
    )


def test_list_manageable_guilds_filters_by_live_permission(monkeypatch):
    rows = [
        _server_row("g1", "Guild One"),
        _server_row("g2", "Guild Two", plan="server_feed_plus", status="paused"),
    ]
    monkeypatch.setattr(web, "get_core_pool", AsyncMock(return_value=_FakePool(rows)))

    async def fake_check(client, guild_id, discord_id):
        return guild_id == "g1"

    monkeypatch.setattr(web, "user_has_manage_guild_live", fake_check)
    result = run(web.list_manageable_guilds(_FakeRequest(discord_id="42")))
    assert [g["guild_id"] for g in result["guilds"]] == ["g1"]
    assert result["guild_oauth_verified"] is False


def test_list_manageable_guilds_uses_oauth_guild_ids_without_live_lookup(monkeypatch):
    rows = [
        _server_row("g1", "Guild One"),
        _server_row("g2", "Guild Two"),
    ]
    monkeypatch.setattr(web, "get_core_pool", AsyncMock(return_value=_FakePool(rows)))
    live = AsyncMock(return_value=False)
    monkeypatch.setattr(web, "user_has_manage_guild_live", live)

    result = run(web.list_manageable_guilds(
        _FakeRequest(discord_id="42", manageable_guild_ids=["g2"])
    ))
    assert [g["guild_id"] for g in result["guilds"]] == ["g2"]
    assert result["guild_oauth_verified"] is True
    # g2 was OAuth-proven, so only g1 needed the slower bot-side lookup.
    assert live.await_count == 1


# ---------------------------------------------------------------------------
# Preferences endpoint passes posting-window fields through and maps
# FeedError to a 400, not a 500.
# ---------------------------------------------------------------------------


def test_set_preferences_passes_posting_window_through(monkeypatch):
    _allow(monkeypatch)
    set_prefs = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(feed_billing, "set_preferences", set_prefs)
    body = web.SetPreferencesBody(posting_window_start_hour=8, posting_window_end_hour=23, posting_timezone="Europe/London")
    run(web.set_preferences("g1", _FakeRequest(discord_id="42"), body))
    _, kwargs = set_prefs.call_args
    assert kwargs["posting_window_start_hour"] == 8
    assert kwargs["posting_window_end_hour"] == 23
    assert kwargs["posting_timezone"] == "Europe/London"
    assert kwargs["admin_id"] == "42"


def test_set_preferences_maps_feed_error_to_400(monkeypatch):
    _allow(monkeypatch)

    async def raise_error(**kwargs):
        raise feed_billing.FeedError("Posting window hours must be between 0 and 23.")

    monkeypatch.setattr(feed_billing, "set_preferences", raise_error)
    with pytest.raises(HTTPException) as exc:
        run(web.set_preferences("g1", _FakeRequest(discord_id="42"), web.SetPreferencesBody()))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Checkout link is refused once a subscription is already active.
# ---------------------------------------------------------------------------


def test_checkout_url_blocked_when_already_active(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "subscription_status": "active",
    }))
    with pytest.raises(HTTPException) as exc:
        run(web.checkout_url("g1", _FakeRequest(discord_id="42")))
    assert exc.value.status_code == 400


def test_billing_url_returns_a_redirect_link(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(feed_billing, "get_guild_feed", AsyncMock(return_value={"id": 1}))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    result = run(web.billing_url("g1", _FakeRequest(discord_id="42")))
    assert result["url"].startswith("https://api.eafc.live/api/discord/feed/billing/")
