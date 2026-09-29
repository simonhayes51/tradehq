from __future__ import annotations

from typing import Any, Optional

from app.db import get_core_pool
from app.services.discord_integration import DiscordClient
from app.services.discord_permissions import check_bot_channel_access


async def get_destination(guild_id: str) -> Optional[dict[str, Any]]:
    pool = await get_core_pool()
    row = await pool.fetchrow(
        """SELECT guild_id,guild_name,channel_id,enabled,configured_by_discord_id,created_at,updated_at
           FROM discord_leak_feed_destinations WHERE guild_id=$1""",
        str(guild_id),
    )
    return dict(row) if row else None


async def list_enabled_destinations() -> list[dict[str, Any]]:
    pool = await get_core_pool()
    rows = await pool.fetch(
        """SELECT guild_id,guild_name,channel_id,enabled
           FROM discord_leak_feed_destinations
           WHERE enabled=TRUE
           ORDER BY guild_name NULLS LAST,guild_id"""
    )
    return [dict(r) for r in rows]


async def setup_destination(
    *,
    guild_id: str,
    guild_name: str,
    channel_id: str,
    configured_by_discord_id: str,
) -> dict[str, Any]:
    client = DiscordClient()
    ok, reason = await check_bot_channel_access(client, str(guild_id), str(channel_id))
    if not ok:
        messages = {
            "channel_not_found": "I can't find that channel. Check it still exists and that the EAFC.Live bot can see it.",
            "invalid_channel_type": "Choose a text or announcement channel.",
            "bot_not_in_guild": "The EAFC.Live bot isn't installed in this server. Re-invite it, then try again.",
            "missing_permissions": "I can't post in that channel. Give the EAFC.Live bot View Channel, Send Messages and Embed Links there, then try again.",
            "bot_auth_failed": "EAFC.Live's Discord connection needs attention. Please contact EAFC.Live support.",
            "discord_unavailable": "Discord is temporarily unavailable. Try again shortly.",
        }
        raise ValueError(messages.get(reason, "I couldn't verify that channel."))

    pool = await get_core_pool()
    row = await pool.fetchrow(
        """INSERT INTO discord_leak_feed_destinations(
               guild_id,guild_name,channel_id,enabled,configured_by_discord_id
           ) VALUES($1,$2,$3,TRUE,$4)
           ON CONFLICT(guild_id) DO UPDATE SET
               guild_name=EXCLUDED.guild_name,
               channel_id=EXCLUDED.channel_id,
               enabled=TRUE,
               configured_by_discord_id=EXCLUDED.configured_by_discord_id,
               updated_at=NOW()
           RETURNING guild_id,guild_name,channel_id,enabled,configured_by_discord_id,created_at,updated_at""",
        str(guild_id), guild_name, str(channel_id), str(configured_by_discord_id),
    )
    return dict(row)


async def set_enabled(guild_id: str, enabled: bool) -> Optional[dict[str, Any]]:
    pool = await get_core_pool()
    row = await pool.fetchrow(
        """UPDATE discord_leak_feed_destinations
           SET enabled=$2,updated_at=NOW()
           WHERE guild_id=$1
           RETURNING guild_id,guild_name,channel_id,enabled,configured_by_discord_id,created_at,updated_at""",
        str(guild_id), bool(enabled),
    )
    return dict(row) if row else None


async def remove_destination(guild_id: str) -> bool:
    pool = await get_core_pool()
    result = await pool.execute(
        "DELETE FROM discord_leak_feed_destinations WHERE guild_id=$1",
        str(guild_id),
    )
    return result.endswith("1")
