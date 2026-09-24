from __future__ import annotations

"""
Data-access layer for the Discord slash commands. Pure functions that
return plain dicts/tuples - no Discord-specific formatting or transport
concerns live here (see discord_embeds.py for presentation,
discord_interactions.py for the HTTP/interaction transport).

CRITICAL: uses the exact same pool and table the live website itself
reads/writes for each kind of record, never a second/parallel data path:

  - users / trades / entitlements -> get_core_pool()      (DATABASE_URL)
  - watchlist / watchlist_alerts   -> get_watchlist_pool()  (WATCHLIST_DATABASE_URL)

See app/routers/watchlist.py's own header comment: "This is the only live
watchlist implementation" - against WATCHLIST_DATABASE_URL via
get_watchlist_db(), NOT the core pool. The very first version of the
Discord commands queried the watchlist/alerts tables through the CORE
pool instead, which reads back empty for any deployment where
WATCHLIST_DATABASE_URL is a genuinely separate database from
DATABASE_URL - the two only coincide when someone has pointed both env
vars at the same connection string, which is a deployment choice, not a
guarantee this code may rely on. That mismatch - not a missing account
link, and not empty data - is why a correctly-linked account with a real
watchlist entry saw "/watchlist is empty": the query ran, successfully,
against the wrong database.

trades has no equivalent split: main.py's /api/trades endpoints resolve
the caller via get_current_user() + Depends(get_db) (core pool), so
get_trades()/count_trades() below match that exactly.
"""

from typing import Any, Optional

from app.db import get_core_pool, get_watchlist_pool


class NotLinked(Exception):
    """Raised when a discord_id has no linked EAFC.Live account."""


async def resolve_user_id(discord_id: str) -> Optional[str]:
    """users.discord_id -> users.id. `users` lives on the core pool - the
    same table and pool app.services.discord_integration's role sync and
    main.py's OAuth login both use, so this returns the exact identity a
    logged-in website session would use, whatever id scheme originally
    created that row (Discord OAuth signup or a pre-existing linked
    email account)."""
    pool = await get_core_pool()
    row_id = await pool.fetchval("SELECT id FROM users WHERE discord_id=$1", int(discord_id))
    return str(row_id) if row_id is not None else None


async def search_cards(query: str, *, limit: int = 25) -> list[dict[str, Any]]:
    """Same search used by the app's own player search (case-insensitive
    via ILIKE, best-effort diacritics-insensitive via
    market_data_provider.normalize_search_term - see search_players's own
    comment). Never invents a candidate; an empty query returns []."""
    if not query or not query.strip():
        return []
    from app.services.market_data_provider import FutggMarketDataProvider, PlayerFilters

    pool = await get_core_pool()
    provider = FutggMarketDataProvider(pool)
    rows = await provider.search_players(PlayerFilters(search=query.strip()), limit=limit)
    return [dict(r) for r in rows]


async def get_card(card_id: int) -> Optional[dict[str, Any]]:
    from app.services.market_data_provider import FutggMarketDataProvider

    pool = await get_core_pool()
    provider = FutggMarketDataProvider(pool)
    row = await provider.get_player(card_id)
    return dict(row) if row is not None else None


async def evaluate_card_by_id(card_id: int) -> Optional[tuple[dict[str, Any], Any]]:
    """Return the canonical fresh FUT.GG evaluation for one exact card.

    Kept as a small compatibility wrapper because the Discord interaction
    layer already imports this function. The evidence-gathering contract now
    lives in futgg_card_evaluation.evaluate_fresh_card, shared with other
    one-card callers instead of being reimplemented here.
    """
    from app.services.futgg_card_evaluation import evaluate_fresh_card
    from app.services.market_data_provider import FutggMarketDataProvider

    pool = await get_core_pool()
    provider = FutggMarketDataProvider(pool)
    evaluated = await evaluate_fresh_card(
        provider,
        int(card_id),
        requested_by="discord",
    )
    if evaluated is None:
        return None
    return evaluated.row, evaluated.intelligence


