from __future__ import annotations

"""
Discord HTTP Interactions endpoint - the transport for the slash commands
(/price, /fairvalue, /watchlist, /alerts, /results, /account, /help) and
their autocomplete. Chosen over a persistent gateway bot because the
existing Discord integration (app/services/discord_integration.py) is
already REST-only and runs inside this one FastAPI process.

Handles three interaction types:
  - PING (1)                        -> {"type": 1} (Discord's endpoint
                                        verification check)
  - APPLICATION_COMMAND (2)         -> deferred immediately (type 5), the
                                        real work + reply happens in a
                                        FastAPI BackgroundTask via a
                                        follow-up webhook PATCH, so slow
                                        database/engine work never risks
                                        missing Discord's 3-second
                                        response deadline
  - APPLICATION_COMMAND_AUTOCOMPLETE (4) -> answered synchronously (type 8)
                                        - Discord has no deferred form of
                                        autocomplete, so the underlying
                                        search must itself be fast

All data comes from app/services/discord_command_data.py, which resolves
the caller's discord_id to their real EAFC.Live user_id and reads from the
exact same tables/pools the website itself uses - see that module's
docstring for the watchlist/core pool split this fixes. Presentation
(embeds, buttons) lives in app/services/discord_embeds.py. No trade data
or account data is invented here.

Every account command (/watchlist, /alerts, /results, /account) replies
EPHEMERAL (flags=64), decided at defer time via `ephemeral=` on the type-5
ack - Discord ties a deferred response's visibility to that first ack, so
the eventual follow-up PATCH does not need to (and cannot) re-set flags.
"""

import logging
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import aiohttp
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from app.services import discord_command_data as data
from app.services import discord_embeds as embeds

log = logging.getLogger("discord_interactions")

router = APIRouter(prefix="/api/discord/interactions", tags=["discord"])

PING = 1
APPLICATION_COMMAND = 2
APPLICATION_COMMAND_AUTOCOMPLETE = 4

PONG = 1
CHANNEL_MESSAGE_WITH_SOURCE = 4
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
APPLICATION_COMMAND_AUTOCOMPLETE_RESULT = 8

EPHEMERAL = 1 << 6

ACCOUNT_COMMANDS = frozenset({"watchlist", "alerts", "results", "account"})
CARD_COMMANDS = frozenset({"price", "fairvalue"})

# (command, limit, window_seconds) - generous enough for normal use, tight
# enough that repeated calls can't be used to scrape data. Every request
# to this endpoint comes from Discord's own infrastructure (a handful of
# shared IPs), so app/middleware/rate_limit.py's per-IP limiter can't tell
# callers apart here - this limits per (discord_id, command) instead.
_COMMAND_RATE_LIMITS: Dict[str, Tuple[int, int]] = {
    "price": (20, 60),
    "fairvalue": (20, 60),
    "watchlist": (10, 60),
    "alerts": (10, 60),
    "results": (10, 60),
    "account": (10, 60),
}
_AUTOCOMPLETE_RATE_LIMIT: Tuple[int, int] = (30, 10)
_DEFAULT_RATE_LIMIT: Tuple[int, int] = (30, 60)
_command_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _rate_limited(discord_id: Optional[str], name: str, *, autocomplete: bool = False) -> bool:
    key_id = discord_id or "anonymous"
    limit, window = _AUTOCOMPLETE_RATE_LIMIT if autocomplete else _COMMAND_RATE_LIMITS.get(name, _DEFAULT_RATE_LIMIT)
    bucket = _command_hits[(key_id, f"{'ac:' if autocomplete else ''}{name}")]
    now = time.monotonic()
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _verify_signature(body: bytes, signature: str, timestamp: str, public_key: str) -> bool:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def _app_url() -> str:
    return os.getenv("FRONTEND_URL", "https://app.eafc.live")


def _option(options: List[Dict[str, Any]], name: str) -> Optional[Any]:
    for opt in options or []:
        if opt.get("name") == name:
            return opt.get("value")
    return None


