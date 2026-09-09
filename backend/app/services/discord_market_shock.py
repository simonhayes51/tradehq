from __future__ import annotations

"""Temporary FC26 market-shock guard for Discord publishing.

The FC26 endgame market can be invalidated by extreme supply events faster than
historical completed-sales evidence can catch up. During an explicit shock
window we keep collecting and grading market data, keep outcome updates running,
but suppress NEW buy calls from Free/paid/Official Discord feeds.

The pause is deliberately scoped to FC26 and expires automatically. Set
EAFC_MARKET_SHOCK_PAUSE_UNTIL=off to disable immediately, or provide an ISO-8601
UTC timestamp to extend/shorten the window without another deploy.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.services.discord_integration import DiscordClient
from app.services.game_cycle import ingestion_game_year

log = logging.getLogger("discord_market_shock")

DEFAULT_PAUSE_UNTIL = "2026-09-17T00:00:00+00:00"
SHOCK_GAME_YEAR = 26


def _parse_until(raw: str) -> Optional[datetime]:
    value = (raw or "").strip()
    if not value or value.lower() in {"off", "false", "0", "disabled"}:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        log.error("Invalid EAFC_MARKET_SHOCK_PAUSE_UNTIL=%r; failing closed to default", raw)
        parsed = datetime.fromisoformat(DEFAULT_PAUSE_UNTIL)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def pause_until() -> Optional[datetime]:
    return _parse_until(os.getenv("EAFC_MARKET_SHOCK_PAUSE_UNTIL", DEFAULT_PAUSE_UNTIL))


def market_shock_active(now: Optional[datetime] = None) -> bool:
    if ingestion_game_year() != SHOCK_GAME_YEAR:
        return False
    until = pause_until()
    if until is None:
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) < until


def build_market_shock_embed() -> dict:
    until = pause_until()
    until_text = until.strftime("%d %b, %H:%M UTC") if until else "until further notice"
    return {
        "title": "EAFC.Live Market Alert",
        "description": (
            "FC26 supply has moved sharply after the latest repeatable 99-rated content. "
            "We have temporarily paused new trade calls while the market reprices."
        ),
        "color": 0xEAB308,
        "fields": [
            {
                "name": "WHAT THIS MEANS",
                "value": (
                    "EAFC.Live is still collecting prices, completed sales and outcomes. "
                    "We are not going to publish BUY calls against a market moving faster than the evidence."
                ),
                "inline": False,
            },
            {
                "name": "REVIEW WINDOW",
                "value": f"Automatic pause currently runs to **{until_text}** unless lifted earlier.",
                "inline": False,
            },
        ],
        "footer": {"text": "EAFC.Live • No forced trades. No filler calls."},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _publish_alert_once(pool, server, client: Optional[DiscordClient] = None) -> bool:
    from app.services import discord_trading_feed as trading

    if not trading.feed_globally_enabled() or not server.is_postable or not server.feed_channel_id:
        return False
    until = pause_until()
    key_seed = f"fc26_market_shock:{until.isoformat() if until else 'off'}"
    dedup_key = hashlib.sha256(key_seed.encode()).hexdigest()[:40]
    payload = {
        "market_shock": True,
        "game_year": SHOCK_GAME_YEAR,
        "pause_until": until.isoformat() if until else None,
    }
    async with pool.acquire() as conn:
        post_id = await trading._record_post(
            conn, server.id, "product_update", dedup_key, payload
        )
    if post_id is None:
        return False

    client = client or DiscordClient()
    status, body = await client.post_embed(
        server.feed_channel_id,
        build_market_shock_embed(),
        nonce=dedup_key[:25],
        enforce_nonce=True,
    )
    ok = status in (200, 201)
    message_id = body.get("id") if ok and isinstance(body, dict) else None
    async with pool.acquire() as conn:
        await trading._finish_post(
            conn,
            post_id,
            ok=ok,
            message_id=message_id,
            error=None if ok else f"discord_http_{status or 'network'}",
        )
    return ok


def install_discord_market_shock_guard() -> None:
    """Suppress only NEW Discord buy calls while preserving collection/outcomes."""
    from app.services import discord_trading_feed as trading

    if getattr(trading, "_market_shock_guard_installed", False):
        return

    original_live = trading.publish_live_opportunities
    original_daily = trading.publish_daily_overview

    async def guarded_live(pool, server, client=None):
        if market_shock_active():
            await _publish_alert_once(pool, server, client)
            return 0
        return await original_live(pool, server, client)

    async def guarded_daily(pool, server, client=None):
        # Free daily posting is a live Trade of the Day after the Free-feed
        # installer. Official daily proof/overview is historical reporting and
        # remains useful, so only Free is suppressed here.
        if server.access_level == "free" and market_shock_active():
            await _publish_alert_once(pool, server, client)
            return False
        return await original_daily(pool, server, client)

    trading.publish_live_opportunities = guarded_live
    trading.publish_daily_overview = guarded_daily
    trading._market_shock_guard_installed = True
    log.warning(
        "Discord FC26 market-shock guard installed: active=%s until=%s",
        market_shock_active(),
        pause_until(),
    )
