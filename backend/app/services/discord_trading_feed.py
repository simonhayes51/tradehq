from __future__ import annotations

"""
Multi-server Discord trading feed.

Adds three access levels on top of the existing single-guild Discord
integration (app/services/discord_integration.py, unchanged):

  - free           one automatic daily overview (aggregate stats only,
                    never individual live opportunities)
  - paid_partner    live opportunities posted into a chosen channel, with
                    per-server posting policy (discord_server_settings),
                    manually enabled by an EAFC.Live admin with an expiry
  - official        the existing Official server, now managed as a row in
                    discord_servers instead of pure env vars, still able to
                    run the daily overview, the existing Proof Ledger
                    (discord_daily_proof.py, untouched) and live
                    opportunities

This module is READ-ONLY against the opportunity engine: it imports the
exact same candidate scan and scoring helpers
app/routers/v2/futgg_market.py's /opportunities and /trade-finder endpoints
already use (_segmented_candidates, _sales_for_rows, _score_and_filter,
evaluate_card) rather than computing anything new. No trade data is
invented here.

Everything in here is inert until:
  1. DISCORD_TRADING_FEED_ENABLED=1 (global kill switch, default off), and
  2. a discord_servers row has status='active' with a non-expired
     expires_at (checked per-run, not just at enable time).
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Optional

from app.services import discord_embeds as embeds
from app.services.discord_integration import DiscordClient
from app.services.discord_daily_proof import (
    PROOF_STATUSES,
    WIN_STATUSES,
    LOSS_STATUSES,
    _SELECT_DAILY_OUTCOMES,
    latest_completed_reporting_period,
    proof_row_is_defensible,
    DailyProofSchedule,
)

log = logging.getLogger("discord_trading_feed")

ACCESS_LEVELS = frozenset({"free", "paid_partner", "official"})
POST_KINDS = frozenset(
    {"daily_overview", "opportunity", "opportunity_update", "product_update", "test"}
)

PROMO_MESSAGE = (
    "This is a daily summary only. **Live trades, the full opportunity feed, and "
    "EAFC.Live's complete trading tools are available with Premium** — "
    "see https://app.eafc.live/#/pricing"
)

# Hard cap on how many completed trades the Free daily overview names, per
# spec ("a maximum of 2-3 of the best completed trades"). These are already
# CLOSED, graded outcomes (see _free_overview_stats) - never a currently
# open or live opportunity, so naming a small number of them cannot be used
# to reconstruct the live Premium feed.
FREE_OVERVIEW_BEST_TRADES_MAX = 3


def feed_globally_enabled() -> bool:
    return (os.getenv("DISCORD_TRADING_FEED_ENABLED") or "0").strip() == "1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class ServerRecord:
    id: int
    guild_id: str
    name: str
    access_level: str
    feed_channel_id: Optional[str]
    proof_channel_id: Optional[str]
    status: str
    expires_at: Optional[datetime]
    # Where opportunity_update ("TARGET HIT" / result) posts go. NULL (the
    # default) means "same channel as the original opportunity" - the
    # pre-existing behavior. Lets a server route sell-time results into a
    # separate channel from its live buy feed.
    sell_channel_id: Optional[str] = None

    @property
    def is_postable(self) -> bool:
        if self.status != "active":
            return False
        if self.access_level == "paid_partner" and self.expires_at is not None:
            if _aware(self.expires_at) <= _now():
                return False
        return True


def server_record_from_feed(feed: dict[str, Any], guild_id: str, guild_name: str) -> ServerRecord:
    """Builds the ServerRecord send_test_post/publish_live_opportunities
    expect out of a discord_feed_billing.get_guild_feed() dict - shared by
    the /feed Discord command and the web settings API so both drive the
    engine identically."""
    return ServerRecord(
        id=feed["id"], guild_id=guild_id, name=guild_name, access_level="paid_partner",
        feed_channel_id=feed.get("feed_channel_id"), proof_channel_id=None,
        status=feed["status"], expires_at=feed.get("current_period_end"),
        sell_channel_id=feed.get("sell_channel_id"),
    )


async def seed_official_server(pool) -> None:
    """Best-effort convenience seed of the pre-existing Official guild
    (DISCORD_GUILD_ID) as a discord_servers row, so it's managed the same
    way as every other server. Never overwrites an existing row - if the
    guild is already registered (e.g. an admin edited it), that row wins.
    Safe to call on every boot."""
    guild_id = (os.getenv("DISCORD_GUILD_ID") or os.getenv("DISCORD_SERVER_ID") or "").strip()
    if not guild_id:
        return
    feed_channel_id = (os.getenv("DISCORD_OPPORTUNITY_CHANNEL_ID") or "").strip() or None
    proof_channel_id = (os.getenv("DISCORD_PROOF_CHANNEL_ID") or "").strip() or None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO discord_servers (
                guild_id, name, access_level, feed_channel_id, proof_channel_id,
                status, enabled_at
            ) VALUES ($1, 'EAFC.Live (Official)', 'official', $2, $3, 'active', NOW())
            ON CONFLICT (guild_id) DO NOTHING
            RETURNING id
            """,
            guild_id,
            feed_channel_id,
            proof_channel_id,
        )
        if row is not None:
            await conn.execute(
                "INSERT INTO discord_server_settings (server_id) VALUES ($1) "
                "ON CONFLICT (server_id) DO NOTHING",
                row["id"],
            )