def _focused_option(options: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for opt in options or []:
        if opt.get("focused"):
            return opt
    return None


async def _resolve_card(player_option: str) -> tuple[Optional[dict], Optional[dict]]:
    """Returns (row, error_payload). `player_option` is expected to be a
    card_id (the autocomplete's submitted value) but a user can bypass
    autocomplete and submit free text, so both are handled - a free-text
    value that matches more than one card returns an error rather than
    silently guessing which version was meant."""
    player_option = (player_option or "").strip()
    if player_option.isdigit():
        row = await data.get_card(int(player_option))
        if row is None:
            return None, {"error": "not_found", "query": player_option}
        return row, None
    matches = await data.search_cards(player_option, limit=5)
    if not matches:
        return None, {"error": "not_found", "query": player_option}
    if len(matches) > 1:
        return None, {"error": "ambiguous", "query": player_option, "matches": matches}
    return matches[0], None


# ---------------------------------------------------------------------------
# Autocomplete (type 4) - must respond synchronously, no deferral possible
# ---------------------------------------------------------------------------


async def handle_autocomplete(name: str, options: List[Dict[str, Any]]) -> Dict[str, Any]:
    focused = _focused_option(options)
    query = str(focused.get("value") or "") if focused else ""
    if name not in CARD_COMMANDS or not query.strip():
        return {"type": APPLICATION_COMMAND_AUTOCOMPLETE_RESULT, "data": {"choices": []}}
    rows = await data.search_cards(query, limit=25)
    choices = [embeds.autocomplete_choice(r) for r in rows]
    return {"type": APPLICATION_COMMAND_AUTOCOMPLETE_RESULT, "data": {"choices": choices}}


# ---------------------------------------------------------------------------
# Application commands (type 2) - deferred, real work happens after the ack
# ---------------------------------------------------------------------------


async def _followup(application_id: str, interaction_token: str, payload: Dict[str, Any]) -> None:
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{interaction_token}/messages/@original"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.patch(url, json=payload) as resp:
                if resp.status >= 400:
                    log.warning("Discord follow-up PATCH failed: status=%s body=%s", resp.status, (await resp.text())[:300])
    except (aiohttp.ClientError, Exception):
        log.warning("Discord follow-up PATCH raised", exc_info=True)


async def _build_reply(name: str, options: List[Dict[str, Any]], discord_id: Optional[str], correlation_id: str) -> Dict[str, Any]:
    app_url = _app_url()

    if name == "help":
        return {"embeds": [embeds.help_embed(app_url)], "components": [embeds.link_row("Open EAFC.Live", app_url)]}

    if name in CARD_COMMANDS:
        player_option = _option(options, "player")
        platform = str(_option(options, "platform") or "ps")
        if not player_option:
            return {"content": f"Usage: `/{name} <player>` — start typing and pick a suggestion."}
        row, err = await _resolve_card(str(player_option))
        if err is not None:
            if err["error"] == "ambiguous":
                names = ", ".join(embeds.card_label(m) for m in err["matches"][:5])
                return {"content": f"Multiple cards match **{err['query']}** — please pick one from the autocomplete list: {names}"}
            return {"embeds": [embeds.no_card_found_embed(err["query"], app_url)]}
        if name == "price":
            embed = embeds.price_embed(row, app_url, platform=platform)
        else:
            result = await data.evaluate_card_by_id(int(row["source_card_id"]))
            if result is None:
                return {"embeds": [embeds.no_card_found_embed(str(player_option), app_url)]}
            _, ci = result
            embed = embeds.fairvalue_embed(row, ci, app_url, platform=platform)
        card_url = f"{app_url.rstrip('/')}/#/v2/players/{row['source_card_id']}"
        return {"embeds": [embed], "components": [embeds.link_row("View in EAFC.Live", card_url)]}

    # Everything past this point needs a linked account.
    if not discord_id:
        return {"embeds": [embeds.not_linked_embed(app_url)]}
    user_id = await data.resolve_user_id(discord_id)
    if not user_id:
        return {"embeds": [embeds.not_linked_embed(app_url)]}

    if name == "watchlist":
        items = await data.get_watchlist(user_id)
        return {"embeds": [embeds.watchlist_embed(items, app_url)], "components": [embeds.link_row("Manage watchlist", f"{app_url.rstrip('/')}/#/v2/watchlist")]}
    if name == "alerts":
        items = await data.get_alerts(user_id)
        return {"embeds": [embeds.alerts_embed(items, app_url)], "components": [embeds.link_row("Manage alerts", f"{app_url.rstrip('/')}/#/v2/watchlist")]}
    if name == "results":
        trades = await data.get_trades(user_id)
        return {"embeds": [embeds.results_embed(trades, app_url)], "components": [embeds.link_row("View trade journal", f"{app_url.rstrip('/')}/#/v2/journal")]}
    if name == "account":
        summary = await data.get_account_summary(user_id)
        return {"embeds": [embeds.account_embed(summary, app_url)], "components": [embeds.link_row("Open account page", f"{app_url.rstrip('/')}/#/account")]}

    return {"content": "Unknown command."}


async def _run_command(application_id: str, interaction_token: str, name: str, options: List[Dict[str, Any]], discord_id: Optional[str]) -> None:
    correlation_id = secrets.token_hex(4)
    try:
        payload = await _build_reply(name, options, discord_id, correlation_id)
    except Exception:
        log.error("Discord command failed: command=%s correlation_id=%s discord_id=%s", name, correlation_id, discord_id, exc_info=True)
        payload = {"embeds": [embeds.error_embed(correlation_id)]}
    await _followup(application_id, interaction_token, payload)


# ---------------------------------------------------------------------------
# HTTP entrypoint
# ---------------------------------------------------------------------------


@router.post("")
async def interactions(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    public_key = os.getenv("DISCORD_PUBLIC_KEY")
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")
    body = await request.body()

    if not public_key or not signature or not timestamp or not _verify_signature(body, signature, timestamp, public_key):
        raise HTTPException(status_code=401, detail="invalid request signature")

    payload = await request.json()
    interaction_type = payload.get("type")

    if interaction_type == PING:
        return JSONResponse({"type": PONG})

    data_block = payload.get("data") or {}
    name = str(data_block.get("name") or "").lower()
    options = data_block.get("options") or []
    member = payload.get("member") or {}
    discord_user = member.get("user") or payload.get("user") or {}
    discord_id = discord_user.get("id")

    if interaction_type == APPLICATION_COMMAND_AUTOCOMPLETE:
        if _rate_limited(discord_id, name, autocomplete=True):
            return JSONResponse({"type": APPLICATION_COMMAND_AUTOCOMPLETE_RESULT, "data": {"choices": []}})
        return JSONResponse(await handle_autocomplete(name, options))

    if interaction_type != APPLICATION_COMMAND:
        return JSONResponse({"type": CHANNEL_MESSAGE_WITH_SOURCE, "data": {"content": "Unsupported interaction type.", "flags": EPHEMERAL}})

    if _rate_limited(discord_id, name):
        return JSONResponse({
            "type": CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "You're using that command too fast — please wait a moment and try again.", "flags": EPHEMERAL},
        })

    application_id = os.getenv("DISCORD_APPLICATION_ID")
    interaction_token = payload.get("token")
    ephemeral = name in ACCOUNT_COMMANDS

    if not application_id or not interaction_token:
        log.error("Cannot defer Discord interaction: missing application_id or interaction token")
        return JSONResponse({
            "type": CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "This bot isn't fully configured yet. Please try again shortly.", "flags": EPHEMERAL},
        })

    background_tasks.add_task(_run_command, application_id, interaction_token, name, options, discord_id)

    ack: Dict[str, Any] = {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}
    if ephemeral:
        ack["data"] = {"flags": EPHEMERAL}
    return JSONResponse(ack)
