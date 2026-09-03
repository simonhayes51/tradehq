from __future__ import annotations

"""
Web management API for the self-service Server Feed, backing the
/v2/discord-feed settings page. This is a convenience surface, not a
replacement - every setting here is also reachable via the /feed Discord
command group (discord_interactions.py), and both drive the exact same
discord_feed_billing.py functions so behavior can never diverge between
the two.

Access control: a logged-in dashboard user (request.session["discord_id"])
may manage a guild's feed only if Discord's own API says, RIGHT NOW, that
they have MANAGE_GUILD or ADMINISTRATOR in that guild (or are its owner) -
see discord_permissions.user_has_manage_guild_live(). This is re-checked
on every request rather than trusted from a stored owner_discord_id,
since roles and ownership can change after /feed setup ran.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import get_core_pool
from app.services import discord_feed_billing as feed_billing
from app.services.discord_integration import DiscordClient
from app.services.discord_permissions import user_has_manage_guild_live
from app.services.discord_trading_feed import ServerRecord, send_test_post, server_record_from_feed

log = logging.getLogger("discord_feed_web")

router = APIRouter(prefix="/api/v2/discord-feed", tags=["discord-feed-web"])

_GUILD_CHANNEL_TYPES = frozenset({0, 5})  # GUILD_TEXT, GUILD_ANNOUNCEMENT


def _session_discord_id(request: Request) -> str:
    discord_id = request.session.get("discord_id")
    if not discord_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return str(discord_id)


async def _require_guild_access(request: Request, guild_id: str, client: DiscordClient) -> str:
    """Returns the caller's Discord user id once verified, or raises 403.
    Separate from _session_discord_id() so callers that need the id for
    audit logging (admin_id=...) don't do the permission REST calls twice."""
    discord_id = _session_discord_id(request)
    if not await user_has_manage_guild_live(client, guild_id, discord_id):
        raise HTTPException(status_code=403, detail="You need Manage Server in that Discord server to manage its feed.")
    return discord_id


async def _get_feed_or_404(guild_id: str) -> dict[str, Any]:
    feed = await feed_billing.get_guild_feed(guild_id)
    if not feed:
        raise HTTPException(status_code=404, detail="This server hasn't run /feed setup yet.")
    return feed


@router.get("/guilds")
async def list_manageable_guilds(request: Request):
    """Every self-service Server Feed guild (plan IS NOT NULL - excludes
    admin-managed official/partner rows with no billing) where the caller
    currently has Manage Server, checked live against Discord."""
    discord_id = _session_discord_id(request)
    pool = await get_core_pool()
    rows = await pool.fetch(
        "SELECT guild_id, name, plan, status, subscription_status FROM discord_servers WHERE plan IS NOT NULL"
    )
    client = DiscordClient()
    manageable = []
    for row in rows:
        if await user_has_manage_guild_live(client, row["guild_id"], discord_id):
            manageable.append({
                "guild_id": row["guild_id"], "name": row["name"],
                "plan": row["plan"], "status": row["status"],
                "subscription_status": row["subscription_status"],
            })
    return {"guilds": manageable}


@router.get("/{guild_id}")
async def get_feed(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    feed = await _get_feed_or_404(guild_id)
    posts_used = await feed_billing.posts_used_today(feed["id"])
    channel_ok, reason = (True, None)
    if feed.get("feed_channel_id"):
        channel_ok, reason = await feed_billing.confirm_channel_access(guild_id, feed["feed_channel_id"])
    return {
        "feed": feed, "posts_used_today": posts_used,
        "channel_ok": channel_ok,
        "channel_problem": feed_billing.CHANNEL_ACCESS_MESSAGES.get(reason) if reason else None,
    }


@router.get("/{guild_id}/channels")
async def list_channels(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    status, channels = await client.get_guild_channels(guild_id)
    if status != 200 or not isinstance(channels, list):
        raise HTTPException(status_code=502, detail="Couldn't fetch this server's channels from Discord.")
    return {
        "channels": [
            {"id": str(c["id"]), "name": c.get("name")}
            for c in channels
            if c.get("type") in _GUILD_CHANNEL_TYPES
        ]
    }


class SetChannelBody(BaseModel):
    channel_id: str


@router.patch("/{guild_id}/channel")
async def set_channel(guild_id: str, request: Request, body: SetChannelBody):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.set_channel(guild_id=guild_id, admin_id=discord_id, channel_id=body.channel_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


class SetSellChannelBody(BaseModel):
    # None (the JSON field omitted, or explicit null) clears it - result
    # posts go back to the main feed channel.
    channel_id: Optional[str] = None


@router.patch("/{guild_id}/sell-channel")
async def set_sell_channel(guild_id: str, request: Request, body: SetSellChannelBody):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.set_sell_channel(guild_id=guild_id, admin_id=discord_id, channel_id=body.channel_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


class SetPreferencesBody(BaseModel):
    platform_scope: Optional[str] = None
    budget_mix: Optional[str] = None
    min_confidence: Optional[float] = None
    min_expected_profit: Optional[int] = None
    posting_window_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    posting_window_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    posting_timezone: Optional[str] = None
    clear_posting_window: bool = False


@router.patch("/{guild_id}/preferences")
async def set_preferences(guild_id: str, request: Request, body: SetPreferencesBody):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.set_preferences(
            guild_id=guild_id, admin_id=discord_id,
            platform_scope=body.platform_scope, budget_mix=body.budget_mix,
            min_confidence=body.min_confidence, min_expected_profit=body.min_expected_profit,
            posting_window_start_hour=body.posting_window_start_hour,
            posting_window_end_hour=body.posting_window_end_hour,
            posting_timezone=body.posting_timezone,
            clear_posting_window=body.clear_posting_window,
        )
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.post("/{guild_id}/test")
async def test_post(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    feed = await _get_feed_or_404(guild_id)
    if not feed.get("feed_channel_id"):
        raise HTTPException(status_code=400, detail="Set a posting channel first.")
    server = server_record_from_feed(feed, guild_id, feed.get("name") or guild_id)
    ok, error = await send_test_post(await get_core_pool(), server, client)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Test post failed: {error or 'unknown error'}")
    await feed_billing.mark_test_post_confirmed(guild_id)
    return {"ok": True}


@router.post("/{guild_id}/activate")
async def activate(guild_id: str, request: Request):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.activate(guild_id=guild_id, admin_id=discord_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.post("/{guild_id}/pause")
async def pause(guild_id: str, request: Request):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.pause(guild_id=guild_id, admin_id=discord_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.post("/{guild_id}/resume")
async def resume(guild_id: str, request: Request):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.resume(guild_id=guild_id, admin_id=discord_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.get("/{guild_id}/billing-url")
async def billing_url(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    await _get_feed_or_404(guild_id)
    from app.routers.discord_feed_redirect import make_redirect_url

    return {"url": make_redirect_url(guild_id, "billing")}


@router.get("/{guild_id}/checkout-url")
async def checkout_url(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    feed = await _get_feed_or_404(guild_id)
    if feed.get("subscription_status") in feed_billing.ACTIVE_SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=400, detail="This server already has an active subscription - use the billing link instead.")
    from app.routers.discord_feed_redirect import make_redirect_url

    return {"url": make_redirect_url(guild_id, "checkout")}
