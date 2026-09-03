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
from app.services import discord_command_sync as command_sync
from app.services import discord_embeds as embeds
from app.services import discord_feed_billing as feed_billing
from app.services import discord_permissions as perms

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


def _sub_option(sub_options: List[Dict[str, Any]], name: str) -> Optional[Any]:
    return _option(sub_options, name)


async def _build_feed_reply(interaction_payload: Dict[str, Any], options: List[Dict[str, Any]], discord_id: Optional[str]) -> Dict[str, Any]:
    """/feed subcommand dispatch. Every reply here is ephemeral (enforced
    in interactions()). Permission and guild-scoping are re-derived from
    the signed interaction payload on every call - never trusted from a
    prior state or a client-supplied guild id."""
    if perms.is_dm_interaction(interaction_payload):
        return {"embeds": [embeds.feed_dm_not_allowed_embed()]}
    if not perms.has_manage_guild(interaction_payload):
        return {"embeds": [embeds.feed_permission_denied_embed()]}

    guild_id = str(interaction_payload["guild_id"])
    guild_name = ((interaction_payload.get("guild") or {}).get("name")) or f"Guild {guild_id}"

    if not options:
        return {"content": "Usage: `/feed <setup|test|activate|status|channel|sell_channel|preferences|pause|resume|billing|help>`"}
    sub = options[0]
    sub_name = str(sub.get("name") or "")
    sub_options = sub.get("options") or []

    try:
        if sub_name == "help":
            return {"embeds": [embeds.feed_help_embed()]}

        if sub_name == "setup":
            plan = str(_sub_option(sub_options, "plan"))
            channel_id = str(_sub_option(sub_options, "channel"))
            platform_scope = str(_sub_option(sub_options, "platform"))
            budget_mix = str(_sub_option(sub_options, "budget_mix"))
            ok, reason = await feed_billing.confirm_channel_access(guild_id, channel_id)
            if not ok:
                return {"content": feed_billing.CHANNEL_ACCESS_MESSAGES.get(reason, "Couldn't verify that channel.")}
            feed = await feed_billing.upsert_pending_setup(
                guild_id=guild_id, guild_name=guild_name, owner_discord_id=str(discord_id),
                plan=plan, channel_id=channel_id, platform_scope=platform_scope, budget_mix=budget_mix,
            )
            # Backend-served static pages, not a frontend SPA route - those
            # don't exist yet and 404'd on a real completed checkout (see
            # discord_feed_redirect.py's _static_page docstring).
            from app.routers.discord_feed_redirect import api_base_url, make_redirect_url

            success_url = f"{api_base_url()}/api/discord/feed/success"
            cancel_url = f"{api_base_url()}/api/discord/feed/cancel"
            await feed_billing.create_checkout_session(
                guild_id=guild_id, owner_discord_id=str(discord_id), plan=plan,
                success_url=success_url, cancel_url=cancel_url,
            )
            # Discord link-button URLs are capped at 512 characters; a
            # Stripe Checkout Session URL routinely exceeds that, so the
            # button points at our own short, signed redirect instead of
            # the raw Stripe URL - see discord_feed_redirect.py.

            summary = embeds.feed_setup_summary_embed(feed, feed_billing.PLANS[plan]["price_gbp"])
            return {"embeds": [summary], "components": [embeds.link_row("Continue to payment", make_redirect_url(guild_id, "checkout"))]}

        if sub_name == "test":
            feed = await feed_billing.get_guild_feed(guild_id)
            if not feed or not feed.get("feed_channel_id"):
                return {"content": "Run `/feed setup` first to choose a posting channel."}
            from app.services.discord_trading_feed import ServerRecord, send_test_post

            server = ServerRecord(
                id=feed["id"], guild_id=guild_id, name=guild_name, access_level="paid_partner",
                feed_channel_id=feed["feed_channel_id"], proof_channel_id=None,
                status=feed["status"], expires_at=feed.get("current_period_end"),
            )
            from app.db import get_core_pool

            ok, error = await send_test_post(await get_core_pool(), server)
            if not ok:
                return {"content": f"Test post failed: {error or 'unknown error'}"}
            await feed_billing.mark_test_post_confirmed(guild_id)
            return {"content": f"✅ Sent a synthetic test post to <#{feed['feed_channel_id']}>. Run `/feed activate` when you're ready to go live."}

        if sub_name == "activate":
            feed = await feed_billing.activate(guild_id=guild_id, admin_id=str(discord_id))
            return {"content": f"✅ Server Feed is now **active** in <#{feed['feed_channel_id']}>."}

        if sub_name == "status":
            feed = await feed_billing.get_guild_feed(guild_id)
            if not feed:
                return {"content": "This server hasn't run `/feed setup` yet."}
            posts_used = await feed_billing.posts_used_today(feed["id"])
            channel_ok, reason = (True, None)
            if feed.get("feed_channel_id"):
                channel_ok, reason = await feed_billing.confirm_channel_access(guild_id, feed["feed_channel_id"])
            problem_text = feed_billing.CHANNEL_ACCESS_MESSAGES.get(reason) if reason else None
            return {"embeds": [embeds.feed_status_embed(feed, posts_used=posts_used, channel_ok=channel_ok, channel_problem=problem_text)]}

        if sub_name == "channel":
            channel_id = str(_sub_option(sub_options, "channel"))
            feed = await feed_billing.set_channel(guild_id=guild_id, admin_id=str(discord_id), channel_id=channel_id)
            return {"content": f"✅ Posting channel updated to <#{feed['feed_channel_id']}>."}

        if sub_name == "sell_channel":
            channel_raw = _sub_option(sub_options, "channel")
            channel_id = str(channel_raw) if channel_raw is not None else None
            feed = await feed_billing.set_sell_channel(guild_id=guild_id, admin_id=str(discord_id), channel_id=channel_id)
            if feed.get("sell_channel_id"):
                return {"content": f"✅ TARGET HIT / result posts will now go to <#{feed['sell_channel_id']}>."}
            return {"content": "✅ Sell-time results will post in your main feed channel again."}

        if sub_name == "preferences":
            platform_scope = _sub_option(sub_options, "platform")
            budget_mix = _sub_option(sub_options, "budget_mix")
            min_confidence_raw = _sub_option(sub_options, "min_confidence")
            min_profit_raw = _sub_option(sub_options, "min_expected_profit")
            start_hour_raw = _sub_option(sub_options, "posting_start_hour")
            end_hour_raw = _sub_option(sub_options, "posting_end_hour")
            posting_timezone = _sub_option(sub_options, "posting_timezone")
            clear_posting_window = bool(_sub_option(sub_options, "clear_posting_window"))
            feed = await feed_billing.set_preferences(
                guild_id=guild_id, admin_id=str(discord_id),
                platform_scope=str(platform_scope) if platform_scope else None,
                budget_mix=str(budget_mix) if budget_mix else None,
                min_confidence=float(min_confidence_raw) if min_confidence_raw else None,
                min_expected_profit=int(min_profit_raw) if min_profit_raw else None,
                posting_window_start_hour=int(start_hour_raw) if start_hour_raw is not None else None,
                posting_window_end_hour=int(end_hour_raw) if end_hour_raw is not None else None,
                posting_timezone=str(posting_timezone) if posting_timezone else None,
                clear_posting_window=clear_posting_window,
            )
            return {"content": "✅ Preferences updated.", "embeds": [embeds.feed_status_embed(feed, posts_used=0, channel_ok=True, channel_problem=None)]}

        if sub_name == "pause":
            await feed_billing.pause(guild_id=guild_id, admin_id=str(discord_id))
            return {"content": "⏸️ Server Feed posting is paused. Your subscription is unaffected."}

        if sub_name == "resume":
            feed = await feed_billing.resume(guild_id=guild_id, admin_id=str(discord_id))
            return {"content": f"▶️ Server Feed resumed in <#{feed['feed_channel_id']}>."}

        if sub_name == "billing":
            from app.routers.discord_feed_redirect import make_redirect_url

            return {
                "content": "Manage your Server Feed subscription securely via Stripe:",
                "components": [embeds.link_row("Manage billing", make_redirect_url(guild_id, "billing"))],
            }

        return {"content": f"Unknown /feed subcommand: {sub_name}"}
    except feed_billing.FeedError as exc:
        return {"content": str(exc)}


