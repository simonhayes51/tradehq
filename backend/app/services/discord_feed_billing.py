from __future__ import annotations

"""
Business logic behind the /feed command group: self-service setup,
Stripe billing, and channel-permission verification for a server owner's
own Paid Partner trading feed.

Everything here is guild-scoped by discord_servers.guild_id (UNIQUE) and
stripe_subscription_id (UNIQUE) - one paid subscription activates exactly
one guild, and re-running /feed setup before payment UPSERTs the same
row rather than creating a duplicate. Posting itself is still governed
by discord_trading_feed.py's DISCORD_TRADING_FEED_ENABLED global switch
and each server's own `status` column - nothing here flips a feed live;
that only happens via an explicit /feed activate after a confirmed test
post and a valid subscription.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import get_core_pool
from app.services.discord_integration import DiscordClient
from app.services.discord_permissions import check_bot_channel_access

log = logging.getLogger("discord_feed_billing")

PLANS: dict[str, dict[str, Any]] = {
    "server_feed": {
        "label": "Server Feed",
        "price_gbp": 9.99,
        "max_posts_per_day": 6,
        "price_env": "STRIPE_SERVER_FEED_PRICE_ID",
    },
    "server_feed_plus": {
        "label": "Server Feed+",
        "price_gbp": 19.99,
        "max_posts_per_day": 12,
        "price_env": "STRIPE_SERVER_FEED_PLUS_PRICE_ID",
    },
}

BUDGET_ALLOCATIONS: dict[str, dict[str, tuple[int, int, int]]] = {
    "server_feed": {
        "balanced": (2, 2, 2),
        "mostly_low": (4, 2, 0),
        "mostly_medium": (1, 4, 1),
        "mostly_high": (0, 2, 4),
    },
    "server_feed_plus": {
        "balanced": (4, 4, 4),
        "mostly_low": (8, 4, 0),
        "mostly_medium": (2, 8, 2),
        "mostly_high": (0, 4, 8),
    },
}

BUDGET_MIX_LABELS = {
    "balanced": "Balanced",
    "mostly_low": "Mostly low-budget",
    "mostly_medium": "Mostly medium-budget",
    "mostly_high": "Mostly high-budget",
}

PLATFORM_SCOPE_LABELS = {"console": "Console", "pc": "PC", "both": "Console + PC"}

# Same price bands app/services/discord_integration.build_opportunity_digest
# already uses for its BUDGET MIX field - reused here rather than invented,
# so "low/medium/high" means the same thing everywhere in this codebase.
BUDGET_BAND_LOW_MAX = 25_000
BUDGET_BAND_MEDIUM_MAX = 100_000

MIN_CONFIDENCE_CHOICES: list[tuple[str, float]] = [
    ("50%", 0.5), ("60%", 0.6), ("70%", 0.7), ("80%", 0.8),
]
MIN_PROFIT_CHOICES: list[tuple[str, int]] = [
    ("1,000 coins", 1000), ("2,500 coins", 2500), ("5,000 coins", 5000),
    ("10,000 coins", 10000), ("25,000 coins", 25000),
]

ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})
BILLABLE_STATUSES = frozenset({"active", "trialing", "past_due"})


class FeedError(Exception):
    """User-facing error with a short, safe message - never a raw
    exception message or a Stripe/Discord ID."""


def classify_budget_band(price: Optional[float]) -> str:
    if price is None:
        return "medium"
    if price < BUDGET_BAND_LOW_MAX:
        return "low"
    if price < BUDGET_BAND_MEDIUM_MAX:
        return "medium"
    return "high"


def budget_allocation_for(plan: str, budget_mix: str) -> tuple[int, int, int]:
    return BUDGET_ALLOCATIONS[plan][budget_mix]


def validate_budget_allocation(plan: str, low: int, medium: int, high: int) -> None:
    if low < 0 or medium < 0 or high < 0:
        raise FeedError("Budget allocations can't be negative.")
    total = low + medium + high
    max_allowed = PLANS[plan]["max_posts_per_day"]
    if total > max_allowed:
        raise FeedError(
            f"That allocation totals {total} posts/day, more than {PLANS[plan]['label']}'s "
            f"{max_allowed}/day allowance. Reduce it or upgrade your plan."
        )


def engine_platform_for(platform_scope: str) -> str:
    """The opportunity engine's candidate scan only filters on a single
    platform value (ps/xbox/pc) - 'console' and 'both' both default to
    'ps' as the primary console market until the engine itself supports
    a combined console filter. The owner's actual preference
    (console/pc/both) is still stored and shown in /feed status."""
    return "pc" if platform_scope == "pc" else "ps"


def _price_id_for(plan: str) -> str:
    env_name = PLANS[plan]["price_env"]
    price_id = os.getenv(env_name)
    if not price_id:
        raise FeedError(
            "Server Feed billing isn't fully configured yet - please contact EAFC.Live support."
        )
    return price_id


async def _audit(conn, server_id: Optional[int], admin_id: str, action: str, details: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO discord_server_audit_log (server_id, admin_id, action, details)
        VALUES ($1, $2, $3, $4::jsonb)
        """,
        server_id, str(admin_id), action, json.dumps(details, default=str),
    )


