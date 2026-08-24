# app/services/trade_summary_data.py
#
# Aggregate stats for the portfolio-summary share card (Today / This week
# / All-time) - backs the Club/Portfolio page's period-picker Share
# control. Scoped entirely by user_id from the query itself (never a
# caller-supplied filter), same ownership-by-construction approach as
# trade_share_data.get_owned_trade.
from __future__ import annotations

from typing import Any, Optional

from app.db import get_core_pool
from app.services.trade_share_data import card_art_for

PERIOD_LABELS = {"today": "Today", "week": "This Week", "all": "All-Time"}

_PERIOD_INTERVALS = {
    "today": "sold_at >= date_trunc('day', NOW())",
    "week": "sold_at >= date_trunc('week', NOW())",
}


async def get_owned_portfolio_summary(user_id: str, period: str) -> dict[str, Any]:
    if period not in PERIOD_LABELS:
        raise ValueError(f"Unknown period: {period}")

    pool = await get_core_pool()
    where_period = f"AND {_PERIOD_INTERVALS[period]}" if period in _PERIOD_INTERVALS else ""

    totals = await pool.fetchrow(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE profit >= 0) AS wins,
            COUNT(*) FILTER (WHERE profit < 0) AS losses,
            COUNT(*) AS closed_trades,
            COALESCE(SUM(profit), 0) AS total_profit,
            COALESCE(SUM(ea_tax), 0) AS total_ea_tax
        FROM trades
        WHERE user_id=$1 AND status='closed' {where_period}
        """,
        str(user_id),
    )
    best_sale = await pool.fetchrow(
        f"""
        SELECT player, version, buy, sell, profit, card_id
        FROM trades
        WHERE user_id=$1 AND status='closed' {where_period}
        ORDER BY profit DESC NULLS LAST
        LIMIT 1
        """,
        str(user_id),
    )

    closed = totals["closed_trades"] or 0
    win_rate = (totals["wins"] / closed * 100) if closed else 0.0

    summary: dict[str, Any] = {
        "period": period,
        "period_label": PERIOD_LABELS[period],
        "wins": totals["wins"] or 0,
        "losses": totals["losses"] or 0,
        "closed_trades": closed,
        "win_rate": round(win_rate, 1),
        "total_profit": totals["total_profit"] or 0,
        "total_ea_tax": totals["total_ea_tax"] or 0,
        "best_sale": None,
    }

    if best_sale is not None:
        card = await card_art_for(dict(best_sale))
        image_url = None
        if card:
            image_url = card.get("card_image_stored_url") or card.get("card_image_url") or card.get("card_design_image_url")
        summary["best_sale"] = {
            "player": best_sale["player"],
            "version": best_sale["version"],
            "profit": best_sale["profit"] or 0,
            "image_url": image_url,
        }

    return summary


def build_summary_share_caption(summary: dict[str, Any]) -> str:
    profit = summary["total_profit"]
    is_profit = profit >= 0
    label = summary["period_label"].lower()
    parts = [
        f"{'📈' if is_profit else '📉'} My {label} on EAFC.Live: "
        f"{summary['wins']}W-{summary['losses']}L, {'+' if is_profit else ''}{profit:,} coins."
    ]
    if summary.get("best_sale"):
        best = summary["best_sale"]
        parts.append(f"Best sale: {best['player']} for {'+' if best['profit'] >= 0 else ''}{best['profit']:,}.")
    return " ".join(parts)
