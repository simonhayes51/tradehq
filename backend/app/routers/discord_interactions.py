from __future__ import annotations

"""
Discord HTTP Interactions endpoint — the transport for the new slash
commands (/price, /fairvalue, /watchlist, /alerts, /results, /account,
/help). Chosen over a persistent gateway bot because the existing Discord
integration (app/services/discord_integration.py) is already REST-only and
runs inside this one FastAPI process; this keeps that shape rather than
adding a second long-running service to deploy and monitor.

Discord signs every interaction request with ed25519 (see
https://discord.com/developers/docs/interactions/receiving-and-responding);
requests that don't verify are rejected with 401 before any handler runs,
exactly as Discord requires.

Every command that depends on the caller's own EAFC.Live account
(/watchlist, /alerts, /results, /account) requires a link between
discord_id and users.id (users.discord_id, already populated by the
existing web OAuth flow) and always replies EPHEMERAL (flags=64) so
personal data never lands in a public channel. /price and /fairvalue are
single-card lookups only — never the ranked/sorted opportunity list — so a
free server cannot reconstruct the Premium feed by calling them
repeatedly.

Every request arrives from Discord's own infrastructure (a handful of
shared IPs), so app/middleware/rate_limit.py's per-IP limiter can't tell
callers apart here — instead this module rate-limits per (discord_id,
command) in-process, mirroring that middleware's approach.

No trade data is invented here: every figure returned comes from the same
FutggMarketDataProvider / evaluate_card path the rest of the app uses.
"""

import logging
import os
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("discord_interactions")

router = APIRouter(prefix="/api/discord/interactions", tags=["discord"])

# (command, limit, window_seconds) - generous enough for normal use, tight
# enough that repeated /price calls can't be used to scrape the ranked
# opportunity feed one card at a time.
_COMMAND_RATE_LIMITS: Dict[str, Tuple[int, int]] = {
    "price": (20, 60),
    "fairvalue": (20, 60),
    "watchlist": (10, 60),
    "alerts": (10, 60),
    "results": (10, 60),
    "account": (10, 60),
}
_DEFAULT_RATE_LIMIT: Tuple[int, int] = (30, 60)
_command_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _rate_limited(discord_id: Optional[str], name: str) -> bool:
    key_id = discord_id or "anonymous"
    limit, window = _COMMAND_RATE_LIMITS.get(name, _DEFAULT_RATE_LIMIT)
    bucket = _command_hits[(key_id, name)]
    now = time.monotonic()
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False

PING = 1
APPLICATION_COMMAND = 2
PONG = 1
CHANNEL_MESSAGE_WITH_SOURCE = 4
EPHEMERAL = 1 << 6

HELP_TEXT = (
    "**EAFC.Live commands**\n"
    "`/price <player>` — current buy price for a card\n"
    "`/fairvalue <player>` — fair value & signal for a card\n"
    "`/watchlist` — your tracked cards (private)\n"
    "`/alerts` — your active price alerts (private)\n"
    "`/results` — your recent logged trades (private)\n"
    "`/account` — your EAFC.Live account & plan (private)\n"
    "`/help` — this message\n\n"
    "Account commands need your Discord linked to EAFC.Live — link it at "
    "https://app.eafc.live/#/settings/discord."
)

NOT_LINKED_MESSAGE = (
    "Your Discord isn't linked to an EAFC.Live account yet. Link it securely at "
    "https://app.eafc.live/#/settings/discord, then try again."
)