async def get_guild_feed(guild_id: str) -> Optional[dict[str, Any]]:
    pool = await get_core_pool()
    row = await pool.fetchrow(
        """
        SELECT s.*, st.max_posts_per_day, st.min_confidence, st.min_expected_profit,
               st.cooldown_minutes, st.platform, st.live_opportunities_enabled
        FROM discord_servers s
        LEFT JOIN discord_server_settings st ON st.server_id = s.id
        WHERE s.guild_id = $1
        """,
        guild_id,
    )
    return dict(row) if row else None


async def upsert_pending_setup(
    *, guild_id: str, guild_name: str, owner_discord_id: str,
    plan: str, channel_id: str, platform_scope: str, budget_mix: str,
) -> dict[str, Any]:
    if plan not in PLANS:
        raise FeedError("Unknown plan.")
    if budget_mix not in BUDGET_ALLOCATIONS[plan]:
        raise FeedError("Unknown budget mix.")
    low, medium, high = budget_allocation_for(plan, budget_mix)

    pool = await get_core_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, subscription_status FROM discord_servers WHERE guild_id=$1", guild_id
        )
        if existing and existing["subscription_status"] in BILLABLE_STATUSES:
            raise FeedError(
                "This server already has an active Server Feed subscription. "
                "Use /feed billing to upgrade, downgrade, or manage it."
            )

        row = await conn.fetchrow(
            """
            INSERT INTO discord_servers (
                guild_id, name, access_level, status, owner_discord_id,
                plan, platform_scope, budget_mix, budget_low, budget_medium, budget_high,
                subscription_status
            ) VALUES ($1, $2, 'paid_partner', 'paused', $3, $4, $5, $6, $7, $8, $9,
                      COALESCE((SELECT subscription_status FROM discord_servers WHERE guild_id=$1), 'unpaid'))
            ON CONFLICT (guild_id) DO UPDATE SET
                name = EXCLUDED.name,
                access_level = 'paid_partner',
                owner_discord_id = EXCLUDED.owner_discord_id,
                plan = EXCLUDED.plan,
                platform_scope = EXCLUDED.platform_scope,
                budget_mix = EXCLUDED.budget_mix,
                budget_low = EXCLUDED.budget_low,
                budget_medium = EXCLUDED.budget_medium,
                budget_high = EXCLUDED.budget_high,
                updated_at = NOW()
            RETURNING id
            """,
            guild_id, guild_name, owner_discord_id, plan, platform_scope, budget_mix, low, medium, high,
        )
        server_id = row["id"]
        await conn.execute(
            """
            INSERT INTO discord_server_settings (server_id, max_posts_per_day, platform)
            VALUES ($1, $2, $3)
            ON CONFLICT (server_id) DO UPDATE SET
                max_posts_per_day = EXCLUDED.max_posts_per_day,
                platform = EXCLUDED.platform,
                updated_at = NOW()
            """,
            server_id, PLANS[plan]["max_posts_per_day"], engine_platform_for(platform_scope),
        )
        await conn.execute(
            "UPDATE discord_servers SET feed_channel_id=$2, updated_at=NOW() WHERE id=$1",
            server_id, channel_id,
        )
        await _audit(conn, server_id, owner_discord_id, "feed_setup", {
            "plan": plan, "channel_id": channel_id, "platform_scope": platform_scope,
            "budget_mix": budget_mix, "budget": [low, medium, high],
        })
        return await get_guild_feed(guild_id)


