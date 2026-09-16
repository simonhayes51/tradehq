from __future__ import annotations

"""Server Feed delivery extras: platform labels and optional role pings.

The trading-feed engine itself already owns candidate selection, limits,
proof/result grading, dedupe and posting cadence. This module deliberately
does not duplicate any of that logic. Instead it decorates the two delivery
entry points at import time so the existing engine continues to be the single
source of truth while messages gain two presentation/delivery features:

* every live trade/result clearly says CONSOLE or PC;
* a server may optionally ping one configured role for new trades and one
  configured role for results (with the buy role used for both when no
  separate sell role is selected).

Role mentions are allow-listed by exact role id. ``@everyone``, ``@here`` and
user mentions are never enabled by this code.
"""

import contextvars
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db import get_core_pool
from app.routers.discord_feed_web import _get_feed_or_404, _require_guild_access
from app.services.discord_integration import DiscordClient
from app.services import discord_trading_feed as feed

log = logging.getLogger("discord_feed_delivery")

router = APIRouter(prefix="/discord-feed", tags=["discord-feed-web"])

_platform_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "discord_feed_platform", default="ps"
)

_ORIGINAL_BUILD_OPPORTUNITY = feed.build_opportunity_post_embed
_ORIGINAL_BUILD_UPDATE = feed.build_opportunity_update_embed
_ORIGINAL_PUBLISH_LIVE = feed.publish_live_opportunities
_ORIGINAL_PUBLISH_UPDATES = feed.publish_opportunity_updates
_INSTALLED = False


def _platform_label(platform: Optional[str]) -> str:
    value = str(platform or "").strip().lower()
    return "PC" if value == "pc" else "CONSOLE"


def _clean_role_id(value: Optional[str]) -> Optional[str]:
    role_id = str(value or "").strip()
    return role_id if role_id.isdigit() else None