async def _build_reply(name: str, options: List[Dict[str, Any]], discord_id: Optional[str], correlation_id: str, interaction_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    app_url = _app_url()

    if name == "feed":
        return await _build_feed_reply(interaction_payload or {}, options, discord_id)

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
        card_url = f"{app_url.rstrip('/')}/#/players/{row['source_card_id']}"
        return {"embeds": [embed], "components": [embeds.link_row("View in EAFC.Live", card_url)]}

    # Everything past this point needs a linked account.
    if not discord_id:
        return {"embeds": [embeds.not_linked_embed(app_url)]}
    user_id = await data.resolve_user_id(discord_id)
    if not user_id:
        return {"embeds": [embeds.not_linked_embed(app_url)]}

    if name == "watchlist":
        items = await data.get_watchlist(user_id)
        return {"embeds": [embeds.watchlist_embed(items, app_url)], "components": [embeds.link_row("Manage watchlist", f"{app_url.rstrip('/')}/#/watchlist")]}
    if name == "alerts":
        items = await data.get_alerts(user_id)
        return {"embeds": [embeds.alerts_embed(items, app_url)], "components": [embeds.link_row("Manage alerts", f"{app_url.rstrip('/')}/#/watchlist")]}
    if name == "results":
        trades = await data.get_trades(user_id)
        return {"embeds": [embeds.results_embed(trades, app_url)], "components": [embeds.link_row("View trade journal", f"{app_url.rstrip('/')}/#/trades")]}
    if name == "account":
        summary = await data.get_account_summary(user_id)
        return {"embeds": [embeds.account_embed(summary, app_url)], "components": [embeds.link_row("Open account page", f"{app_url.rstrip('/')}/#/account")]}

    return {"content": "Unknown command."}


async def _run_command(
    application_id: str, interaction_token: str, name: str, options: List[Dict[str, Any]],
    discord_id: Optional[str], interaction_payload: Optional[Dict[str, Any]] = None,
) -> None:
    correlation_id = secrets.token_hex(4)
    try:
        reply = await _build_reply(name, options, discord_id, correlation_id, interaction_payload)
    except Exception:
        log.error("Discord command failed: command=%s correlation_id=%s discord_id=%s", name, correlation_id, discord_id, exc_info=True)
        reply = {"embeds": [embeds.error_embed(correlation_id)]}
    await _followup(application_id, interaction_token, reply)


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

    # Env var first, then the id the boot-time command sync resolved from
    # the bot token itself - so follow-ups still work on a deployment that
    # only has DISCORD_BOT_TOKEN set.
    application_id = os.getenv("DISCORD_APPLICATION_ID") or command_sync.authenticated_application_id()
    interaction_token = payload.get("token")
    ephemeral = name in ACCOUNT_COMMANDS or name == "feed"

    if not application_id or not interaction_token:
        log.error("Cannot defer Discord interaction: missing application_id or interaction token")
        return JSONResponse({
            "type": CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "This bot isn't fully configured yet. Please try again shortly.", "flags": EPHEMERAL},
        })

    background_tasks.add_task(_run_command, application_id, interaction_token, name, options, discord_id, payload)

    ack: Dict[str, Any] = {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}
    if ephemeral:
        ack["data"] = {"flags": EPHEMERAL}
    return JSONResponse(ack)