def _verify_signature(body: bytes, signature: str, timestamp: str, public_key: str) -> bool:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def _respond(content: str, *, ephemeral: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {"content": content[:2000]}
    if ephemeral:
        data["flags"] = EPHEMERAL
    return {"type": CHANNEL_MESSAGE_WITH_SOURCE, "data": data}


def _option(options: List[Dict[str, Any]], name: str) -> Optional[Any]:
    for opt in options or []:
        if opt.get("name") == name:
            return opt.get("value")
    return None


async def _linked_user_id(conn, discord_id: str) -> Optional[str]:
    return await conn.fetchval("SELECT id FROM users WHERE discord_id=$1", int(discord_id))


async def _cmd_price(pool, player: str) -> str:
    from app.services.market_data_provider import FutggMarketDataProvider, PlayerFilters

    provider = FutggMarketDataProvider(pool)
    rows = await provider.search_players(PlayerFilters(search=player), limit=1)
    if not rows:
        return f"No card found matching **{player}**."
    row = rows[0]
    price = row.get("current_executable_buy") or row.get("current_bin")
    if price is None:
        return f"**{row.get('name')}** — no current price available."
    return f"**{row.get('name')}** ({row.get('version') or row.get('rarity') or 'N/A'}) — current buy: **{int(price):,} coins**"


async def _cmd_fairvalue(pool, player: str) -> str:
    from app.services.market_data_provider import FutggMarketDataProvider, PlayerFilters
    from app.services.futgg_intelligence import evaluate_card

    provider = FutggMarketDataProvider(pool)
    rows = await provider.search_players(PlayerFilters(search=player), limit=1)
    if not rows:
        return f"No card found matching **{player}**."
    row = rows[0]
    sales: List[Dict[str, Any]] = []
    try:
        sales = await provider.get_recent_sales(int(row["source_card_id"]))
    except Exception:
        pass
    ci = evaluate_card(row, sales=sales)
    fair_value = f"{int(ci.fair_value):,} coins" if ci.fair_value is not None else "unavailable"
    signal = str(ci.signal or "unknown").replace("_", " ").title()
    return (
        f"**{row.get('name')}** — fair value: **{fair_value}**\n"
        f"Signal: {signal} | Risk: {str(ci.risk_level or 'unknown').title()}"
    )


async def _cmd_watchlist(conn, user_id: str) -> str:
    rows = await conn.fetch(
        "SELECT player_name, version, last_price FROM watchlist WHERE user_id=$1 ORDER BY started_at DESC LIMIT 10",
        user_id,
    )
    if not rows:
        return "Your watchlist is empty. Add cards from the EAFC.Live app."
    lines = [
        f"• {r['player_name']}" + (f" ({r['version']})" if r["version"] else "")
        + (f" — {int(r['last_price']):,} coins" if r["last_price"] else "")
        for r in rows
    ]
    return "**Your watchlist:**\n" + "\n".join(lines)


async def _cmd_alerts(conn, user_id: str) -> str:
    rows = await conn.fetch(
        "SELECT card_id, platform, metric, rise_pct, fall_pct FROM watchlist_alerts WHERE user_id=$1 LIMIT 10",
        user_id,
    )
    if not rows:
        return "You have no active alerts."
    lines = [
        f"• Card {r['card_id']} ({r['platform']}) — {r['metric']} ±{r['rise_pct']}%/{r['fall_pct']}%"
        for r in rows
    ]
    return "**Your active alerts:**\n" + "\n".join(lines)


async def _cmd_results(conn, user_id: str) -> str:
    rows = await conn.fetch(
        "SELECT player, version, profit, timestamp FROM trades WHERE user_id=$1 ORDER BY timestamp DESC LIMIT 5",
        user_id,
    )
    if not rows:
        return "You haven't logged any trades yet."
    lines = [
        f"• {r['player']} ({r['version']}) — {int(r['profit']):+,} coins — {r['timestamp'].strftime('%d %b')}"
        for r in rows
    ]
    total = sum(int(r["profit"]) for r in rows)
    return "**Your recent trades:**\n" + "\n".join(lines) + f"\n\nLast {len(rows)}: {total:+,} coins"


async def _cmd_account(conn, user_id: str) -> str:
    from app.auth.entitlements import _load_billing_state, _resolve_tier

    state = await _load_billing_state(conn, user_id)
    tier = _resolve_tier(state)
    premium_until = state.get("premium_until")
    line = f"**Plan:** {tier.title()}"
    if premium_until:
        line += f" (until {premium_until.strftime('%d %b %Y')})"
    return line


async def _dispatch(pool, name: str, options: List[Dict[str, Any]], discord_id: Optional[str]) -> str:
    if name == "help":
        return HELP_TEXT

    if name == "price":
        player = _option(options, "player")
        if not player:
            return "Usage: `/price <player name>`"
        return await _cmd_price(pool, str(player))

    if name == "fairvalue":
        player = _option(options, "player")
        if not player:
            return "Usage: `/fairvalue <player name>`"
        return await _cmd_fairvalue(pool, str(player))

    async with pool.acquire() as conn:
        # Everything past this point needs a linked account.
        if not discord_id:
            return NOT_LINKED_MESSAGE
        user_id = await _linked_user_id(conn, discord_id)
        if not user_id:
            return NOT_LINKED_MESSAGE

        if name == "watchlist":
            return await _cmd_watchlist(conn, user_id)
        if name == "alerts":
            return await _cmd_alerts(conn, user_id)
        if name == "results":
            return await _cmd_results(conn, user_id)
        if name == "account":
            return await _cmd_account(conn, user_id)

    return "Unknown command."


@router.post("")
async def interactions(request: Request) -> JSONResponse:
    public_key = os.getenv("DISCORD_PUBLIC_KEY")
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")
    body = await request.body()

    if not public_key or not signature or not timestamp or not _verify_signature(body, signature, timestamp, public_key):
        raise HTTPException(status_code=401, detail="invalid request signature")

    payload = await request.json()
    if payload.get("type") == PING:
        return JSONResponse({"type": PONG})

    if payload.get("type") != APPLICATION_COMMAND:
        return JSONResponse(_respond("Unsupported interaction type.", ephemeral=True))

    data = payload.get("data") or {}
    name = str(data.get("name") or "").lower()
    options = data.get("options") or []
    member = payload.get("member") or {}
    discord_user = member.get("user") or payload.get("user") or {}
    discord_id = discord_user.get("id")

    if name != "help" and _rate_limited(discord_id, name):
        return JSONResponse(
            _respond("You're using that command too fast — please wait a moment and try again.", ephemeral=True)
        )

    from app.db import get_core_pool

    pool = await get_core_pool()
    try:
        content = await _dispatch(pool, name, options, discord_id)
    except Exception:
        log.warning("Discord command %r failed", name, exc_info=True)
        content = "Something went wrong running that command. Please try again shortly."

    ephemeral = name in {"watchlist", "alerts", "results", "account"}
    return JSONResponse(_respond(content, ephemeral=ephemeral))
