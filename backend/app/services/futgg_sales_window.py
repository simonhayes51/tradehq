"""Canonical completed-sales window used by FUT.GG intelligence.

Migration 084/085 changed futgg_market_snapshot's valuation aggregates from 14
days to seven days for both performance and recency. Raw history loaders may
still return a wider window for charts/backward compatibility, so every engine
caller filters its raw trend series through this helper before evaluate_card().

This prevents one evaluation from combining a 7-day fair-value aggregate with a
14-day trend series for the same card.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List

VALUATION_SALES_WINDOW_DAYS = 7


def filter_valuation_sales(
    sales: Iterable[dict[str, Any]],
    *,
    as_of: datetime | None = None,
) -> List[dict[str, Any]]:
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=VALUATION_SALES_WINDOW_DAYS)

    out: List[dict[str, Any]] = []
    for sale in sales or []:
        observed = sale.get("approximate_sold_at")
        if observed is None:
            # Trend code cannot place an undated sale chronologically anyway;
            # dropping it is safer than allowing it to leak beyond the window.
            continue
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if observed >= cutoff:
            out.append(sale)
    return out