async def create_checkout_session(*, guild_id: str, owner_discord_id: str, plan: str, success_url: str, cancel_url: str) -> str:
    import stripe

    price_id = _price_id_for(plan)
    session = await asyncio.to_thread(
        stripe.checkout.Session.create,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=guild_id,
        # Without this, Stripe Checkout shows no promo-code field at all -
        # a customer can never apply a Coupon/Promotion Code, regardless
        # of anything configured in the Dashboard.
        allow_promotion_codes=True,
        metadata={"kind": "discord_feed", "guild_id": guild_id, "discord_user_id": str(owner_discord_id), "plan": plan},
        subscription_data={
            "metadata": {"kind": "discord_feed", "guild_id": guild_id, "discord_user_id": str(owner_discord_id), "plan": plan},
        },
    )
    pool = await get_core_pool()
    await pool.execute(
        "UPDATE discord_servers SET stripe_checkout_session_id=$2, subscription_status='checkout_pending', updated_at=NOW() WHERE guild_id=$1",
        guild_id, session.id,
    )
    return session.url


async def create_billing_portal_session(*, guild_id: str, return_url: str) -> str:
    import stripe

    feed = await get_guild_feed(guild_id)
    if not feed or not feed.get("stripe_customer_id"):
        raise FeedError("No billing account found for this server yet - run /feed setup first.")
    session = await asyncio.to_thread(
        stripe.billing_portal.Session.create,
        customer=feed["stripe_customer_id"],
        return_url=return_url,
    )
    return session.url


async def confirm_channel_access(guild_id: str, channel_id: str) -> tuple[bool, Optional[str]]:
    client = DiscordClient()
    ok, reason = await check_bot_channel_access(client, guild_id, channel_id)
    if reason == "bot_not_in_guild":
        pool = await get_core_pool()
        await pool.execute(
            "UPDATE discord_servers SET bot_removed_at=NOW(), status='paused', updated_at=NOW() WHERE guild_id=$1",
            guild_id,
        )
    return ok, reason


CHANNEL_ACCESS_MESSAGES = {
    "channel_not_found": "I can't find that channel - please check it still exists.",
    "invalid_channel_type": "Please pick a text or announcement channel.",
    "bot_not_in_guild": "It looks like I'm no longer a member of this server - please re-invite the bot.",
    "missing_permissions": "I need View Channel, Send Messages, and Embed Links in that channel.",
    "discord_unavailable": "Discord didn't respond - please try again shortly.",
}


async def set_channel(*, guild_id: str, admin_id: str, channel_id: str) -> dict[str, Any]:
    feed = await get_guild_feed(guild_id)
    if not feed:
        raise FeedError("Run /feed setup first.")
    ok, reason = await confirm_channel_access(guild_id, channel_id)
    if not ok:
        raise FeedError(CHANNEL_ACCESS_MESSAGES.get(reason, "Couldn't verify that channel."))
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE discord_servers SET feed_channel_id=$2, updated_at=NOW() WHERE guild_id=$1",
            guild_id, channel_id,
        )
        await _audit(conn, feed["id"], admin_id, "feed_channel_changed", {"channel_id": channel_id})
    return await get_guild_feed(guild_id)


