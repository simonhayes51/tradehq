from __future__ import annotations

"""Permanent Free Discord Server Feed.

Free guilds receive one canonical EAFC.Live Trade of the Day. The candidate is
selected once per London content day, frozen in the database, then the exact same
embed is broadcast to every active Free server. Paid Server Feed products and the
Official server keep their existing selectors, posting policy and billing paths.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import asyncpg

from app.db import get_core_pool
from app.services.discord_daily_proof import DailyProofSchedule
from app.services.discord_integration import DiscordClient
from app.services import discord_embeds as embeds

log = logging.getLogger("discord_free_server_feed")

FREE_PLAN = "free"
FREE_MAX_POSTS_PER_DAY = 1
FREE_MAX_EVIDENCE_AGE_MINUTES = int(
    os.getenv("DISCORD_FREE_TRADE_MAX_EVIDENCE_AGE_MINUTES", "360")
)
FREE_CURRENT_INTELLIGENCE_MAX_AGE_MINUTES = int(
    os.getenv("DISCORD_FREE_TRADE_INTELLIGENCE_MAX_AGE_MINUTES", "30")
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coins(value: Any) -> str:
    return f"{Decimal(str(value)):,.0f}"


def _percent(value: Any) -> str:
    return f"{Decimal(str(value)) * Decimal('100'):.1f}%"


def _broadcast_date(now: Optional[datetime] = None):
    """Open today's broadcast at the established daily-feed London boundary."""
    now = now or _now()
    schedule = DailyProofSchedule.from_env()
    local_now = now.astimezone(schedule.zone)
    boundary = local_now.replace(
        hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0
    )
    if local_now < boundary:
        return None
    return local_now.date()


async def setup_free_feed(
    *, guild_id: str, guild_name: str, owner_discord_id: str, channel_id: str
) -> dict[str, Any]:
    """Activate one free feed for a guild without creating any Stripe object."""
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id, access_level, subscription_status "
                "FROM discord_servers WHERE guild_id=$1 FOR UPDATE",
                guild_id,
            )
            if existing and existing["access_level"] == "official":
                raise ValueError(
                    "The official EAFC.Live server cannot be converted to the Free Server Feed."
                )
            if existing and str(existing["subscription_status"] or "") in {
                "active", "trialing", "past_due"
            }:
                raise ValueError(
                    "This server already has a paid Server Feed. Use `/feed status` or `/feed billing` instead."
                )

            row = await conn.fetchrow(
                """
                INSERT INTO discord_servers (
                    guild_id, name, access_level, feed_channel_id, status,
                    enabled_by_admin_id, enabled_at, owner_discord_id,
                    plan, platform_scope, budget_mix, budget_low, budget_medium,
                    budget_high, subscription_status, current_period_end, expires_at,
                    stripe_subscription_id, stripe_checkout_session_id, updated_at
                ) VALUES (
                    $1,$2,'free',$3,'active',$4,NOW(),$4,
                    NULL,NULL,NULL,NULL,NULL,NULL,'free',NULL,NULL,NULL,NULL,NOW()
                )
                ON CONFLICT (guild_id) DO UPDATE SET
                    name=EXCLUDED.name,
                    access_level='free',
                    feed_channel_id=EXCLUDED.feed_channel_id,
                    status='active',
                    enabled_by_admin_id=EXCLUDED.enabled_by_admin_id,
                    enabled_at=NOW(),
                    owner_discord_id=EXCLUDED.owner_discord_id,
                    plan=NULL,
                    platform_scope=NULL,
                    budget_mix=NULL,
                    budget_low=NULL,
                    budget_medium=NULL,
                    budget_high=NULL,
                    subscription_status='free',
                    current_period_end=NULL,
                    expires_at=NULL,
                    stripe_subscription_id=NULL,
                    stripe_checkout_session_id=NULL,
                    bot_removed_at=NULL,
                    updated_at=NOW()
                RETURNING id, guild_id, name, access_level, feed_channel_id, status,
                          owner_discord_id, plan, subscription_status
                """,
                guild_id,
                guild_name,
                channel_id,
                owner_discord_id,
            )
            await conn.execute(
                """
                INSERT INTO discord_server_settings (
                    server_id, max_posts_per_day, daily_overview_enabled,
                    live_opportunities_enabled, updated_at
                ) VALUES ($1,1,TRUE,FALSE,NOW())
                ON CONFLICT (server_id) DO UPDATE SET
                    max_posts_per_day=1,
                    daily_overview_enabled=TRUE,
                    live_opportunities_enabled=FALSE,
                    updated_at=NOW()
                """,
                row["id"],
            )
            await conn.execute(
                """
                INSERT INTO discord_server_audit_log (server_id, admin_id, action, details)
                VALUES ($1,$2,'free_feed_activated',$3::jsonb)
                """,
                row["id"],
                owner_discord_id,
                json.dumps({"channel_id": channel_id, "max_posts_per_day": 1}),
            )
            return dict(row)


