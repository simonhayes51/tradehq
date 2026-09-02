# app/services/trade_economics.py
#
# Canonical realised profit/ROI math for a CLOSED, quantity-aware user
# trade (the `trades` table). Three call sites - app/routers/v2/trades.py
# close_trade, app/routers/trades.py create_trade, and its bulk_insert_
# trades - each hand-rolled the identical tax/profit formula inline
# before this module existed, and a fourth (the trade-share card, via
# TradeShareCardArt.jsx) independently recomputed ROI on the frontend
# and forgot to multiply the cost side by quantity - overstating ROI by
# a factor of quantity for every qty>1 trade. Consolidated here so
# there is exactly one place this formula lives.
#
# Deliberately separate from trading_math.py, which is the Recommendation
# Engine's PRE-trade, per-unit opportunity-ROI module (no quantity
# concept at all - see its own docstring). That module scores a card
# price before anyone has bought anything; this one scores a trade a
# user actually logged, buy/sell/quantity all fixed.
#
# Tax convention: EA tax is taken off the unit sell price first (floored
# to whole coins), then the after-tax unit price and unit buy price are
# differenced and multiplied by quantity. This is what create_trade,
# bulk_insert_trades and close_trade have always persisted to `profit`/
# `ea_tax` - preserved exactly here rather than switched to a
# tax-on-aggregate-proceeds order (which can differ by a coin or two on
# the rounding), specifically so this refactor changes no historical or
# newly-computed figure, only where the formula lives.
from __future__ import annotations

from typing import NamedTuple, Optional

EA_TAX_RATE = 0.05


class CloseEconomics(NamedTuple):
    profit: int
    ea_tax: int


def close_trade_economics(buy: int, sell: int, quantity: int) -> CloseEconomics:
    """profit/ea_tax for a closed trade, exactly as persisted to `trades`."""
    sell_after_tax = int(sell * (1 - EA_TAX_RATE))
    ea_tax_per_unit = sell - sell_after_tax
    profit = (sell_after_tax - buy) * quantity
    return CloseEconomics(profit=profit, ea_tax=ea_tax_per_unit * quantity)


def realised_roi_percent(profit: float, buy: float, quantity: int) -> Optional[float]:
    """ROI against total cost (buy * quantity), not against buy alone.

    `profit` is the already-taxed, already-quantity-scaled value stored
    on the trade - never recomputed here, only divided by the total
    coins actually put in. Returns None (not 0 or inf) when there was no
    cost to divide by, so callers can render "—" instead of a bogus 0%.
    """
    total_cost = (buy or 0) * (quantity or 1)
    if not total_cost:
        return None
    return (profit / total_cost) * 100