async def get_server(conn, server_id: int) -> Optional[ServerRecord]:
    row = await conn.fetchrow("SELECT * FROM discord_servers WHERE id=$1", server_id)
    if row is None:
        return None
    return ServerRecord(
        id=row["id"],
        guild_id=row["guild_id"],
        name=row["name"],
        access_level=row["access_level"],
        feed_channel_id=row["feed_channel_id"],
        proof_channel_id=row["proof_channel_id"],
        status=row["status"],
        expires_at=row["expires_at"],
        sell_channel_id=row["sell_channel_id"],
    )


def _dedup_amount(value: Optional[Decimal]) -> str:
    """Stable string form of a coin amount for dedup hashing - rounds to
    the whole coin (embeds never show fractional coins) so formatting
    differences (Decimal("25000") vs Decimal("25000.00")) can't split one
    opportunity into two dedup keys."""
    if value is None:
        return "none"
    return str(int(Decimal(value).to_integral_value(rounding=ROUND_HALF_UP)))


def _opportunity_dedup_key(source_card_id: Any, ci: Any) -> str:
    """Identifies an opportunity by what the embed actually shows (buy,
    sell target, expected profit), not by when the snapshot it was read
    from was computed. futgg_market_snapshot is a materialized view whose
    REFRESH bumps every row's snapshot_computed_at to now() on every
    refresh cycle (~every 2 min) whether or not that card's numbers
    changed, so keying on it meant the identical opportunity got a fresh
    dedup_key - and got reposted - every time cooldown_minutes elapsed."""
    buy_price = ci.current_executable_buy or ci.recommended_buy_max
    return hashlib.sha256(
        "{}:{}:{}:{}".format(
            source_card_id,
            _dedup_amount(buy_price),
            _dedup_amount(ci.recommended_sell_target),
            _dedup_amount(ci.expected_profit_after_tax),
        ).encode()
    ).hexdigest()[:40]


async def _record_post(
    conn, server_id: int, kind: str, dedup_key: str, payload: dict[str, Any]
) -> Optional[int]:
    """Claim a (server, kind, dedup_key) slot. Returns the new post row id,
    or None if this exact post was already made/claimed (duplicate)."""
    return await conn.fetchval(
        """
        INSERT INTO discord_server_posts (server_id, kind, dedup_key, status, payload)
        VALUES ($1, $2, $3, 'sending', $4::jsonb)
        ON CONFLICT (server_id, kind, dedup_key) DO UPDATE
        SET status='sending', attempts=discord_server_posts.attempts+1, last_error=NULL
        WHERE discord_server_posts.status IN ('failed')
        RETURNING id
        """,
        server_id,
        kind,
        dedup_key,
        json.dumps(payload, default=str),
    )


async def _finish_post(conn, post_id: int, *, ok: bool, message_id: Optional[str], error: Optional[str]) -> None:
    await conn.execute(
        """
        UPDATE discord_server_posts
        SET status=$2, discord_message_id=$3, sent_at=CASE WHEN $2='sent' THEN NOW() ELSE NULL END,
            last_error=$4
        WHERE id=$1
        """,
        post_id,
        "sent" if ok else "failed",
        message_id,
        error,
    )


async def _posts_sent_today(conn, server_id: int, kind: str) -> int:
    return await conn.fetchval(
        """
        SELECT COUNT(*) FROM discord_server_posts
        WHERE server_id=$1 AND kind=$2 AND status='sent'
          AND sent_at > NOW() - INTERVAL '24 hours'
        """,
        server_id,
        kind,
    )


async def _last_opportunity_post_at(conn, server_id: int) -> Optional[datetime]:
    return await conn.fetchval(
        """
        SELECT MAX(sent_at) FROM discord_server_posts
        WHERE server_id=$1 AND kind IN ('opportunity', 'test') AND status='sent'
        """,
        server_id,
    )


# ---------------------------------------------------------------------------
# FREE — daily overview
# ---------------------------------------------------------------------------