async def set_free_feed_status(
    *, guild_id: str, admin_id: str, active: bool
) -> None:
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE discord_servers
            SET status=$2, updated_at=NOW()
            WHERE guild_id=$1 AND access_level='free'
            RETURNING id
            """,
            guild_id,
            "active" if active else "paused",
        )
        if not row:
            raise ValueError("This server does not have a Free Server Feed configured.")
        await conn.execute(
            "INSERT INTO discord_server_audit_log "
            "(server_id, admin_id, action, details) VALUES ($1,$2,$3,'{}'::jsonb)",
            row["id"],
            admin_id,
            "free_feed_resumed" if active else "free_feed_paused",
        )


async def free_posts_used_today(server_id: int) -> int:
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*) FROM discord_server_posts
                WHERE server_id=$1 AND kind='daily_overview' AND status='sent'
                  AND sent_at > NOW() - INTERVAL '24 hours'
                """,
                server_id,
            )
            or 0
        )


def _trade_embed(row: dict[str, Any]) -> dict[str, Any]:
    name = row.get("name") or "Unknown player"
    rating = row.get("rating")
    rarity = row.get("rarity") or ""
    label = " ".join(str(x) for x in (rating, rarity) if x not in (None, ""))
    card_id = int(row["source_card_id"])
    app_url = (os.getenv("FRONTEND_URL") or "https://app.eafc.live").rstrip("/")
    embed: dict[str, Any] = {
        "title": "EAFC.Live Trade of the Day",
        "url": f"{app_url}/#/players/{card_id}",
        "description": f"**{name}**" + (f" — {label}" if label else ""),
        "color": 0x22C55E,
        "fields": [
            {
                "name": "BUY NOW",
                "value": f"**{_coins(row['current_executable_buy'])}** coins",
                "inline": True,
            },
            {
                "name": "SELL TARGET",
                "value": f"**{_coins(row['recommended_sell_target'])}** coins",
                "inline": True,
            },
            {
                "name": "EXPECTED PROFIT",
                "value": f"**+{_coins(row['expected_profit_after_tax'])}** coins after tax",
                "inline": True,
            },
            {
                "name": "EXPECTED ROI",
                "value": _percent(row["expected_roi"]),
                "inline": True,
            },
            {
                "name": "CONFIDENCE",
                "value": _percent(row["confidence_score"]),
                "inline": True,
            },
            {
                "name": "EVIDENCE",
                "value": "Confirmed completed sales",
                "inline": True,
            },
        ],
        "footer": {
            "text": (
                "FREE SERVER FEED • 1 shared trade/day • "
                "Upgrade for up to 6 or 15 server-specific posts/day"
            )
        },
        "timestamp": row["evaluated_at"].isoformat(),
    }
    image = row.get("card_image_stored_url") or row.get("card_image_url")
    if image:
        embed["thumbnail"] = {"url": image}
    return embed


def _trade_payload(row: dict[str, Any], trade_date) -> dict[str, Any]:
    """Build a trackable opportunity payload for the shared free trade."""
    return {
        "source_card_id": int(row["source_card_id"]),
        "name": row.get("name"),
        "trade_date": trade_date.isoformat(),
        "free_trade_of_day": True,
        "signal": row["signal"],
        # These are deliberately the canonical opportunity keys consumed by
        # publish_opportunity_updates(). Free gets one call, but still gets an
        # honest result for that call.
        "buy_price": str(row["current_executable_buy"]),
        "sell_target": str(row["recommended_sell_target"]),
        "expected_profit_after_tax": str(row["expected_profit_after_tax"]),
        "expected_roi": str(row["expected_roi"]),
        "confidence_score": float(row["confidence_score"]),
        "evidence_age_minutes": int(row["evidence_age_minutes"]),
    }


