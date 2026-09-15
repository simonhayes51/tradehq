from __future__ import annotations

"""
Web management API for the Server Feed, backing the /v2/discord-feed
settings page.

Dashboard Discord login is also defined here deliberately. This router is
included before main.py's legacy /api/login and /api/callback routes, so the
routes below are the first matching routes in FastAPI/Starlette. Keeping the
override here avoids duplicating the giant main.py just to add Discord's
standard `guilds` OAuth scope.

The dashboard login requests only `identify guilds`. Discord returns the
caller's guild list plus their guild-level permission bitfield. We retain in
the signed session only the IDs of EAFC.Live-registered Server Feed guilds
where that caller is the guild owner or currently has ADMINISTRATOR /
MANAGE_GUILD. We do not retain their complete Discord server list.

Access checks keep the existing live bot-side permission lookup and the
stored Partner/Official manager fallback as additional paths, so old sessions
continue to work where they already did and a temporary Discord API failure
does not unnecessarily lock a legitimate partner out.
"""

import json
import logging
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.db import get_core_pool
from app.services import discord_feed_billing as feed_billing
from app.services.discord_integration import DiscordClient
from app.services.discord_permissions import user_has_manage_guild_live
from app.services.discord_trading_feed import send_test_post, server_record_from_feed

log = logging.getLogger("discord_feed_web")

# No router-level prefix: this router also owns the production dashboard
# /api/login + /api/callback overrides. Feed routes keep their full path.
router = APIRouter(tags=["discord-feed-web"])

_GUILD_CHANNEL_TYPES = frozenset({0, 5})  # GUILD_TEXT, GUILD_ANNOUNCEMENT
_ADMINISTRATOR = 1 << 3
_MANAGE_GUILD = 1 << 5
_SESSION_GUILDS_KEY = "discord_manageable_guild_ids"
_SESSION_GUILDS_VERIFIED_KEY = "discord_guild_access_verified_at"


def _session_discord_id(request: Request) -> str:
    discord_id = request.session.get("discord_id")
    if not discord_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return str(discord_id)


def _session_manages_guild(request: Request, guild_id: str) -> bool:
    guilds = request.session.get(_SESSION_GUILDS_KEY) or []
    if not isinstance(guilds, list):
        return False
    return str(guild_id) in {str(value) for value in guilds}


async def _oauth_registered_manageable_guild_ids(access_token: str) -> list[str]:
    """Return only registered EAFC.Live guild IDs the OAuth caller manages.

    `/users/@me/guilds` can return up to 200 guilds at a time. Pagination is
    supported so a very large Discord account still behaves correctly. We
    discard every unrelated guild immediately and persist nothing here.
    """
    pool = await get_core_pool()
    rows = await pool.fetch(
        "SELECT guild_id FROM discord_servers WHERE plan IS NOT NULL OR access_level IN ('paid_partner','official')"
    )
    registered = {str(row["guild_id"]) for row in rows if row["guild_id"]}
    if not registered:
        return []

    manageable: set[str] = set()
    headers = {"Authorization": f"Bearer {access_token}"}
    after: Optional[str] = None

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            for _ in range(10):  # hard ceiling: 2,000 guilds
                params = {"limit": "200"}
                if after:
                    params["after"] = after
                async with session.get(
                    "https://discord.com/api/v10/users/@me/guilds",
                    headers=headers,
                    params=params,
                ) as resp:
                    if resp.status != 200:
                        log.warning("Discord guild OAuth lookup returned %s", resp.status)
                        return []
                    guilds = await resp.json(content_type=None)

                if not isinstance(guilds, list):
                    return []

                for guild in guilds:
                    if not isinstance(guild, dict):
                        continue
                    guild_id = str(guild.get("id") or "")
                    if not guild_id or guild_id not in registered:
                        continue
                    try:
                        permissions = int(guild.get("permissions") or 0)
                    except (TypeError, ValueError):
                        permissions = 0
                    if bool(guild.get("owner")) or permissions & (_ADMINISTRATOR | _MANAGE_GUILD):
                        manageable.add(guild_id)

                if len(manageable) == len(registered) or len(guilds) < 200:
                    break
                last_id = str((guilds[-1] or {}).get("id") or "") if guilds else ""
                if not last_id or last_id == after:
                    break
                after = last_id
    except Exception:
        # A Discord outage must not make login fail. Existing live/stored
        # permission fallbacks can still authorise the web page.
        log.warning("Discord guild OAuth lookup failed", exc_info=True)
        return []

    return sorted(manageable)