def build_free_overview_embed(stats: dict[str, Any], app_url: str) -> dict[str, Any]:
    """Free-tier daily overview.

    Deliberately limited to what a completed, already-graded outcome can
    show plus a bare count of open opportunities:
      - successful / unsuccessful / open counts
      - total estimated after-tax result across completed opportunities
      - at most FREE_OVERVIEW_BEST_TRADES_MAX best COMPLETED trades (entry,
        exit, after-tax result) - never a currently open/live one
      - a Premium promo line

    Never includes: a live/open opportunity's player, price, or any field
    in discord_integration.PUBLIC_OPPORTUNITY_FORBIDDEN_FIELDS, and never
    enough rows to reconstruct the full Premium feed (only the capped
    "best trades" sample, all of them already closed).
    """
    combined = Decimal(str(stats.get("total_estimated_profit") or 0))
    color = 0x22C55E if combined > 0 else 0xEAB308
    best_trades = stats.get("best_trades", [])[:FREE_OVERVIEW_BEST_TRADES_MAX]
    best_trades_lines = [
        (
            f"• **{item['name']}** — entry {int(item['entry']):,} → exit {int(item['exit']):,} "
            f"({Decimal(str(item['result'])):+,.0f} coins after tax)"
        )
        for item in best_trades
    ]
    fields = [
        {
            "name": "TRADES",
            "value": (
                f"Successful: {stats['successful']} | "
                f"Unsuccessful: {stats['unsuccessful']} | "
                f"Open: {stats['open']}"
            ),
            "inline": False,
        },
        {
            "name": "TOTAL ESTIMATED AFTER-TAX RESULT (completed trades)",
            "value": f"**{combined:+,.0f} coins**",
            "inline": False,
        },
    ]
    if best_trades_lines:
        fields.append({"name": "BEST COMPLETED TRADES", "value": "\n".join(best_trades_lines), "inline": False})
    fields.append({"name": "EAFC.LIVE PREMIUM", "value": PROMO_MESSAGE, "inline": False})
    return {
        "title": "EAFC LIVE — DAILY TRADING OVERVIEW",
        "url": app_url.rstrip("/") + "/#/opportunities",
        "color": color,
        "fields": fields,
        "footer": {
            "text": (
                "Free daily summary of completed outcomes only. "
                "Live, open opportunities are not shown here."
            )
        },
        "timestamp": _now().isoformat(),
    }