async def _select_candidate(conn) -> Optional[dict[str, Any]]:
    row = await conn.fetchrow(
        """
        SELECT
            c.source_card_id,
            COALESCE(m.display_name, m.name) AS name,
            m.rating,
            m.rarity,
            m.card_image_stored_url,
            m.card_image_url,
            c.signal,
            c.current_executable_buy,
            c.recommended_sell_target,
            c.expected_profit_after_tax,
            c.expected_roi,
            c.confidence_score,
            c.liquidity_score,
            c.evidence_age_minutes,
            c.evaluated_at
        FROM futgg_current_intelligence c
        JOIN futgg_market_snapshot m ON m.source_card_id=c.source_card_id AND m.game_year=c.game_year
        WHERE c.signal IN ('strong_buy','buy')
          AND c.status NOT IN ('expired','invalidated','insufficient_data')
          AND (c.expires_at IS NULL OR c.expires_at > NOW())
          AND c.evaluated_at >= NOW() - ($1::int * INTERVAL '1 minute')
          AND c.valuation_source='completed_sales'
          AND c.evidence_quality='confirmed'
          AND c.evidence_age_minutes IS NOT NULL
          AND c.evidence_age_minutes <= $2
          AND COALESCE(c.sales_sample_size,0) >= 3
          AND COALESCE(c.liquidity_score,0) >= 0.30
          AND c.current_executable_buy IS NOT NULL
          AND c.recommended_sell_target IS NOT NULL
          AND c.expected_profit_after_tax IS NOT NULL
          AND c.expected_roi IS NOT NULL
          AND c.confidence_score IS NOT NULL
          AND m.current_bin = c.current_bin
          AND m.is_tradeable IS DISTINCT FROM FALSE
          AND c.expected_profit_after_tax >= GREATEST(
              300, c.current_executable_buy * 0.015
          )
        ORDER BY
            CASE c.signal WHEN 'strong_buy' THEN 0 ELSE 1 END,
            c.expected_profit_after_tax DESC,
            c.confidence_score DESC,
            c.expected_roi DESC,
            c.source_card_id
        LIMIT 1
        """,
        FREE_CURRENT_INTELLIGENCE_MAX_AGE_MINUTES,
        FREE_MAX_EVIDENCE_AGE_MINUTES,
    )
    return dict(row) if row else None


async def _canonical_trade(conn, trade_date) -> Optional[dict[str, Any]]:
    existing = await conn.fetchrow(
        "SELECT source_card_id, payload, embed "
        "FROM discord_free_trade_of_day WHERE trade_date=$1",
        trade_date,
    )
    if existing:
        return dict(existing)

    candidate = await _select_candidate(conn)
    if not candidate:
        return None
    embed = _trade_embed(candidate)
    payload = _trade_payload(candidate, trade_date)
    await conn.execute(
        """
        INSERT INTO discord_free_trade_of_day (trade_date, source_card_id, payload, embed)
        VALUES ($1,$2,$3::jsonb,$4::jsonb)
        ON CONFLICT (trade_date) DO NOTHING
        """,
        trade_date,
        int(candidate["source_card_id"]),
        json.dumps(payload),
        json.dumps(embed),
    )
    row = await conn.fetchrow(
        "SELECT source_card_id, payload, embed "
        "FROM discord_free_trade_of_day WHERE trade_date=$1",
        trade_date,
    )
    return dict(row) if row else None