# ---------------------------------------------------------------------------
# Dashboard Discord OAuth override
# ---------------------------------------------------------------------------


@router.get("/api/login")
async def dashboard_login_with_guild_access(request: Request, return_to: Optional[str] = None):
    """Dashboard login with Discord's standard identify + guilds scopes."""
    import main as app_main

    return_to = app_main._safe_return_to(return_to)
    if app_main.DISABLE_DISCORD_AUTH:
        log.warning(
            "DISABLE_DISCORD_AUTH is set - bypassing Discord OAuth, logging in as dev identity %s",
            app_main.DEV_DISCORD_ID,
        )
        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{app_main.DEV_DISCORD_ID % 5}.png"
        await app_main._establish_dashboard_session(
            request,
            app_main.DEV_DISCORD_ID,
            app_main.DEV_USERNAME,
            avatar_url,
            app_main.DEV_USERNAME,
        )
        request.session[_SESSION_GUILDS_KEY] = []
        request.session[_SESSION_GUILDS_VERIFIED_KEY] = int(time.time())
        return RedirectResponse(f"{app_main.FRONTEND_URL}/#{return_to}")

    state = secrets.token_urlsafe(24)
    app_main.OAUTH_STATE[state] = {
        "flow": "dashboard",
        "ts": time.time(),
        "return_to": return_to,
    }
    params = {
        "client_id": app_main.DISCORD_CLIENT_ID,
        "redirect_uri": app_main.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
        "prompt": "consent",
    }
    return RedirectResponse(f"{app_main.DISCORD_OAUTH_AUTHORIZE}?{urlencode(params)}")


@router.get("/api/callback")
async def dashboard_callback_with_guild_access(request: Request):
    """Preserve the existing callback behaviour and capture manageable guilds."""
    import main as app_main

    err = request.query_params.get("error")
    err_desc = request.query_params.get("error_description")
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    app_main._prune_oauth_state()
    state_meta = app_main.OAUTH_STATE.get(state) if state else None
    is_browser_flow = state_meta is None or state_meta.get("flow") == "dashboard"
    return_to = (state_meta or {}).get("return_to")

    if err:
        log.info("Discord OAuth returned an error: %s (%s)", err, (err_desc or "")[:200])
        if state:
            app_main.OAUTH_STATE.pop(state, None)
        if is_browser_flow:
            return app_main._oauth_failure_redirect(err, return_to=return_to)
        raise HTTPException(400, detail=f"OAuth error from Discord: {err}")

    if not code:
        if is_browser_flow:
            return app_main._oauth_failure_redirect("server_error", return_to=return_to)
        raise HTTPException(status_code=400, detail="Missing code")

    if state_meta is None:
        if is_browser_flow:
            return app_main._oauth_failure_redirect("expired_state")
        raise HTTPException(400, detail="Invalid or expired OAuth state - please retry login")
    app_main.OAUTH_STATE.pop(state, None)

    data = {
        "client_id": app_main.DISCORD_CLIENT_ID,
        "client_secret": app_main.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": app_main.DISCORD_REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.post(app_main.DISCORD_OAUTH_TOKEN, data=data, headers=headers) as resp:
                txt = await resp.text()
                if resp.status != 200:
                    log.error("Discord token exchange failed (%s): %s", resp.status, txt[:500])
                    if is_browser_flow:
                        return app_main._oauth_failure_redirect("exchange_failed", return_to=return_to)
                    raise HTTPException(400, detail="OAuth token exchange failed - please retry login")
                token_data = json.loads(txt)

        access_token = token_data.get("access_token")
        if not access_token:
            log.error("No access_token in Discord token response")
            if is_browser_flow:
                return app_main._oauth_failure_redirect("exchange_failed", return_to=return_to)
            raise HTTPException(400, detail="OAuth failed (no access_token)")

        user_data = await app_main.get_discord_user_info(access_token)
        if not user_data or "id" not in user_data:
            log.error("Failed to fetch Discord user data")
            if is_browser_flow:
                return app_main._oauth_failure_redirect("exchange_failed", return_to=return_to)
            raise HTTPException(400, detail="Failed to fetch user data")
        user_id = user_data["id"]

        # The browser extension still requests identify only. Preserve that
        # existing flow exactly; it neither needs nor expects guild access.
        if state_meta.get("flow") != "dashboard":
            jwt_token = app_main.issue_extension_token(user_id)
            ext_redirect = state_meta.get("ext_redirect")
            if not ext_redirect:
                raise HTTPException(400, detail="Invalid extension state")
            return RedirectResponse(f"{ext_redirect}#token={jwt_token}&state={state}")

        discord_id = int(user_id)
        if os.getenv("REQUIRE_GUILD_MEMBERSHIP", "0") == "1":
            if not await app_main.check_server_membership(discord_id):
                return RedirectResponse(f"{app_main.FRONTEND_URL}/#/access-denied")

        username = user_data.get("global_name") or user_data.get("username") or "User"
        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{user_id}/{user_data['avatar']}.png"
            if user_data.get("avatar")
            else f"https://cdn.discordapp.com/embed/avatars/{int(user_data.get('discriminator','0') or 0) % 5}.png"
        )
        global_name = user_data.get("global_name") or user_data.get("username") or "User"

        await app_main._establish_dashboard_session(
            request, discord_id, username, avatar_url, global_name
        )

        manageable_guilds = await _oauth_registered_manageable_guild_ids(access_token)
        request.session[_SESSION_GUILDS_KEY] = manageable_guilds
        request.session[_SESSION_GUILDS_VERIFIED_KEY] = int(time.time())
        log.info(
            "Discord OAuth successful for user %s; %s registered manageable guild(s)",
            discord_id,
            len(manageable_guilds),
        )

        return RedirectResponse(
            f"{app_main.FRONTEND_URL}/#{app_main._safe_return_to(state_meta.get('return_to'))}"
        )

    except HTTPException:
        raise
    except Exception:
        log.exception("OAuth unexpected error")
        if is_browser_flow:
            return app_main._oauth_failure_redirect("server_error", return_to=return_to)
        raise HTTPException(500, detail="Authentication failed")