async def _free_overview_stats(conn) -> dict[str, Any]:
    """Same defensible-outcome source as the Official Proof Ledger
    (discord_daily_proof.proof_row_is_defensible) over the trailing 24h,
    plus a count (never details) of currently open qualifying candidates."""
    period = latest_completed_reporting_period()
    rows = await conn.fetch(
        _SELECT_DAILY_OUTCOMES, period.start, period.end, sorted(PROOF_STATUSES)
    )
    eligible = [dict(r) for r in rows if proof_row_is_defensible(dict(r), period)]
    wins = [r for r in eligible if r["outcome_status"] in WIN_STATUSES]
    losses = [r for r in eligible if r["outcome_status"] in LOSS_STATUSES]
    results = [Decimal(str(r["net_profit_after_tax"])) for r in eligible]
    open_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT ON (source_card_id) source_card_id, signal, invalidated_at, expires_at
            FROM futgg_recommendation_snapshots
            ORDER BY source_card_id, evaluated_at DESC
        ) latest
        WHERE latest.signal IN ('buy', 'strong_buy')
          AND latest.invalidated_at IS NULL
          AND (latest.expires_at IS NULL OR latest.expires_at > NOW())
        """
    )
    top_wins = sorted(
        wins,
        key=lambda r: (-Decimal(str(r["net_profit_after_tax"])), str(r["name"])),
    )[:FREE_OVERVIEW_BEST_TRADES_MAX]
    return {
        "successful": len(wins),
        "unsuccessful": len(losses),
        "open": int(open_count or 0),
        "total_estimated_profit": sum(results, Decimal("0")),
        "best_trades": [
            {
                "name": str(r["name"]),
                "entry": int(r["entry_price"]),
                "exit": int(r["outcome_price"]),
                "result": Decimal(str(r["net_profit_after_tax"])),
            }
            for r in top_wins
        ],
    }


async def publish_daily_overview(pool, server: ServerRecord, client: Optional[DiscordClient] = None) -> bool:
    if not feed_globally_enabled() or not server.is_postable or not server.feed_channel_id:
        return False
    client = client or DiscordClient()
    today = date.today().isoformat()
    dedup_key = hashlib.sha256(f"daily_overview:{server.id}:{today}".encode()).hexdigest()[:40]
    async with pool.acquire() as conn:
        remaining = await conn.fetchval(
            "SELECT max_posts_per_day FROM discord_server_settings WHERE server_id=$1", server.id
        )
        already = await _posts_sent_today(conn, server.id, "daily_overview")
        if already >= 1:
            return False
        stats = await _free_overview_stats(conn)
        post_id = await _record_post(conn, server.id, "daily_overview", dedup_key, stats)
    if post_id is None:
        return False
    embed = build_free_overview_embed(stats, os.getenv("FRONTEND_URL", "https://app.eafc.live"))
    status, body = await client.post_embed(
        server.feed_channel_id, embed, nonce=dedup_key[:25], enforce_nonce=True
    )
    ok = status in (200, 201)
    message_id = body.get("id") if ok and isinstance(body, dict) else None
    async with pool.acquire() as conn:
        await _finish_post(
            conn, post_id, ok=ok, message_id=message_id,
            error=None if ok else f"discord_http_{status or 'network'}",
        )
    return ok


# ---------------------------------------------------------------------------
# PAID PARTNER / OFFICIAL — live opportunities
# ---------------------------------------------------------------------------

_LIQUIDITY_LABELS = (
    (0.7, "High"),
    (0.4, "Moderate"),
    (0.0, "Limited"),
)


def _liquidity_label(score: Optional[float]) -> str:
    if score is None:
        return "Unknown"
    for threshold, label in _LIQUIDITY_LABELS:
        if score >= threshold:
            return label
    return "Limited"


_RISK_COLORS = {"low": embeds.BRAND_COLOR, "medium": embeds.BRAND_COLOR_WARN, "high": embeds.BRAND_COLOR_DANGER}


def build_opportunity_post_embed(row: dict[str, Any], ci: Any, *, test: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns (embed, components) - the link button is a separate
    top-level message field in Discord's API, not part of the embed
    itself."""
    app_url = os.getenv("FRONTEND_URL", "https://app.eafc.live")
    thumbnail = row.get("card_image_stored_url") or row.get("card_image_url") or row.get("card_design_image_url")
    risk = str(ci.risk_level or "unknown").lower()
    profit = ci.expected_profit_after_tax
    fields = [
        {
            "name": "BUY",
            # `row` is _segmented_candidates()'s raw candidate dict - a
            # futgg_market_snapshot-shaped row, which has NEVER had a
            # current_executable_buy column (see market_data_provider.py's
            # _SNAPSHOT_COLUMNS). row.get(...) therefore always returned
            # None, so this ALWAYS displayed ci.recommended_buy_max (the
            # conservative buy ceiling) as "BUY" - never the actual, lower
            # live price the card was really executable at. Meanwhile
            # EXPECTED PROFIT below is computed from ci.expected_profit_
            # after_tax, which futgg_intelligence.py derives from the
            # REAL entry (current_bin when executable) - so the two
            # numbers described two different buy prices, and profit
            # looked inflated relative to the price actually shown.
            # ci.current_executable_buy is the field evaluate_card()
            # actually populates from this same row; read it from there.
            "value": f"{embeds.coins(ci.current_executable_buy or ci.recommended_buy_max)} coins",
            "inline": True,
        },
        {"name": "SELL TARGET", "value": f"{embeds.coins(ci.recommended_sell_target)} coins", "inline": True},
        {
            "name": "EXPECTED PROFIT (after EA tax)",
            "value": f"{embeds.coins(profit, signed=True)} coins" if profit is not None else "n/a",
            "inline": True,
        },
        {"name": "RISK", "value": risk.title() if risk != "unknown" else "Unknown", "inline": True},
        {"name": "SELLING SPEED", "value": _liquidity_label(ci.liquidity_score), "inline": True},
        {"name": "CONFIDENCE", "value": f"{(ci.confidence_score or 0):.0%}", "inline": True},
    ]
    embed: dict[str, Any] = {
        "title": f"{'[TEST] ' if test else ''}{embeds.card_label(row)}",
        "description": "🧪 **TEST POST — not a real trade**" if test else "🔥 **Live trading opportunity**",
        "color": 0xF97316 if test else _RISK_COLORS.get(risk, embeds.BRAND_COLOR),
        "author": embeds.brand_author(app_url),
        "fields": fields,
        "footer": {
            "text": "EAFC.Live — this is a placeholder test post." if test
            else "EAFC.Live — live conditions change, verify current price before buying."
        },
        "timestamp": _now().isoformat(),
    }
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}
    components: list[dict[str, Any]] = []
    card_id = row.get("source_card_id")
    if not test and card_id is not None and int(card_id) > 0:
        # ?src=discord_feed lets PlayerPage.jsx recognise a signed-out
        # visitor already saw this exact trade's buy/sell numbers here in
        # Discord, so its account-signup prompt can say so instead of
        # reading as re-hiding what was just given away for free.
        components = [embeds.link_row(
            "View in EAFC.Live", f"{app_url.rstrip('/')}/#/players/{card_id}?src=discord_feed"
        )]
    return embed, components