class _RoleMentionClient:
    """DiscordClient proxy that permits one exact role mention only."""

    def __init__(self, client: DiscordClient, role_id: Optional[str]):
        self._client = client
        self._role_id = _clean_role_id(role_id)

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    async def post_embed(
        self,
        channel_id: str,
        embed: dict[str, Any],
        *,
        nonce: str,
        enforce_nonce: bool,
        components: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[int, Any]:
        if not channel_id or not embed:
            return 0, {"message": "Missing channel or embed"}

        payload: dict[str, Any] = {
            "embeds": [embed],
            "nonce": nonce,
            "enforce_nonce": enforce_nonce,
            "allowed_mentions": {"parse": []},
        }
        if components:
            payload["components"] = components
        if self._role_id:
            payload["content"] = f"<@&{self._role_id}>"
            payload["allowed_mentions"] = {
                "parse": [],
                "roles": [self._role_id],
            }

        return await self._client._request(
            "POST", f"/channels/{channel_id}/messages", payload=payload
        )


async def _delivery_settings(server_id: int) -> dict[str, Optional[str]]:
    pool = await get_core_pool()
    row = await pool.fetchrow(
        """
        SELECT s.buy_ping_role_id, s.sell_ping_role_id, st.platform
        FROM discord_servers s
        LEFT JOIN discord_server_settings st ON st.server_id=s.id
        WHERE s.id=$1
        """,
        server_id,
    )
    if not row:
        return {"buy_role": None, "sell_role": None, "platform": "ps"}
    return {
        "buy_role": _clean_role_id(row["buy_ping_role_id"]),
        "sell_role": _clean_role_id(row["sell_ping_role_id"]),
        "platform": str(row["platform"] or "ps"),
    }


def _build_opportunity_with_platform(
    row: dict[str, Any], ci: Any, *, test: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    embed, components = _ORIGINAL_BUILD_OPPORTUNITY(row, ci, test=test)
    platform = _platform_label(_platform_ctx.get())

    # Put it in both places: the title makes the platform obvious while
    # scrolling and the field keeps it clear inside the trade details.
    base_title = str(embed.get("title") or "Trade")
    test_prefix = "[TEST] " if base_title.startswith("[TEST] ") else ""
    if test_prefix:
        base_title = base_title[len(test_prefix):]
    embed["title"] = f"{test_prefix}{platform} TRADE — {base_title}"

    fields = list(embed.get("fields") or [])
    if not any(str(field.get("name") or "").upper() == "PLATFORM" for field in fields):
        fields.append({"name": "PLATFORM", "value": platform.title() if platform != "PC" else "PC", "inline": True})
    embed["fields"] = fields
    return embed, components


def _build_update_with_platform(post: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    embed = _ORIGINAL_BUILD_UPDATE(post, outcome)
    platform = _platform_label(_platform_ctx.get())
    base_title = str(embed.get("title") or "Trade update")
    test_prefix = "[TEST] " if base_title.startswith("[TEST] ") else ""
    if test_prefix:
        base_title = base_title[len(test_prefix):]
    embed["title"] = f"{test_prefix}{platform} — {base_title}"
    fields = list(embed.get("fields") or [])
    if not any(str(field.get("name") or "").upper() == "PLATFORM" for field in fields):
        fields.append({"name": "PLATFORM", "value": platform.title() if platform != "PC" else "PC", "inline": True})
    embed["fields"] = fields
    return embed


async def _publish_live_with_delivery(
    pool, server: feed.ServerRecord, client: Optional[DiscordClient] = None
) -> int:
    settings = await _delivery_settings(server.id)
    token = _platform_ctx.set(str(settings["platform"] or "ps"))
    try:
        real_client = client or DiscordClient()
        return await _ORIGINAL_PUBLISH_LIVE(
            pool,
            server,
            _RoleMentionClient(real_client, settings["buy_role"]),
        )
    finally:
        _platform_ctx.reset(token)


async def _publish_updates_with_delivery(
    pool, server: feed.ServerRecord, client: Optional[DiscordClient] = None
) -> int:
    settings = await _delivery_settings(server.id)
    token = _platform_ctx.set(str(settings["platform"] or "ps"))
    try:
        real_client = client or DiscordClient()
        role_id = settings["sell_role"] or settings["buy_role"]
        return await _ORIGINAL_PUBLISH_UPDATES(
            pool,
            server,
            _RoleMentionClient(real_client, role_id),
        )
    finally:
        _platform_ctx.reset(token)


def install_delivery_features() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    feed.build_opportunity_post_embed = _build_opportunity_with_platform
    feed.build_opportunity_update_embed = _build_update_with_platform
    feed.publish_live_opportunities = _publish_live_with_delivery
    feed.publish_opportunity_updates = _publish_updates_with_delivery
    _INSTALLED = True


# The v2 router is imported during application bootstrap, before background
# workers begin. Installing here ensures run_all_server_feeds() resolves the
# decorated delivery functions on every pass without changing its trading
# logic.
install_delivery_features()


async def _guild_roles(guild_id: str, client: DiscordClient) -> list[dict[str, Any]]:
    status, roles = await client.get_guild_roles(guild_id)
    if status != 200 or not isinstance(roles, list):
        raise HTTPException(status_code=502, detail="Couldn't fetch this server's roles from Discord.")

    result: list[dict[str, Any]] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        role_id = str(role.get("id") or "")
        # Discord's @everyone role has the same id as the guild. Never let
        # it become a configurable feed ping.
        if not role_id or role_id == str(guild_id):
            continue
        result.append(
            {
                "id": role_id,
                "name": str(role.get("name") or "Role"),
                "mentionable": bool(role.get("mentionable")),
                "position": int(role.get("position") or 0),
            }
        )
    result.sort(key=lambda role: (-role["position"], role["name"].lower()))
    return result


@router.get("/{guild_id}/roles")
async def list_ping_roles(guild_id: str, request: Request):
    client = DiscordClient()
    await _require_guild_access(request, guild_id, client)
    return {"roles": await _guild_roles(guild_id, client)}


class SetPingRolesBody(BaseModel):
    buy_role_id: Optional[str] = None
    sell_role_id: Optional[str] = None


@router.patch("/{guild_id}/ping-roles")
async def set_ping_roles(guild_id: str, request: Request, body: SetPingRolesBody):
    client = DiscordClient()
    discord_id = await _require_guild_access(request, guild_id, client)
    feed_row = await _get_feed_or_404(guild_id)

    roles = await _guild_roles(guild_id, client)
    valid_ids = {role["id"] for role in roles}

    buy_role_id = _clean_role_id(body.buy_role_id)
    sell_role_id = _clean_role_id(body.sell_role_id)
    if body.buy_role_id and not buy_role_id:
        raise HTTPException(status_code=400, detail="Invalid new-trade ping role.")
    if body.sell_role_id and not sell_role_id:
        raise HTTPException(status_code=400, detail="Invalid result ping role.")
    if buy_role_id and buy_role_id not in valid_ids:
        raise HTTPException(status_code=400, detail="That new-trade ping role no longer exists in this server.")
    if sell_role_id and sell_role_id not in valid_ids:
        raise HTTPException(status_code=400, detail="That result ping role no longer exists in this server.")

    pool = await get_core_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE discord_servers
            SET buy_ping_role_id=$2, sell_ping_role_id=$3, updated_at=NOW()
            WHERE guild_id=$1
            """,
            str(guild_id),
            buy_role_id,
            sell_role_id,
        )
        await conn.execute(
            """
            INSERT INTO discord_server_audit_log (server_id, admin_id, action, details)
            VALUES ($1, $2, 'feed_ping_roles_changed', $3::jsonb)
            """,
            feed_row["id"],
            str(discord_id),
            __import__("json").dumps(
                {"buy_ping_role_id": buy_role_id, "sell_ping_role_id": sell_role_id}
            ),
        )

    return {"feed": await _get_feed_or_404(guild_id)}