# ---------------------------------------------------------------------------
# Server Feed access + settings
# ---------------------------------------------------------------------------


async def _stored_partner_manager_matches(guild_id: str, discord_id: str) -> bool:
    """Fallback only for admin-managed Partner/Official feeds."""
    try:
        pool = await get_core_pool()
        row = await pool.fetchrow(
            """
            SELECT owner_discord_id, access_level
            FROM discord_servers
            WHERE guild_id=$1
            """,
            str(guild_id),
        )
    except Exception:
        log.exception("Could not resolve stored Discord feed manager for guild %s", guild_id)
        return False

    if not row:
        return False
    if row["access_level"] not in ("paid_partner", "official"):
        return False
    return bool(row["owner_discord_id"]) and str(row["owner_discord_id"]) == str(discord_id)


async def _user_can_manage_guild(
    request: Request, client: DiscordClient, guild_id: str, discord_id: str
) -> bool:
    if _session_manages_guild(request, guild_id):
        return True
    if await user_has_manage_guild_live(client, guild_id, discord_id):
        return True
    return await _stored_partner_manager_matches(guild_id, discord_id)


async def _require_guild_access(request: Request, guild_id: str, client: DiscordClient) -> str:
    discord_id = _session_discord_id(request)
    if not await _user_can_manage_guild(request, client, guild_id, discord_id):
        raise HTTPException(
            status_code=403,
            detail="You need Manage Server or Administrator in that Discord server to manage its feed.",
        )
    return discord_id


async def _get_feed_or_404(guild_id: str) -> dict[str, Any]:
    feed = await feed_billing.get_guild_feed(guild_id)
    if not feed:
        raise HTTPException(status_code=404, detail="This server hasn't got a Server Feed set up yet.")
    return feed