def validate_posting_window(
    start_hour: Optional[int], end_hour: Optional[int], tz_name: Optional[str]
) -> None:
    """Both hours must be supplied together (or neither), 0-23, and the
    timezone must be a real IANA name. Raises FeedError on anything else."""
    if start_hour is None and end_hour is None:
        return
    if start_hour is None or end_hour is None:
        raise FeedError("Please set both a start hour and an end hour for the posting window.")
    if not (0 <= start_hour <= 23) or not (0 <= end_hour <= 23):
        raise FeedError("Posting window hours must be between 0 and 23.")
    if tz_name:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise FeedError(f"'{tz_name}' isn't a recognised timezone (e.g. 'Europe/London', 'America/New_York').")


async def set_preferences(
    *, guild_id: str, admin_id: str,
    platform_scope: Optional[str] = None, budget_mix: Optional[str] = None,
    min_confidence: Optional[float] = None, min_expected_profit: Optional[int] = None,
    posting_window_start_hour: Optional[int] = None, posting_window_end_hour: Optional[int] = None,
    posting_timezone: Optional[str] = None, clear_posting_window: bool = False,
) -> dict[str, Any]:
    feed = await get_guild_feed(guild_id)
    if not feed:
        raise FeedError("Run /feed setup first.")
    plan = feed["plan"]
    new_budget_mix = budget_mix or feed["budget_mix"]
    low, medium, high = budget_allocation_for(plan, new_budget_mix)
    validate_budget_allocation(plan, low, medium, high)

    if min_confidence is not None and min_confidence not in dict(MIN_CONFIDENCE_CHOICES).values():
        raise FeedError("Please choose one of the offered confidence levels.")
    if min_expected_profit is not None and min_expected_profit not in dict(MIN_PROFIT_CHOICES).values():
        raise FeedError("Please choose one of the offered profit thresholds.")
    if clear_posting_window:
        posting_window_start_hour = posting_window_end_hour = posting_timezone = None
    else:
        validate_posting_window(posting_window_start_hour, posting_window_end_hour, posting_timezone)

    pool = await get_core_pool()
    async with pool.acquire() as conn:
        if clear_posting_window:
            await conn.execute(
                """
                UPDATE discord_servers SET
                    platform_scope = COALESCE($2, platform_scope),
                    budget_mix = $3, budget_low=$4, budget_medium=$5, budget_high=$6,
                    posting_window_start_hour = NULL, posting_window_end_hour = NULL, posting_timezone = NULL,
                    updated_at=NOW()
                WHERE guild_id=$1
                """,
                guild_id, platform_scope, new_budget_mix, low, medium, high,
            )
        elif posting_window_start_hour is not None:
            await conn.execute(
                """
                UPDATE discord_servers SET
                    platform_scope = COALESCE($2, platform_scope),
                    budget_mix = $3, budget_low=$4, budget_medium=$5, budget_high=$6,
                    posting_window_start_hour = $7, posting_window_end_hour = $8,
                    posting_timezone = COALESCE($9, posting_timezone, 'UTC'),
                    updated_at=NOW()
                WHERE guild_id=$1
                """,
                guild_id, platform_scope, new_budget_mix, low, medium, high,
                posting_window_start_hour, posting_window_end_hour, posting_timezone,
            )
        else:
            await conn.execute(
                """
                UPDATE discord_servers SET
                    platform_scope = COALESCE($2, platform_scope),
                    budget_mix = $3, budget_low=$4, budget_medium=$5, budget_high=$6,
                    updated_at=NOW()
                WHERE guild_id=$1
                """,
                guild_id, platform_scope, new_budget_mix, low, medium, high,
            )
        await conn.execute(
            """
            UPDATE discord_server_settings SET
                platform = COALESCE($2, platform),
                min_confidence = COALESCE($3, min_confidence),
                min_expected_profit = COALESCE($4, min_expected_profit),
                updated_at = NOW()
            WHERE server_id = $1
            """,
            feed["id"],
            engine_platform_for(platform_scope) if platform_scope else None,
            min_confidence, min_expected_profit,
        )
        await _audit(conn, feed["id"], admin_id, "feed_preferences_changed", {
            "platform_scope": platform_scope, "budget_mix": new_budget_mix,
            "min_confidence": min_confidence, "min_expected_profit": min_expected_profit,
            "posting_window_start_hour": posting_window_start_hour,
            "posting_window_end_hour": posting_window_end_hour,
            "posting_timezone": posting_timezone, "clear_posting_window": clear_posting_window,
        })
    return await get_guild_feed(guild_id)