async def publish_free_trade_of_day(
    pool, server, client: Optional[DiscordClient] = None
) -> bool:
    """Publish the frozen daily opportunity to one Free guild."""
    from app.services import discord_trading_feed as trading

    if server.access_level != "free":
        return False
    if (
        not trading.feed_globally_enabled()
        or not server.is_postable
        or not server.feed_channel_id
    ):
        return False
    trade_date = _broadcast_date()
    if trade_date is None:
        return False
    client = client or DiscordClient()

    try:
        async with pool.acquire() as conn:
            if (
                await trading._posts_sent_today(conn, server.id, "daily_overview")
                >= FREE_MAX_POSTS_PER_DAY
            ):
                return False
            trade = await _canonical_trade(conn, trade_date)
            if not trade:
                return False
            payload = trade["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            embed = trade["embed"]
            if isinstance(embed, str):
                embed = json.loads(embed)
            dedup_key = hashlib.sha256(
                f"free_trade_of_day:{trade_date.isoformat()}:{trade['source_card_id']}".encode()
            ).hexdigest()[:40]
            post_id = await trading._record_post(
                conn, server.id, "daily_overview", dedup_key, payload
            )
        if post_id is None:
            return False

        app_url = os.getenv("FRONTEND_URL", "https://app.eafc.live")
        components = [embeds.track_trade_row(
            card_id=int(payload["source_card_id"]),
            buy_price=int(Decimal(str(payload["buy_price"]))),
            sell_target=int(Decimal(str(payload["sell_target"]))),
            platform="ps",
            url=f"{app_url.rstrip('/')}/#/players/{payload['source_card_id']}?src=discord_feed",
        )]
        status, body = await client.post_embed(
            server.feed_channel_id,
            embed,
            nonce=dedup_key[:25],
            enforce_nonce=True,
            components=components,
        )
        ok = status in (200, 201)
        message_id = body.get("id") if ok and isinstance(body, dict) else None
        error = None if ok else f"discord_http_{status}"
        async with pool.acquire() as conn:
            await trading._finish_post(
                conn,
                post_id,
                ok=ok,
                message_id=message_id,
                error=error,
            )
        return ok
    except asyncpg.UndefinedTableError:
        log.warning(
            "Free Trade of the Day table is not migrated yet; skipping broadcast"
        )
        return False
    except Exception:
        log.warning(
            "Free Trade of the Day publish failed for guild=%s",
            server.guild_id,
            exc_info=True,
        )
        return False


async def _free_status(guild_id: str) -> dict[str, Any]:
    from app.services import discord_feed_billing as billing

    feed = await billing.get_guild_feed(guild_id)
    if not feed:
        return {"content": "This server hasn't run `/feed setup` yet."}
    used = await free_posts_used_today(int(feed["id"]))
    state = "active" if feed.get("status") == "active" else "paused"
    return {
        "content": (
            f"🆓 **Free Server Feed: {state}**\n"
            f"Channel: <#{feed['feed_channel_id']}>\n"
            f"Trade of the Day: **{used}/1** posted in the last 24h\n"
            "Every Free server receives the same EAFC.Live Trade of the Day. "
            "Paid Server Feed plans unlock server-specific feeds with up to 6 or 15 posts/day."
        )
    }


def install_free_server_feed() -> None:
    """Install Free setup/commands and route only Free daily posts to it."""
    from app.routers import discord_interactions as interactions
    from app.services import discord_feed_billing as billing
    from app.services import discord_trading_feed as trading

    if getattr(interactions, "_free_server_feed_installed", False):
        return

    original_reply = interactions._build_feed_reply
    original_daily_overview = trading.publish_daily_overview

    async def routed_daily_overview(pool, server, client=None):
        if server.access_level == "free":
            return await publish_free_trade_of_day(pool, server, client)
        return await original_daily_overview(pool, server, client)

    trading.publish_daily_overview = routed_daily_overview

    async def wrapped_reply(interaction_payload, options, discord_id):
        if not options:
            return await original_reply(interaction_payload, options, discord_id)
        sub = options[0]
        sub_name = str(sub.get("name") or "")
        sub_options = sub.get("options") or []
        plan_raw = interactions._sub_option(sub_options, "plan")
        requested_plan = str(plan_raw) if plan_raw is not None else FREE_PLAN

        if sub_name == "setup" and requested_plan != FREE_PLAN:
            if (
                interactions._sub_option(sub_options, "platform") is None
                or interactions._sub_option(sub_options, "budget_mix") is None
            ):
                return {
                    "content": "Paid Server Feed setup also needs `platform` and `budget_mix`."
                }
            return await original_reply(interaction_payload, options, discord_id)

        if sub_name == "help":
            return {
                "content": (
                    "**EAFC.Live Server Feed**\n"
                    "🆓 Free: 1 shared Trade of the Day, no card or subscription required.\n"
                    "💚 Server Feed: £9.99/month, up to 6 server-specific posts/day.\n"
                    "⚡ Server Feed+: £19.99/month, up to 15 server-specific posts/day.\n\n"
                    "Run `/feed setup` with just a channel for Free, or choose a paid "
                    "plan plus platform and budget mix."
                )
            }

        if sub_name == "setup" and requested_plan == FREE_PLAN:
            if interactions.perms.is_dm_interaction(interaction_payload):
                return {"embeds": [interactions.embeds.feed_dm_not_allowed_embed()]}
            if not interactions.perms.has_manage_guild(interaction_payload):
                return {"embeds": [interactions.embeds.feed_permission_denied_embed()]}
            guild_id = str(interaction_payload["guild_id"])
            guild_name = (
                (interaction_payload.get("guild") or {}).get("name")
                or f"Guild {guild_id}"
            )
            channel_raw = interactions._sub_option(sub_options, "channel")
            if channel_raw is None:
                return {
                    "content": "Choose the channel where the daily free trade should be posted."
                }
            channel_id = str(channel_raw)
            ok, reason = await billing.confirm_channel_access(guild_id, channel_id)
            if not ok:
                return {
                    "content": billing.CHANNEL_ACCESS_MESSAGES.get(
                        reason, "Couldn't verify that channel."
                    )
                }
            try:
                await setup_free_feed(
                    guild_id=guild_id,
                    guild_name=guild_name,
                    owner_discord_id=str(discord_id),
                    channel_id=channel_id,
                )
            except ValueError as exc:
                return {"content": str(exc)}
            return {
                "content": (
                    f"✅ **Free Server Feed is active** in <#{channel_id}>.\n"
                    "You'll get the same EAFC.Live **Trade of the Day** as every "
                    "other Free server, up to 1 post/day. If no trade clears our "
                    "quality bar, we post nothing.\n"
                    "No card, trial or subscription has been created."
                )
            }

        if (
            interactions.perms.is_dm_interaction(interaction_payload)
            or not interaction_payload.get("guild_id")
        ):
            return await original_reply(interaction_payload, options, discord_id)
        guild_id = str(interaction_payload["guild_id"])
        feed = await billing.get_guild_feed(guild_id)
        if not feed or feed.get("access_level") != "free":
            return await original_reply(interaction_payload, options, discord_id)
        if not interactions.perms.has_manage_guild(interaction_payload):
            return {"embeds": [interactions.embeds.feed_permission_denied_embed()]}

        if sub_name == "status":
            return await _free_status(guild_id)
        if sub_name == "activate":
            return {
                "content": (
                    "✅ The Free Server Feed is already active. No payment or activation step is needed."
                )
            }
        if sub_name == "sell_channel":
            # Result routing is basic feed configuration, not a paid feed
            # preference. Let the canonical command handler validate access,
            # persist/clear the channel, and return its normal confirmation.
            return await original_reply(interaction_payload, options, discord_id)
        if sub_name == "preferences":
            return {
                "content": (
                    "Free uses the shared EAFC.Live Trade of the Day. "
                    "Server-specific feed preferences are available on paid "
                    "Server Feed plans."
                )
            }
        if sub_name == "billing":
            return {
                "content": (
                    "There is no billing on Free. To upgrade, run `/feed setup` "
                    "again and choose Server Feed or Server Feed+."
                )
            }
        if sub_name == "pause":
            await set_free_feed_status(
                guild_id=guild_id, admin_id=str(discord_id), active=False
            )
            return {
                "content": "⏸️ Free Server Feed paused. There is no subscription or billing to change."
            }
        if sub_name == "resume":
            await set_free_feed_status(
                guild_id=guild_id, admin_id=str(discord_id), active=True
            )
            return {
                "content": f"▶️ Free Server Feed resumed in <#{feed['feed_channel_id']}>."
            }
        if sub_name == "test":
            server = trading.ServerRecord(
                id=feed["id"],
                guild_id=guild_id,
                name=(interaction_payload.get("guild") or {}).get("name")
                or f"Guild {guild_id}",
                access_level="free",
                feed_channel_id=feed["feed_channel_id"],
                proof_channel_id=None,
                status=feed["status"],
                expires_at=None,
            )
            ok, error = await trading.send_test_post(await get_core_pool(), server)
            if not ok:
                return {"content": f"Test post failed: {error or 'unknown error'}"}
            return {
                "content": (
                    f"✅ Sent a synthetic test post to <#{feed['feed_channel_id']}>. "
                    "Your Free feed is already active."
                )
            }

        return await original_reply(interaction_payload, options, discord_id)

    interactions._build_feed_reply = wrapped_reply
    interactions._free_server_feed_installed = True
    log.info(
        "installed permanent Free Discord Server Feed (1 shared Trade of the Day/day)"
    )
