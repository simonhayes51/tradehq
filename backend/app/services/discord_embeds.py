from __future__ import annotations

"""
Presentation layer for the Discord slash commands - EAFC.Live-branded
embeds and link-button components. Pure functions: given plain data
dicts (from discord_command_data.py), return Discord embed/component
JSON. No DB or HTTP calls happen here.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

BRAND_NAME = "EAFC.Live"
BRAND_COLOR = 0x22C55E
BRAND_COLOR_WARN = 0xEAB308
BRAND_COLOR_DANGER = 0xEF4444
BRAND_COLOR_NEUTRAL = 0x64748B
LOGO_PATH = "/icons/icon-192x192.png"

LINK_BUTTON_STYLE = 5  # Discord message component: link button (needs url, no custom_id)
ACTION_ROW = 1
BUTTON = 2


def _app_url(app_url: str) -> str:
    return app_url.rstrip("/")


def _brand_author(app_url: str) -> dict[str, str]:
    base = _app_url(app_url)
    return {"name": BRAND_NAME, "icon_url": base + LOGO_PATH, "url": base}


def _footer() -> dict[str, str]:
    return {"text": BRAND_NAME}


def coins(value: Any, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    amount = Decimal(str(value))
    return f"{amount:+,.0f}" if signed else f"{amount:,.0f}"


def link_row(label: str, url: str) -> dict[str, Any]:
    return {"type": ACTION_ROW, "components": [{"type": BUTTON, "style": LINK_BUTTON_STYLE, "label": label[:80], "url": url}]}


def data_age_label(observed_at: Optional[datetime], *, now: Optional[datetime] = None) -> str:
    if observed_at is None:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    minutes = max(0, int((now - observed_at).total_seconds() // 60))
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def card_label(row: dict[str, Any]) -> str:
    name = row.get("name") or "Unknown player"
    rating = row.get("rating")
    version = row.get("version") or row.get("rarity") or ""
    parts = [name]
    if rating is not None:
        parts.append(str(int(rating)))
    if version:
        parts.append(str(version))
    return " — ".join(parts)


def autocomplete_choice(row: dict[str, Any]) -> dict[str, Any]:
    """One Discord autocomplete choice: {name, value}. name <= 100 chars,
    value is the card_id as a string (never a free-text name) so the
    command handler never has to guess which version was meant."""
    label = card_label(row)[:100]
    return {"name": label, "value": str(row["source_card_id"])}


_VERDICT_LABELS = {
    "strong_buy": "Buy",
    "buy": "Buy",
    "hold": "Fair",
    "watch": "Watch",
    "avoid": "Avoid",
}

_LIQUIDITY_LABELS = ((0.7, "Fast"), (0.4, "Moderate"), (0.0, "Slow"))


def liquidity_label(score: Optional[float]) -> str:
    if score is None:
        return "Unknown"
    for threshold, label in _LIQUIDITY_LABELS:
        if score >= threshold:
            return label
    return "Slow"


def verdict_label(ci: Any) -> str:
    """Never presents insufficient data as a confident verdict."""
    if str(getattr(ci, "status", "")) == "insufficient_data" or getattr(ci, "signal", None) == "insufficient_data":
        return "Insufficient data"
    return _VERDICT_LABELS.get(getattr(ci, "signal", None), "Unknown")


def not_linked_embed(app_url: str) -> dict[str, Any]:
    return {
        "title": "Discord not linked",
        "description": (
            "Your Discord isn't linked to an EAFC.Live account yet. "
            f"Link it securely at {_app_url(app_url)}/#/settings/discord, then try again."
        ),
        "color": BRAND_COLOR_WARN,
        "author": _brand_author(app_url),
        "footer": _footer(),
    }


def error_embed(correlation_id: str) -> dict[str, Any]:
    return {
        "title": "Something went wrong",
        "description": (
            f"That command hit an unexpected error. Reference `{correlation_id}` "
            "if you need to report this."
        ),
        "color": BRAND_COLOR_DANGER,
        "footer": _footer(),
    }


def no_card_found_embed(query: str, app_url: str) -> dict[str, Any]:
    return {
        "title": "No matching card",
        "description": (
            f"No card matched **{query}**. Start typing a player's name and pick one of the "
            "suggestions Discord offers, rather than typing a full name and pressing enter."
        ),
        "color": BRAND_COLOR_WARN,
        "author": _brand_author(app_url),
        "footer": _footer(),
    }


def price_embed(row: dict[str, Any], app_url: str, *, platform: str = "ps") -> dict[str, Any]:
    price = row.get("current_executable_buy") or row.get("current_bin")
    thumbnail = row.get("card_image_stored_url") or row.get("card_image_url") or row.get("card_design_image_url")
    age = data_age_label(row.get("bin_checked_at") or row.get("bin_captured_at"))
    fields = [
        {"name": "PLATFORM", "value": platform.upper(), "inline": True},
        {"name": "DATA AGE", "value": age, "inline": True},
    ]
    embed: dict[str, Any] = {
        "title": card_label(row),
        "description": f"**Current buy: {coins(price)} coins**" if price is not None else "No current price available.",
        "color": BRAND_COLOR if price is not None else BRAND_COLOR_NEUTRAL,
        "author": _brand_author(app_url),
        "fields": fields,
        "footer": _footer(),
    }
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}
    return embed


def fairvalue_embed(row: dict[str, Any], ci: Any, app_url: str, *, platform: str = "ps") -> dict[str, Any]:
    thumbnail = row.get("card_image_stored_url") or row.get("card_image_url") or row.get("card_design_image_url")
    age = data_age_label(row.get("bin_checked_at") or row.get("bin_captured_at"))
    verdict = verdict_label(ci)
    price = row.get("current_executable_buy") or row.get("current_bin")
    fair_value = getattr(ci, "fair_value", None)

    diff_line = "n/a"
    if price is not None and fair_value is not None and float(price) > 0:
        diff = Decimal(str(fair_value)) - Decimal(str(price))
        pct = (diff / Decimal(str(price))) * Decimal("100")
        diff_line = f"{coins(diff, signed=True)} coins ({pct:+.1f}%)"

    color = {
        "Buy": BRAND_COLOR,
        "Fair": BRAND_COLOR_NEUTRAL,
        "Watch": BRAND_COLOR_WARN,
        "Avoid": BRAND_COLOR_DANGER,
        "Insufficient data": BRAND_COLOR_NEUTRAL,
    }.get(verdict, BRAND_COLOR_NEUTRAL)

    fields = [
        {"name": "CURRENT PRICE", "value": f"{coins(price)} coins" if price is not None else "n/a", "inline": True},
        {"name": "FAIR VALUE", "value": f"{coins(fair_value)} coins" if fair_value is not None else "n/a", "inline": True},
        {"name": "DIFFERENCE", "value": diff_line, "inline": True},
        {"name": "VERDICT", "value": verdict, "inline": True},
        {"name": "RISK", "value": str(getattr(ci, "risk_level", None) or "unknown").title(), "inline": True},
        {"name": "SELLING SPEED", "value": liquidity_label(getattr(ci, "liquidity_score", None)), "inline": True},
        {"name": "PLATFORM", "value": platform.upper(), "inline": True},
        {"name": "DATA AGE", "value": age, "inline": True},
    ]
    embed: dict[str, Any] = {
        "title": card_label(row),
        "color": color,
        "author": _brand_author(app_url),
        "fields": fields,
        "footer": _footer(),
    }
    if verdict == "Insufficient data":
        embed["description"] = "Not enough recent sales/price data to give a confident valuation yet."
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}
    return embed


def watchlist_embed(items: list[dict[str, Any]], app_url: str) -> dict[str, Any]:
    if not items:
        return {
            "title": "Your watchlist",
            "description": "Your watchlist is empty. Add a card from the EAFC.Live app to start tracking it.",
            "color": BRAND_COLOR_NEUTRAL,
            "author": _brand_author(app_url),
            "footer": _footer(),
        }
    lines = []
    for item in items[:10]:
        started = item.get("started_price")
        last = item.get("last_price")
        movement = ""
        if started is not None and last is not None:
            delta = Decimal(str(last)) - Decimal(str(started))
            movement = f" ({coins(delta, signed=True)})"
        price_line = f"{coins(last)} coins" if last is not None else f"{coins(started)} coins (entry)"
        lines.append(f"**{item.get('player_name')}** ({item.get('version') or 'N/A'}) — {price_line}{movement}")
    return {
        "title": "Your watchlist",
        "description": "\n".join(lines),
        "color": BRAND_COLOR,
        "author": _brand_author(app_url),
        "footer": {"text": f"{BRAND_NAME} — showing {min(len(items), 10)} of {len(items)}"},
    }


def alerts_embed(items: list[dict[str, Any]], app_url: str) -> dict[str, Any]:
    if not items:
        return {
            "title": "Your alerts",
            "description": "You have no active alerts. Set one up from a card's page in the EAFC.Live app.",
            "color": BRAND_COLOR_NEUTRAL,
            "author": _brand_author(app_url),
            "footer": _footer(),
        }
    lines = []
    for item in items[:10]:
        status = f"last triggered {data_age_label(item.get('last_alert_at'))}" if item.get("last_alert_at") else "not yet triggered"
        lines.append(
            f"**Card {item.get('card_id')}** ({item.get('platform')}) — {item.get('metric')} "
            f"±{item.get('rise_pct')}%/{item.get('fall_pct')}% — {status}"
        )
    return {
        "title": "Your alerts",
        "description": "\n".join(lines),
        "color": BRAND_COLOR,
        "author": _brand_author(app_url),
        "footer": {"text": f"{BRAND_NAME} — showing {min(len(items), 10)} of {len(items)}"},
    }


def results_embed(trades: list[dict[str, Any]], app_url: str) -> dict[str, Any]:
    if not trades:
        return {
            "title": "Your recent trades",
            "description": "You haven't logged any trades yet.",
            "color": BRAND_COLOR_NEUTRAL,
            "author": _brand_author(app_url),
            "footer": _footer(),
        }
    lines = []
    total = Decimal("0")
    for t in trades:
        profit = t.get("profit")
        buy, sell = t.get("buy"), t.get("sell")
        when = t.get("timestamp")
        date_label = when.strftime("%d %b") if isinstance(when, datetime) else "unknown date"
        player = t.get("player") or "Unknown"
        version = t.get("version") or "N/A"
        profit_label = coins(profit, signed=True) + " coins" if profit is not None else "n/a"
        buy_label = coins(buy) if buy is not None else "n/a"
        sell_label = coins(sell) if sell is not None else "n/a"
        if profit is not None:
            total += Decimal(str(profit))
        lines.append(f"**{player}** ({version}) — buy {buy_label} → sell {sell_label} — {profit_label} — {date_label}")
    return {
        "title": "Your recent trades",
        "description": "\n".join(lines),
        "color": BRAND_COLOR,
        "author": _brand_author(app_url),
        "footer": {"text": f"{BRAND_NAME} — total for these {len(trades)}: {coins(total, signed=True)} coins"},
    }


def account_embed(summary: dict[str, Any], app_url: str) -> dict[str, Any]:
    tier = str(summary.get("tier") or "free").title()
    premium_until = summary.get("premium_until")
    plan_line = tier
    if premium_until is not None and isinstance(premium_until, datetime):
        plan_line += f" (until {premium_until.strftime('%d %b %Y')})"
    return {
        "title": "Your EAFC.Live account",
        "color": BRAND_COLOR if tier != "Free" else BRAND_COLOR_NEUTRAL,
        "author": _brand_author(app_url),
        "fields": [
            {"name": "PLAN", "value": plan_line, "inline": True},
            {"name": "WATCHLIST", "value": str(summary.get("watchlist_count", 0)), "inline": True},
            {"name": "ACTIVE ALERTS", "value": str(summary.get("alerts_count", 0)), "inline": True},
            {"name": "LOGGED TRADES", "value": str(summary.get("trades_count", 0)), "inline": True},
        ],
        "footer": {"text": f"{BRAND_NAME} — only visible to you"},
    }


def help_embed(app_url: str) -> dict[str, Any]:
    return {
        "title": "EAFC.Live commands",
        "color": BRAND_COLOR,
        "author": _brand_author(app_url),
        "description": "All account commands reply privately (only you can see them), marked 🔒 below.",
        "fields": [
            {"name": "/price <player>", "value": "Current buy price for a card", "inline": False},
            {"name": "/fairvalue <player>", "value": "Fair value, verdict, risk and selling speed", "inline": False},
            {"name": "🔒 /watchlist", "value": "Your tracked cards", "inline": False},
            {"name": "🔒 /alerts", "value": "Your active price alerts", "inline": False},
            {"name": "🔒 /results", "value": "Your recent logged trades", "inline": False},
            {"name": "🔒 /account", "value": "Your EAFC.Live account and plan", "inline": False},
            {"name": "/help", "value": "This message", "inline": False},
        ],
        "footer": {"text": f"{BRAND_NAME} — link your Discord in the app to use 🔒 commands"},
    }
