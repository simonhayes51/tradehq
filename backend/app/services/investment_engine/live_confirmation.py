# app/services/investment_engine/live_confirmation.py
"""
FC27-ONLY live BIN/sales confirmation. This is where FC27 can be
substantially better evidence than the FC26 backtest, which only ever
had reconstructed daily chart prices - no BIN depth, no individual
sales, no intraday resolution at all.

Computed strictly from what the schema actually contains (confirmed by
direct repo inspection before writing this module):
  * futgg_bin_history - point-in-time (lowest_bin, price_range_low/high,
    captured_at) snapshots.
  * futgg_sales_history - individual (listed_price, sold_price,
    approximate_sold_at) completed sales.

There is NO active-listing-count / order-book-depth table anywhere in
the schema. `listings_near_floor` and `supply_depth_shrinking` are
therefore always returned as None (unknown) rather than fabricated -
per the explicit instruction not to invent data the schema doesn't
contain. If a future auto_sync scraper starts capturing genuine listing
depth, this is the one place that would change; nothing downstream
should assume these fields are ever non-None today.

Pure module: no I/O. The caller (runner.py) is responsible for fetching
`bin_history_recent`/`sales_recent` bounded to strictly-prior
observations (NO LOOKAHEAD).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class LiveConfirmation:
    lowest_bin: Optional[float]
    lowest_bin_making_new_lows: Optional[bool]
    listings_near_floor: Optional[int]          # always None today - see module docstring
    supply_depth_shrinking: Optional[bool]      # always None today - see module docstring
    sales_count_recent: Optional[int]
    median_sale_price: Optional[float]
    sales_clearing_higher: Optional[bool]
    confirmed: Optional[bool]                    # None = insufficient data to judge either way
    reasons: Tuple[str, ...]


def evaluate_live_confirmation(
    *,
    bin_history_recent: Sequence[Tuple[float, object]],   # (lowest_bin, captured_at), oldest first
    sales_recent: Sequence[Tuple[float, object]],          # (sold_price, approximate_sold_at), oldest first
    min_sales_count: int,
) -> LiveConfirmation:
    reasons: List[str] = []
    lowest_bin = bin_history_recent[-1][0] if bin_history_recent else None

    lowest_bin_new_lows: Optional[bool] = None
    if len(bin_history_recent) >= 2 and lowest_bin is not None:
        prior_min = min(p for p, _ in bin_history_recent[:-1])
        lowest_bin_new_lows = lowest_bin < prior_min
        reasons.append(
            "lowest_bin_still_making_new_lows" if lowest_bin_new_lows
            else "lowest_bin_no_longer_making_new_lows"
        )

    sales_count = len(sales_recent)
    median_sale: Optional[float] = None
    sales_clearing_higher: Optional[bool] = None
    if sales_recent:
        prices = sorted(p for p, _ in sales_recent)
        median_sale = prices[len(prices) // 2]
        if len(sales_recent) >= 4:
            half = len(sales_recent) // 2
            earlier = [p for p, _ in sales_recent[:half]]
            later = [p for p, _ in sales_recent[half:]]
            sales_clearing_higher = (sum(later) / len(later)) > (sum(earlier) / len(earlier))
            reasons.append(
                "sales_clearing_higher" if sales_clearing_higher else "sales_not_yet_clearing_higher"
            )

    if sales_count < min_sales_count:
        confirmed = None
        reasons.append(f"insufficient_recent_sales(<{min_sales_count})")
    else:
        confirmed = bool(lowest_bin_new_lows is False or sales_clearing_higher is True)
        reasons.append("live_confirmation_positive" if confirmed else "live_confirmation_negative")

    return LiveConfirmation(
        lowest_bin=lowest_bin,
        lowest_bin_making_new_lows=lowest_bin_new_lows,
        listings_near_floor=None,
        supply_depth_shrinking=None,
        sales_count_recent=sales_count,
        median_sale_price=median_sale,
        sales_clearing_higher=sales_clearing_higher,
        confirmed=confirmed,
        reasons=tuple(reasons),
    )
