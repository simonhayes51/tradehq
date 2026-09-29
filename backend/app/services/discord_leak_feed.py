from __future__ import annotations

import json
import logging

from typing import Any, Optional

from app.db import get_core_pool
from app.services.discord_integration import DiscordClient
from app.services.discord_permissions import check_bot_channel_access

log = logging.getLogger("discord_leak_feed")


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


async def enqueue_delivery_jobs(conn: Any, tweet_id: str) -> int:
    """Queue one delivery per currently-enabled guild.

    The leak-monitor service deliberately does not need DISCORD_BOT_TOKEN.
    The main API's Discord worker owns the bot token and drains this queue.
    """
    rows = await conn.fetch(
        """INSERT INTO discord_leak_feed_deliveries(tweet_id,guild_id)
           SELECT $1,guild_id
           FROM discord_leak_feed_destinations
           WHERE enabled=TRUE
           ON CONFLICT(tweet_id,guild_id) DO NOTHING
           RETURNING guild_id""",
        str(tweet_id),
    )
    return len(rows)


async def process_pending_deliveries(
    pool: Any,
    client: Optional[DiscordClient] = None,
    *,
    limit: int = 25,
) -> int:
    """Send queued leaks using the main backend's configured Discord bot.

    Channel selection is resolved at send time, so /leaks channel takes effect
    even for a leak that was queued moments before the channel was changed.
    """
    client = client or DiscordClient()

    async with pool.acquire() as conn:
        # Don't leave jobs stuck forever when a destination is removed/paused.
        await conn.execute(
            """UPDATE discord_leak_feed_deliveries d
               SET status='cancelled',updated_at=NOW()
               WHERE d.status IN ('pending','retrying')
                 AND NOT EXISTS (
                     SELECT 1 FROM discord_leak_feed_destinations x
                     WHERE x.guild_id=d.guild_id AND x.enabled=TRUE
                 )"""
        )
        rows = await conn.fetch(
            """SELECT d.tweet_id,d.guild_id,d.attempts,
                      dest.channel_id,
                      t.text,t.tweet_url,t.media_urls,
                      src.username
               FROM discord_leak_feed_deliveries d
               JOIN discord_leak_feed_destinations dest
                 ON dest.guild_id=d.guild_id AND dest.enabled=TRUE
               JOIN leak_tweets t ON t.tweet_id=d.tweet_id
               JOIN leak_sources src ON src.id=t.source_id
               WHERE d.status IN ('pending','retrying')
                 AND (d.next_attempt_at IS NULL OR d.next_attempt_at<=NOW())
               ORDER BY d.created_at
               LIMIT $1""",
            max(1, min(int(limit), 100)),
        )

    sent = 0
    for row in rows:
        media = row["media_urls"] or []
        if isinstance(media, str):
            try:
                media = json.loads(media)
            except json.JSONDecodeError:
                media = []

        embed: dict[str, Any] = {
            "title": "EAFC.Live Leak Feed",
            "url": row["tweet_url"],
            "description": (row["text"] or "New FC27 leak detected.")[:4000],
            "color": 0x22C55E,
            "footer": {"text": f"Source: @{row['username']}"},
        }
        if isinstance(media, list) and media:
            embed["image"] = {"url": str(media[0])}

        channel_id = str(row["channel_id"])
        nonce = f"lk-{str(row['tweet_id'])[-12:]}-{channel_id[-8:]}"[:25]
        status, body = await client.post_embed(
            channel_id,
            embed,
            nonce=nonce,
            enforce_nonce=True,
        )

        async with pool.acquire() as conn:
            if status in (200, 201):
                await conn.execute(
                    """UPDATE discord_leak_feed_deliveries
                       SET status='sent',attempts=attempts+1,sent_at=NOW(),
                           last_error=NULL,next_attempt_at=NULL,updated_at=NOW()
                       WHERE tweet_id=$1 AND guild_id=$2""",
                    str(row["tweet_id"]),
                    str(row["guild_id"]),
                )
                sent += 1
                continue

            attempts = int(row["attempts"] or 0) + 1
            terminal = attempts >= 5 or status in (401, 403, 404)
            error = f"discord_http_{status or 'network'}:{str(body)[:160]}"
            await conn.execute(
                """UPDATE discord_leak_feed_deliveries
                   SET status=$3,attempts=$4,last_error=$5,
                       next_attempt_at=CASE WHEN $3='retrying'
                         THEN NOW() + ($6::int * INTERVAL '1 minute')
                         ELSE NULL END,
                       updated_at=NOW()
                   WHERE tweet_id=$1 AND guild_id=$2""",
                str(row["tweet_id"]),
                str(row["guild_id"]),
                "failed" if terminal else "retrying",
                attempts,
                error,
                min(30, 2 ** attempts),
            )
            log.warning(
                "EAFC.Live leak delivery failed: tweet=%s guild=%s channel=%s status=%s body=%s",
                row["tweet_id"],
                row["guild_id"],
                channel_id,
                status,
                str(body)[:200],
            )

    return sent