async def get_watchlist(user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    pool = await get_watchlist_pool()
    rows = await pool.fetch(
        """
        SELECT id, card_id, player_name, version, platform,
               started_price, last_price, started_at
        FROM watchlist WHERE user_id=$1
        ORDER BY started_at DESC LIMIT $2
        """,
        user_id, limit,
    )
    return [dict(r) for r in rows]


async def count_watchlist(user_id: str) -> int:
    pool = await get_watchlist_pool()
    return int(await pool.fetchval("SELECT COUNT(*) FROM watchlist WHERE user_id=$1", user_id))


async def get_alerts(user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    pool = await get_watchlist_pool()
    rows = await pool.fetch(
        """
        SELECT card_id, platform, metric, ref_mode, ref_price,
               rise_pct, fall_pct, last_alert_at
        FROM watchlist_alerts WHERE user_id=$1
        ORDER BY created_at DESC LIMIT $2
        """,
        user_id, limit,
    )
    return [dict(r) for r in rows]


async def count_alerts(user_id: str) -> int:
    pool = await get_watchlist_pool()
    return int(await pool.fetchval("SELECT COUNT(*) FROM watchlist_alerts WHERE user_id=$1", user_id))


async def track_trade(
    discord_id: str,
    *,
    card_id: int,
    buy_price: int,
    sell_target: int,
    platform: str = "ps",
) -> dict[str, Any]:
    """Add a Discord feed call to the caller's real watchlist and create a
    DM price alert at the published sell target.

    This deliberately writes to the SAME watchlist/watchlist_alerts tables as
    the website. It never creates a Discord-only shadow watchlist.
    """
    user_id = await resolve_user_id(discord_id)
    if not user_id:
        raise NotLinked()

    card = await get_card(int(card_id))
    if not card:
        return {"ok": False, "reason": "card_not_found"}

    platform = str(platform or "ps").lower()
    if platform not in {"ps", "xbox", "pc"}:
        platform = "ps"
    buy_price = int(buy_price)
    sell_target = int(sell_target)
    if buy_price <= 0 or sell_target <= buy_price:
        return {"ok": False, "reason": "invalid_trade"}

    from app.auth.entitlements import _limits_for, resolve_user_tier

    core_pool = await get_core_pool()
    tier = await resolve_user_tier(core_pool, user_id, discord_id=str(discord_id))
    limits = _limits_for(tier)
    watch_pool = await get_watchlist_pool()

    existing_watch = await watch_pool.fetchval(
        "SELECT id FROM watchlist WHERE user_id=$1 AND card_id=$2 AND platform=$3",
        user_id, int(card_id), platform,
    )
    if existing_watch is None:
        used = int(await watch_pool.fetchval("SELECT COUNT(*) FROM watchlist WHERE user_id=$1", user_id) or 0)
        if used >= int(limits["watchlist_max"]):
            return {"ok": False, "reason": "watchlist_limit", "limit": int(limits["watchlist_max"])}

    existing_alert = await watch_pool.fetchval(
        """SELECT id FROM watchlist_alerts
           WHERE user_id=$1 AND card_id=$2 AND platform=$3 AND metric='price'""",
        user_id, int(card_id), platform,
    )
    if existing_alert is None:
        used_alerts = int(await watch_pool.fetchval(
            "SELECT COUNT(*) FROM watchlist_alerts WHERE user_id=$1", user_id
        ) or 0)
        if used_alerts >= int(limits["alerts_max"]):
            return {"ok": False, "reason": "alerts_limit", "limit": int(limits["alerts_max"])}

    current_price = card.get("current_bin")
    last_price = int(current_price) if isinstance(current_price, (int, float)) and current_price > 0 else buy_price
    name = card.get("display_name") or card.get("name") or f"Card {card_id}"
    version = card.get("rarity") or card.get("version")
    await watch_pool.execute(
        """
        INSERT INTO watchlist (
            user_id, card_id, player_name, version, platform,
            started_price, last_price, last_checked, notes, source
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,NOW(),$8,'futgg')
        ON CONFLICT (user_id, card_id, platform) DO UPDATE SET
            player_name=EXCLUDED.player_name,
            version=EXCLUDED.version,
            last_price=EXCLUDED.last_price,
            last_checked=NOW(),
            notes=EXCLUDED.notes,
            source='futgg'
        """,
        user_id, int(card_id), str(name), version, platform,
        buy_price, last_price,
        f"Discord trade target: {sell_target:,}",
    )

    # Existing alert machinery is percentage-based. A fixed reference equal
    # to the published buy price plus this exact rise percentage fires at the
    # published target. fall_pct=100 prevents an unrelated downside DM; the
    # button promises a target notification, not a stop-loss call.
    rise_pct = ((sell_target - buy_price) / buy_price) * 100.0
    await watch_pool.execute(
        """
        INSERT INTO watchlist_alerts (
            user_id, user_discord_id, card_id, platform, metric, source,
            ref_mode, ref_price, rise_pct, fall_pct, cooloff_minutes,
            prefer_dm, fallback_channel_id
        )
        VALUES ($1,$2,$3,$4,'price','futgg','fixed',$5,$6,100,43200,TRUE,NULL)
        ON CONFLICT (user_id, card_id, platform, metric) DO UPDATE SET
            user_discord_id=EXCLUDED.user_discord_id,
            source='futgg',
            ref_mode='fixed',
            ref_price=EXCLUDED.ref_price,
            rise_pct=EXCLUDED.rise_pct,
            fall_pct=100,
            cooloff_minutes=43200,
            prefer_dm=TRUE,
            fallback_channel_id=NULL,
            last_alert_at=NULL
        """,
        user_id, str(discord_id), int(card_id), platform, buy_price, rise_pct,
    )
    return {
        "ok": True,
        "player_name": str(name),
        "sell_target": sell_target,
        "platform": platform,
    }


async def get_trades(user_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """profit/version/timestamp are declared NOT NULL in
    migrations/010_consolidate_core_bootstrap.sql, but that migration is a
    `CREATE TABLE IF NOT EXISTS` re-declaration of a table that may
    already have existed under an earlier, hand-applied legacy schema
    (see run_migrations.py's own LEGACY_BASELINE comment) - IF NOT EXISTS
    means those constraints were never actually enforced against
    whatever the table already looked like. A row old enough to predate
    that constraint can still have NULLs the schema no longer allows for
    new rows. Callers (discord_embeds.py) must not assume any field here
    is non-null."""
    pool = await get_core_pool()
    rows = await pool.fetch(
        """
        SELECT player, version, buy, sell, profit, ea_tax, platform, timestamp
        FROM trades WHERE user_id=$1
        ORDER BY timestamp DESC LIMIT $2
        """,
        user_id, limit,
    )
    return [dict(r) for r in rows]


async def count_trades(user_id: str) -> int:
    pool = await get_core_pool()
    return int(await pool.fetchval("SELECT COUNT(*) FROM trades WHERE user_id=$1", user_id))


async def get_account_summary(user_id: str) -> dict[str, Any]:
    from app.auth.entitlements import _load_billing_state, _resolve_tier

    pool = await get_core_pool()
    state = await _load_billing_state(pool, user_id)
    tier = _resolve_tier(state)
    return {
        "tier": tier,
        "premium_until": state.get("premium_until"),
        "watchlist_count": await count_watchlist(user_id),
        "alerts_count": await count_alerts(user_id),
        "trades_count": await count_trades(user_id),
    }