async def activate(*, guild_id: str, admin_id: str) -> dict[str, Any]:
    feed = await get_guild_feed(guild_id)
    if not feed:
        raise FeedError("Run /feed setup first.")
    if feed["subscription_status"] not in ACTIVE_SUBSCRIPTION_STATUSES:
        raise FeedError("This server needs an active Server Feed subscription first - run /feed setup or /feed billing.")
    if not feed["feed_channel_id"]:
        raise FeedError("Set a posting channel first with /feed setup or /feed channel.")
    ok, reason = await confirm_channel_access(guild_id, feed["feed_channel_id"])
    if not ok:
        raise FeedError(CHANNEL_ACCESS_MESSAGES.get(reason, "Couldn't verify the posting channel."))
    if not feed.get("test_post_confirmed_at"):
        raise FeedError("Run /feed test first to confirm a synthetic post works, then try again.")

    pool = await get_core_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE discord_servers SET status='active',
                   enabled_by_admin_id=$2, enabled_at=NOW(), bot_removed_at=NULL, updated_at=NOW()
            WHERE guild_id=$1
            """,
            guild_id, str(admin_id),
        )
        await conn.execute(
            "UPDATE discord_server_settings SET live_opportunities_enabled=TRUE, updated_at=NOW() WHERE server_id=$1",
            feed["id"],
        )
        await _audit(conn, feed["id"], admin_id, "feed_activated", {})
    return await get_guild_feed(guild_id)


async def pause(*, guild_id: str, admin_id: str) -> dict[str, Any]:
    feed = await get_guild_feed(guild_id)
    if not feed:
        raise FeedError("Run /feed setup first.")
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE discord_servers SET status='paused', updated_at=NOW() WHERE guild_id=$1", guild_id)
        await _audit(conn, feed["id"], admin_id, "feed_paused", {})
    return await get_guild_feed(guild_id)


async def resume(*, guild_id: str, admin_id: str) -> dict[str, Any]:
    feed = await get_guild_feed(guild_id)
    if not feed:
        raise FeedError("Run /feed setup first.")
    if feed["subscription_status"] not in ACTIVE_SUBSCRIPTION_STATUSES:
        raise FeedError("This server's Server Feed subscription isn't active - check /feed billing.")
    if not feed["feed_channel_id"]:
        raise FeedError("Set a posting channel first.")
    ok, reason = await confirm_channel_access(guild_id, feed["feed_channel_id"])
    if not ok:
        raise FeedError(CHANNEL_ACCESS_MESSAGES.get(reason, "Couldn't verify the posting channel."))
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE discord_servers SET status='active', bot_removed_at=NULL, updated_at=NOW() WHERE guild_id=$1",
            guild_id,
        )
        await _audit(conn, feed["id"], admin_id, "feed_resumed", {})
    return await get_guild_feed(guild_id)


async def mark_test_post_confirmed(guild_id: str) -> None:
    pool = await get_core_pool()
    await pool.execute(
        "UPDATE discord_servers SET test_post_confirmed_at=NOW(), updated_at=NOW() WHERE guild_id=$1", guild_id
    )


async def posts_used_today(server_id: int) -> int:
    pool = await get_core_pool()
    return int(await pool.fetchval(
        "SELECT COUNT(*) FROM discord_server_posts WHERE server_id=$1 AND kind='opportunity' AND status='sent' AND sent_at > NOW() - INTERVAL '24 hours'",
        server_id,
    ))


# ---------------------------------------------------------------------------
# Stripe webhook handlers - called from main.py's existing /api/webhooks/stripe
# dispatcher, branched on metadata.kind == 'discord_feed' before falling
# through to the per-user consumer-subscription handlers already there.
# ---------------------------------------------------------------------------


def is_discord_feed_object(obj: dict[str, Any]) -> bool:
    return (obj.get("metadata") or {}).get("kind") == "discord_feed"


async def _resolve_guild_id(conn, subscription: dict[str, Any]) -> Optional[str]:
    guild_id = (subscription.get("metadata") or {}).get("guild_id")
    if guild_id:
        return guild_id
    return await conn.fetchval(
        "SELECT guild_id FROM discord_servers WHERE stripe_subscription_id=$1", subscription.get("id")
    )


def _subscription_period_end(subscription: dict[str, Any]) -> Optional[datetime]:
    """current_period_end/start moved off the top-level Subscription
    object onto each subscription item in newer Stripe API versions
    (confirmed via a real webhook payload on API version
    2025-08-27.basil: absent on the subscription itself, present at
    items.data[0].current_period_end) - fall back to the first item's
    value so /feed status's PAID THROUGH date actually populates."""
    raw = subscription.get("current_period_end")
    if raw is None:
        items = (subscription.get("items") or {}).get("data") or []
        if items:
            raw = items[0].get("current_period_end")
    return datetime.fromtimestamp(raw, tz=timezone.utc) if raw else None


