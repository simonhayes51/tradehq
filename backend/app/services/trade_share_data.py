# app/services/trade_share_data.py
#
# Data assembly for the "Share" button on a completed Portfolio trade.
# Trades opened through the v2 recommendation flow (app/routers/v2/trades.py)
# carry a card_id (migrations/032_open_trades_and_recommendation_snapshots.sql)
# and get an exact card-art lookup; older/manually-logged trades (the
# original 010_consolidate_core_bootstrap.sql schema) only ever stored a
# free-text player/version pair, so those fall back to a best-effort
# name(+version) search. Either way, a trade with no resolvable art still
# renders a valid card, just without the thumbnail - this must never
# block sharing a trade.
from __future__ import annotations

from typing import Any, Optional

from app.db import get_core_pool
from app.services.discord_command_data import get_card, search_cards


async def get_owned_trade(user_id: str, trade_id: int) -> Optional[dict[str, Any]]:
    """Scoped by (user_id, trade_id) - trades has no other unique key, and
    this is the only lookup path into trade_share_render, so an unowned
    trade_id can never resolve to someone else's trade."""
    pool = await get_core_pool()
    row = await pool.fetchrow(
        "SELECT * FROM trades WHERE user_id=$1 AND trade_id=$2",
        str(user_id), int(trade_id),
    )
    return dict(row) if row else None


async def _best_effort_card_art(player: str, version: str) -> Optional[dict[str, Any]]:
    candidates = await search_cards(player, limit=10)
    if not candidates:
        return None
    version_lower = (version or "").strip().lower()
    for row in candidates:
        if (row.get("version") or "").strip().lower() == version_lower:
            return row
    return candidates[0]


async def _card_art_for(trade: dict[str, Any]) -> Optional[dict[str, Any]]:
    card_id = trade.get("card_id")
    if card_id is not None:
        card = await get_card(int(card_id))
        if card is not None:
            return card
    return await _best_effort_card_art(trade["player"], trade.get("version") or "")


async def build_trade_share_render_data(trade: dict[str, Any]) -> dict[str, Any]:
    card = await _card_art_for(trade)
    image_url = None
    rating = None
    if card:
        image_url = card.get("card_image_stored_url") or card.get("card_image_url") or card.get("card_design_image_url")
        rating = card.get("rating")
    return {
        "player": trade["player"],
        "version": trade.get("version") or "Standard",
        "buy": trade["buy"],
        "sell": trade["sell"],
        "quantity": trade.get("quantity") or 1,
        "profit": trade.get("profit") or 0,
        "ea_tax": trade.get("ea_tax") or 0,
        "platform": trade.get("platform"),
        "timestamp": trade.get("timestamp").isoformat() if trade.get("timestamp") else None,
        "image_url": image_url,
        "rating": rating,
    }


def build_share_caption(render_data: dict[str, Any]) -> str:
    profit = render_data["profit"]
    is_profit = profit >= 0
    verb = "Flipped" if is_profit else "Sold"
    return (
        f"{'📈' if is_profit else '📉'} {verb} {render_data['player']} ({render_data['version']}) "
        f"for {abs(profit):,} coins {'profit' if is_profit else 'loss'} on EAFC.Live! "
        f"Bought at {render_data['buy']:,}, sold at {render_data['sell']:,}."
    )