@router.get("/api/v2/discord-feed/guilds")
async def list_manageable_guilds(request: Request):
    """Return every configured Server Feed the caller can manage."""
    discord_id = _session_discord_id(request)
    pool = await get_core_pool()
    rows = await pool.fetch(
        """
        SELECT guild_id, name, plan, access_level, status,
               subscription_status, owner_discord_id
        FROM discord_servers
        WHERE plan IS NOT NULL
           OR access_level IN ('paid_partner', 'official')
        ORDER BY created_at DESC
        """
    )
    oauth_guilds = {
        str(value) for value in (request.session.get(_SESSION_GUILDS_KEY) or [])
    }
    client = DiscordClient()
    manageable = []
    for row in rows:
        guild_id = str(row["guild_id"])
        oauth_access = guild_id in oauth_guilds
        live_access = False
        if not oauth_access:
            live_access = await user_has_manage_guild_live(client, guild_id, discord_id)
        stored_partner_access = (
            row["access_level"] in ("paid_partner", "official")
            and bool(row["owner_discord_id"])
            and str(row["owner_discord_id"]) == str(discord_id)
        )
        if oauth_access or live_access or stored_partner_access:
            manageable.append({
                "guild_id": row["guild_id"],
                "name": row["name"],
                "plan": row["plan"],
                "access_level": row["access_level"],
                "status": row["status"],
                "subscription_status": row["subscription_status"],
            })
    return {
        "guilds": manageable,
        "guild_oauth_verified": _SESSION_GUILDS_VERIFIED_KEY in request.session,
    }


@router.get("/api/v2/discord-feed/{guild_id}")
async def get_feed(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    feed = await _get_feed_or_404(guild_id)
    posts_used = await feed_billing.posts_used_today(feed["id"])
    channel_ok, reason = (True, None)
    if feed.get("feed_channel_id"):
        channel_ok, reason = await feed_billing.confirm_channel_access(guild_id, feed["feed_channel_id"])
    return {
        "feed": feed,
        "posts_used_today": posts_used,
        "channel_ok": channel_ok,
        "channel_problem": feed_billing.CHANNEL_ACCESS_MESSAGES.get(reason) if reason else None,
    }


@router.get("/api/v2/discord-feed/{guild_id}/channels")
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


@router.patch("/api/v2/discord-feed/{guild_id}/channel")
async def set_channel(guild_id: str, request: Request, body: SetChannelBody):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.set_channel(
            guild_id=guild_id, admin_id=discord_id, channel_id=body.channel_id
        )
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


class SetSellChannelBody(BaseModel):
    channel_id: Optional[str] = None


@router.patch("/api/v2/discord-feed/{guild_id}/sell-channel")
async def set_sell_channel(guild_id: str, request: Request, body: SetSellChannelBody):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.set_sell_channel(
            guild_id=guild_id, admin_id=discord_id, channel_id=body.channel_id
        )
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


@router.patch("/api/v2/discord-feed/{guild_id}/preferences")
async def set_preferences(guild_id: str, request: Request, body: SetPreferencesBody):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.set_preferences(
            guild_id=guild_id,
            admin_id=discord_id,
            platform_scope=body.platform_scope,
            budget_mix=body.budget_mix,
            min_confidence=body.min_confidence,
            min_expected_profit=body.min_expected_profit,
            posting_window_start_hour=body.posting_window_start_hour,
            posting_window_end_hour=body.posting_window_end_hour,
            posting_timezone=body.posting_timezone,
            clear_posting_window=body.clear_posting_window,
        )
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.post("/api/v2/discord-feed/{guild_id}/test")
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


@router.post("/api/v2/discord-feed/{guild_id}/activate")
async def activate(guild_id: str, request: Request):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.activate(guild_id=guild_id, admin_id=discord_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.post("/api/v2/discord-feed/{guild_id}/pause")
async def pause(guild_id: str, request: Request):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.pause(guild_id=guild_id, admin_id=discord_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.post("/api/v2/discord-feed/{guild_id}/resume")
async def resume(guild_id: str, request: Request):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    try:
        feed = await feed_billing.resume(guild_id=guild_id, admin_id=discord_id)
    except feed_billing.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"feed": feed}


@router.get("/api/v2/discord-feed/{guild_id}/billing-url")
async def billing_url(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    await _get_feed_or_404(guild_id)
    from app.routers.discord_feed_redirect import make_redirect_url

    return {"url": make_redirect_url(guild_id, "billing")}


@router.get("/api/v2/discord-feed/{guild_id}/checkout-url")
async def checkout_url(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    feed = await _get_feed_or_404(guild_id)
    if feed.get("subscription_status") in feed_billing.ACTIVE_SUBSCRIPTION_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="This server already has an active subscription - use the billing link instead.",
        )
    from app.routers.discord_feed_redirect import make_redirect_url

    return {"url": make_redirect_url(guild_id, "checkout")}