async def handle_discord_feed_checkout_completed(session: dict[str, Any], conn) -> None:
    guild_id = (session.get("metadata") or {}).get("guild_id") or session.get("client_reference_id")
    if not guild_id:
        return
    await conn.execute(
        "UPDATE discord_servers SET stripe_customer_id=$2, updated_at=NOW() WHERE guild_id=$1",
        guild_id, session.get("customer"),
    )


async def handle_discord_feed_subscription_created(subscription: dict[str, Any], conn) -> None:
    guild_id = await _resolve_guild_id(conn, subscription)
    if not guild_id:
        log.warning("discord_feed subscription.created with no resolvable guild_id: %s", subscription.get("id"))
        return
    period_end = _subscription_period_end(subscription)
    await conn.execute(
        """
        UPDATE discord_servers SET
            stripe_subscription_id=$2, stripe_customer_id=$3,
            subscription_status=$4, current_period_end=$5, updated_at=NOW()
        WHERE guild_id=$1
        """,
        guild_id, subscription.get("id"), subscription.get("customer"),
        subscription.get("status"), period_end,
    )


async def handle_discord_feed_subscription_updated(subscription: dict[str, Any], conn) -> None:
    guild_id = await _resolve_guild_id(conn, subscription)
    if not guild_id:
        return
    period_end = _subscription_period_end(subscription)
    status = subscription.get("status")
    await conn.execute(
        """
        UPDATE discord_servers SET
            subscription_status=$2, current_period_end=$3, updated_at=NOW(),
            status = CASE WHEN $2 NOT IN ('active','trialing','past_due') THEN 'paused' ELSE status END
        WHERE guild_id=$1
        """,
        guild_id, status, period_end,
    )


async def handle_discord_feed_subscription_deleted(subscription: dict[str, Any], conn) -> None:
    guild_id = await _resolve_guild_id(conn, subscription)
    if not guild_id:
        return
    await conn.execute(
        """
        UPDATE discord_servers SET subscription_status='canceled', status='paused', updated_at=NOW()
        WHERE guild_id=$1
        """,
        guild_id,
    )