def build_opportunity_update_embed(post: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    status = str(outcome.get("outcome_status") or "")
    label = {
        "target_hit": "🎯 TARGET HIT",
        "loss_unrealised": "CLOSED — LOSS",
        "downside_hit": "CLOSED — STOP",
    }.get(status, "UPDATE")
    profit = outcome.get("net_profit_after_tax")
    app_url = os.getenv("FRONTEND_URL", "https://app.eafc.live")
    return {
        "title": f"{post.get('name', 'Opportunity')} — {label}",
        "color": embeds.BRAND_COLOR if status == "target_hit" else embeds.BRAND_COLOR_DANGER,
        "author": embeds.brand_author(app_url),
        "fields": [
            {
                "name": "RESULT",
                "value": f"{embeds.coins(profit, signed=True)} coins" if profit is not None else "n/a",
                "inline": False,
            }
        ],
        "footer": {"text": "EAFC.Live"},
        "timestamp": _now().isoformat(),
    }


_TEST_FIXTURE_ROW: dict[str, Any] = {
    "source_card_id": -1,
    "name": "Sample Player (Fixture)",
    "version": "Rare Gold",
    "current_executable_buy": 12500,
}


class _FakeIntelligence:
    recommended_sell_target = 15800
    expected_profit_after_tax = 2500.0
    risk_level = "low"
    liquidity_score = 0.82
    confidence_score = 0.77
    recommended_buy_max = 12500
    current_executable_buy = 12500


async def send_test_post(pool, server: ServerRecord, client: Optional[DiscordClient] = None) -> tuple[bool, Optional[str]]:
    """Sends a clearly-labelled fixture post so an admin can verify a
    channel/webhook works without ever exposing a genuine live trade."""
    if not server.feed_channel_id:
        return False, "no_feed_channel_configured"
    client = client or DiscordClient()
    dedup_key = hashlib.sha256(f"test:{server.id}:{_now().isoformat()}".encode()).hexdigest()[:40]
    embed, components = build_opportunity_post_embed(_TEST_FIXTURE_ROW, _FakeIntelligence(), test=True)
    async with pool.acquire() as conn:
        post_id = await _record_post(conn, server.id, "test", dedup_key, {"fixture": True})
    status, body = await client.post_embed(
        server.feed_channel_id, embed, nonce=dedup_key[:25], enforce_nonce=True, components=components
    )
    ok = status in (200, 201)
    message_id = body.get("id") if ok and isinstance(body, dict) else None
    if post_id is not None:
        async with pool.acquire() as conn:
            await _finish_post(
                conn, post_id, ok=ok, message_id=message_id,
                error=None if ok else f"discord_http_{status or 'network'}",
            )
    return ok, None if ok else f"discord_http_{status or 'network'}"


_TEST_OVERVIEW_STATS: dict[str, Any] = {
    "successful": 3,
    "unsuccessful": 1,
    "open": 7,
    "total_estimated_profit": Decimal("4200"),
    "best_trades": [
        {"name": "[TEST] Sample Player A", "entry": 10000, "exit": 13200, "result": Decimal("2900")},
        {"name": "[TEST] Sample Player B", "entry": 25000, "exit": 26800, "result": Decimal("1550")},
    ],
}

_TEST_OUTCOMES: dict[str, dict[str, Any]] = {
    "target_hit": {"outcome_status": "target_hit", "net_profit_after_tax": Decimal("2500")},
    "loss_unrealised": {"outcome_status": "loss_unrealised", "net_profit_after_tax": Decimal("-800")},
}


async def send_test_free_overview(
    pool, server: ServerRecord, client: Optional[DiscordClient] = None
) -> tuple[bool, Optional[str]]:
    """Sends a clearly-labelled fixture Free daily overview - fixed
    fixture numbers, never a query against real closed-trade data - so an
    admin can verify the channel/format without revealing real Proof
    Ledger results ahead of the real daily post."""
    if not server.feed_channel_id:
        return False, "no_feed_channel_configured"
    client = client or DiscordClient()
    dedup_key = hashlib.sha256(f"test_overview:{server.id}:{_now().isoformat()}".encode()).hexdigest()[:40]
    embed = build_free_overview_embed(_TEST_OVERVIEW_STATS, os.getenv("FRONTEND_URL", "https://app.eafc.live"))
    embed["title"] = "[TEST] " + embed["title"]
    async with pool.acquire() as conn:
        post_id = await _record_post(conn, server.id, "test", dedup_key, {"fixture": True, "kind": "free_overview"})
    status, body = await client.post_embed(server.feed_channel_id, embed, nonce=dedup_key[:25], enforce_nonce=True)
    ok = status in (200, 201)
    message_id = body.get("id") if ok and isinstance(body, dict) else None
    if post_id is not None:
        async with pool.acquire() as conn:
            await _finish_post(
                conn, post_id, ok=ok, message_id=message_id,
                error=None if ok else f"discord_http_{status or 'network'}",
            )
    return ok, None if ok else f"discord_http_{status or 'network'}"


async def send_test_opportunity_update(
    pool, server: ServerRecord, status: str, client: Optional[DiscordClient] = None
) -> tuple[bool, Optional[str]]:
    """Sends a clearly-labelled fixture opportunity-update post (target hit
    or closed/expired) using a fixed fixture outcome - never a real graded
    row - so an admin can verify the update format without a real trade
    having to close first. `status` must be one of _TEST_OUTCOMES's keys."""
    if status not in _TEST_OUTCOMES:
        raise ValueError(f"Unknown test outcome status: {status}")
    if not server.feed_channel_id:
        return False, "no_feed_channel_configured"
    client = client or DiscordClient()
    dedup_key = hashlib.sha256(f"test_update:{server.id}:{status}:{_now().isoformat()}".encode()).hexdigest()[:40]
    embed = build_opportunity_update_embed({"name": "[TEST] Sample Player"}, _TEST_OUTCOMES[status])
    embed["title"] = "[TEST] " + embed["title"]
    async with pool.acquire() as conn:
        post_id = await _record_post(conn, server.id, "test", dedup_key, {"fixture": True, "kind": "opportunity_update", "status": status})
    result_status, body = await client.post_embed(server.feed_channel_id, embed, nonce=dedup_key[:25], enforce_nonce=True)
    ok = result_status in (200, 201)
    message_id = body.get("id") if ok and isinstance(body, dict) else None
    if post_id is not None:
        async with pool.acquire() as conn:
            await _finish_post(
                conn, post_id, ok=ok, message_id=message_id,
                error=None if ok else f"discord_http_{result_status or 'network'}",
            )
    return ok, None if ok else f"discord_http_{result_status or 'network'}"


def _within_posting_window(
    start_hour: Optional[int], end_hour: Optional[int], tz_name: Optional[str], *, now: Optional[datetime] = None
) -> bool:
    """True when posting is allowed right now. No window configured
    (either bound unset) means no restriction - always True. Supports an
    overnight wraparound window where start_hour > end_hour (e.g. 20 -> 6
    covers 20:00 through 05:59)."""
    if start_hour is None or end_hour is None:
        return True
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        tz = timezone.utc
    local_hour = (now or _now()).astimezone(tz).hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= local_hour < end_hour
    return local_hour >= start_hour or local_hour < end_hour


async def _settings_for(conn, server_id: int) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM discord_server_settings WHERE server_id=$1", server_id)
    if row is None:
        raise ValueError(f"discord_server_settings missing for server_id={server_id}")
    return dict(row)


async def publish_live_opportunities(pool, server: ServerRecord, client: Optional[DiscordClient] = None) -> int:
    """Posts newly-qualifying opportunities into server.feed_channel_id,
    honouring per-server max_posts_per_day / min_confidence /
    min_expected_profit / cooldown_minutes / platform. Only reads the
    engine's live candidate scan - never writes to it."""
    if not feed_globally_enabled() or not server.is_postable or not server.feed_channel_id:
        return 0
    if server.access_level not in ("paid_partner", "official"):
        return 0

    # Imported lazily to avoid a router->service import cycle at module load.
    from app.db import get_core_pool
    from app.routers.v2.futgg_market import _segmented_candidates, _sales_for_rows, _score_and_filter
    from app.services.market_data_provider import FutggMarketDataProvider

    client = client or DiscordClient()
    async with pool.acquire() as conn:
        settings = await _settings_for(conn, server.id)
        if not settings.get("live_opportunities_enabled"):
            return 0
        already = await _posts_sent_today(conn, server.id, "opportunity")
        if already >= settings["max_posts_per_day"]:
            return 0
        last_post_at = await _last_opportunity_post_at(conn, server.id)
        if last_post_at is not None:
            elapsed_minutes = (_now() - _aware(last_post_at)).total_seconds() / 60
            if elapsed_minutes < settings["cooldown_minutes"]:
                return 0
        budget_row = await conn.fetchrow(
            """
            SELECT budget_low, budget_medium, budget_high,
                   posting_window_start_hour, posting_window_end_hour, posting_timezone
            FROM discord_servers WHERE id=$1
            """,
            server.id,
        )
        if budget_row and not _within_posting_window(
            budget_row["posting_window_start_hour"],
            budget_row["posting_window_end_hour"],
            budget_row["posting_timezone"],
        ):
            return 0
        band_budget: Optional[dict[str, int]] = None
        band_used: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
        if budget_row and budget_row["budget_low"] is not None:
            band_budget = {
                "low": budget_row["budget_low"],
                "medium": budget_row["budget_medium"],
                "high": budget_row["budget_high"],
            }
            for band in band_budget:
                band_used[band] = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM discord_server_posts
                    WHERE server_id=$1 AND kind='opportunity' AND status='sent'
                      AND sent_at > NOW() - INTERVAL '24 hours'
                      AND payload->>'budget_band' = $2
                    """,
                    server.id, band,
                )

    core_pool = await get_core_pool()
    provider = FutggMarketDataProvider(core_pool)
    candidate_rows = await _segmented_candidates(provider)
    sales_by_card = await _sales_for_rows(provider, candidate_rows)
    scored = _score_and_filter(
        candidate_rows,
        signals={"buy", "strong_buy"},
        min_confidence=float(settings["min_confidence"]),
        min_expected_profit=float(settings["min_expected_profit"]),
        sales_by_card=sales_by_card,
    )
    scored.sort(
        key=lambda pair: (
            -(pair[1].confidence_score or 0.0),
            -(float(pair[1].expected_profit_after_tax) if pair[1].expected_profit_after_tax is not None else 0.0),
            int(pair[0]["source_card_id"]),
        )
    )

    # Posts at most ONE opportunity per call. cooldown_minutes is only
    # checked once, above, against the single most recent post - if this
    # loop kept posting every qualifying candidate in one pass, a batch of
    # several qualifying cards would all land in the same instant with no
    # spacing between them regardless of cooldown_minutes (confirmed live:
    # several posts landed at the identical minute). Posting one per call
    # and relying on the periodic worker loop
    # (DISCORD_TRADING_FEED_POLL_SECONDS) to call this again later makes
    # cooldown_minutes apply between every single post, not just the first
    # in a batch.
    posted = 0
    budget = settings["max_posts_per_day"] - already
    for row, ci in scored:
        if posted >= budget or posted >= 1:
            break
        band = None
        if band_budget is not None:
            from app.services.discord_feed_billing import classify_budget_band

            # Same fix as build_opportunity_post_embed's BUY field: `row`
            # never carries current_executable_buy, so this always
            # classified by the conservative ceiling (ci.recommended_buy_
            # max) instead of the real executable price - ci.current_
            # executable_buy is the field evaluate_card() actually sets.
            price = ci.current_executable_buy or ci.recommended_buy_max
            band = classify_budget_band(float(price) if price is not None else None)
            if band_used[band] >= band_budget[band]:
                continue  # this budget band's daily allocation is exhausted
        dedup_key = _opportunity_dedup_key(row["source_card_id"], ci)
        # buy_price/sell_target are the exact numbers this embed shows the
        # user - publish_opportunity_updates() grades THIS trade against
        # THESE numbers later, not against some other, unrelated
        # evaluation of the same card (see its docstring for why that
        # used to produce results that couldn't have actually happened).
        buy_price = ci.current_executable_buy or ci.recommended_buy_max
        async with pool.acquire() as conn:
            post_id = await _record_post(
                conn, server.id, "opportunity", dedup_key,
                {
                    "source_card_id": row["source_card_id"],
                    "name": row.get("name"),
                    "budget_band": band,
                    "buy_price": float(buy_price) if buy_price is not None else None,
                    "sell_target": float(ci.recommended_sell_target) if ci.recommended_sell_target is not None else None,
                },
            )
        if post_id is None:
            continue  # already posted this exact snapshot to this server
        embed, components = build_opportunity_post_embed(row, ci)
        status, body = await client.post_embed(
            server.feed_channel_id, embed, nonce=dedup_key[:25], enforce_nonce=True, components=components
        )
        ok = status in (200, 201)
        message_id = body.get("id") if ok and isinstance(body, dict) else None
        async with pool.acquire() as conn:
            await _finish_post(
                conn, post_id, ok=ok, message_id=message_id,
                error=None if ok else f"discord_http_{status or 'network'}",
            )
        if ok:
            posted += 1
            if band is not None:
                band_used[band] += 1
    return posted


#: How long after posting an opportunity we watch its own actual price
#: history for a target-hit/loss before calling a negative/flat result.
#: A target hit is always reported the moment it's observed, no matter
#: how much of this window remains - only "it did NOT hit target" has to
#: wait for the full window, so an early snapshot of a still-open
#: position can never be reported as a loss.
OPPORTUNITY_UPDATE_HORIZON = "24h"


async def publish_opportunity_updates(pool, server: ServerRecord, client: Optional[DiscordClient] = None) -> int:
    """Posts an update once a previously-posted opportunity's OWN buy
    price and sell target - the exact numbers that specific embed showed
    users, taken from its payload - have chronologically graded to
    target_hit/loss_unrealised/downside_hit against futgg_bin_history.

    Deliberately does NOT read futgg_recommendation_outcomes: that table
    is fed by a separate, independently-scheduled scanner
    (futgg_scanner.py) that re-evaluates cards on its own cadence, so
    "the latest graded outcome for this card_id" can be - and in
    production was - a completely different recommendation (different
    entry price, different target) from the one actually posted here.
    Grading this post's own payload numbers directly is the only way the
    reported result can be the result of the trade users actually saw.
    Never fabricates an outcome."""
    if not feed_globally_enabled() or not server.is_postable or not server.feed_channel_id:
        return 0
    from app.services import trading_math as tm
    from app.services.futgg_outcome_grader import HORIZONS, grade_recommendation

    client = client or DiscordClient()
    window = HORIZONS[OPPORTUNITY_UPDATE_HORIZON]
    # A server without its own sell-time channel configured keeps the
    # original behavior: results post into the same channel as the buy
    # feed.
    target_channel_id = server.sell_channel_id or server.feed_channel_id
    async with pool.acquire() as conn:
        pending = await conn.fetch(
            """
            SELECT p.id AS post_id, p.payload, p.dedup_key, p.sent_at
            FROM discord_server_posts p
            WHERE p.server_id=$1 AND p.kind='opportunity' AND p.status='sent'
              AND NOT EXISTS (
                  SELECT 1 FROM discord_server_posts u
                  WHERE u.server_id=p.server_id AND u.kind='opportunity_update'
                    AND u.dedup_key = p.dedup_key
              )
            ORDER BY p.sent_at DESC
            LIMIT 25
            """,
            server.id,
        )
        updated = 0
        for row in pending:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"])
            card_id = payload.get("source_card_id")
            buy_price = payload.get("buy_price")
            sell_target = payload.get("sell_target")
            sent_at = row["sent_at"]
            if card_id is None or buy_price is None or sell_target is None or sent_at is None:
                # Older posts made before this payload carried buy_price/
                # sell_target (or a test post) - nothing honest to grade
                # against, so no update is ever posted for them.
                continue
            sent_at = _aware(sent_at)
            observations = await conn.fetch(
                """
                SELECT lowest_bin, captured_at
                FROM futgg_bin_history
                WHERE source_card_id=$1 AND captured_at > $2 AND captured_at <= $3
                ORDER BY captured_at
                """,
                card_id, sent_at, sent_at + window,
            )
            grade = grade_recommendation(
                horizon=OPPORTUNITY_UPDATE_HORIZON,
                evaluated_at=sent_at,
                buy_price=float(buy_price),
                sell_target=float(sell_target),
                observations=[(o["lowest_bin"], o["captured_at"]) for o in observations],
            )
            window_elapsed = _now() >= grade.window_end
            if not grade.target_hit and not (
                window_elapsed and grade.outcome_status in ("loss_unrealised", "downside_hit")
            ):
                continue  # not yet a reportable result - keep watching
            if grade.target_hit:
                # grade.net_profit_after_tax is computed from grade.entry_
                # price/realised_sell_price - the grader's OWN "first
                # observation at or below buy_price" / "first at or above
                # sell_target" chronological walk. That's the right way
                # to answer "was this call ever executable" in aggregate
                # (see futgg_outcome_grader.py), but for a single public
                # result post it means one bad futgg_bin_history row (an
                # 89-rated card briefly showing a few-hundred-coin listing
                # is a data glitch, not a real fill - the exact class of
                # thing EA_MIN_PRICE already guards the low end of, just
                # not at every price tier) makes the reported profit
                # nothing like what was promised: confirmed live, a BUY
                # 5,000 / SELL TARGET 9,600 post whose own embed said
                # "+4,120 expected" got reported as "TARGET HIT +8,605" -
                # implying an ~515-coin entry nobody was ever offered.
                # A confirmed target hit has a fully determined profit
                # (buy at the advertised price, sell at the advertised
                # target) - report THAT, not a re-derived figure that can
                # be thrown off by a single noisy observation.
                net_profit_after_tax = tm.net_profit(Decimal(str(sell_target)), Decimal(str(buy_price)))
            else:
                net_profit_after_tax = grade.net_profit_after_tax
            outcome = {"outcome_status": grade.outcome_status, "net_profit_after_tax": net_profit_after_tax}
            update_post_id = await _record_post(conn, server.id, "opportunity_update", row["dedup_key"], outcome)
            if update_post_id is None:
                continue
            embed = build_opportunity_update_embed({"name": payload.get("name")}, outcome)
            status, body = await client.post_embed(
                target_channel_id, embed, nonce=row["dedup_key"][:25], enforce_nonce=True
            )
            ok = status in (200, 201)
            message_id = body.get("id") if ok and isinstance(body, dict) else None
            await _finish_post(
                conn, update_post_id, ok=ok, message_id=message_id,
                error=None if ok else f"discord_http_{status or 'network'}",
            )
            if ok:
                updated += 1
        return updated


async def run_all_server_feeds(pool) -> None:
    """Called from the periodic worker loop. Iterates every active,
    non-expired server and runs the feed appropriate to its access level.
    A complete no-op unless DISCORD_TRADING_FEED_ENABLED=1."""
    if not feed_globally_enabled():
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM discord_servers WHERE status='active'")
    for row in rows:
        async with pool.acquire() as conn:
            server = await get_server(conn, row["id"])
        if server is None or not server.is_postable:
            continue
        try:
            if server.access_level in ("free", "official"):
                await publish_daily_overview(pool, server)
            if server.access_level in ("paid_partner", "official"):
                await publish_live_opportunities(pool, server)
                await publish_opportunity_updates(pool, server)
        except Exception:
            log.warning("Discord trading feed pass failed for server_id=%s", server.id, exc_info=True)
