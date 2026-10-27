"""Evidence-first investment setups; never infer market depth from a cheapest BIN."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Sequence


def median_window(observations: Sequence[tuple[int, datetime]], now: datetime,
                  start_hours: float, end_hours: float) -> dict | None:
    """Use hourly independent samples so fast price polling isn't over-weighted."""
    buckets: dict[int, tuple[datetime, int]] = {}
    for price, stamp in observations:
        if price is None or price <= 0 or not isinstance(stamp, datetime):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (now - stamp).total_seconds() / 3600
        if not (start_hours <= age < end_hours):
            continue
        key = int(stamp.timestamp() // 3600)
        if key not in buckets or stamp > buckets[key][0]:
            buckets[key] = (stamp, price)
    values = [price for _stamp, price in buckets.values()]
    if not values:
        return None
    return {"median": int(median(values)), "samples": len(values),
            "minimum": min(values), "maximum": max(values)}


def investment_case(observations: Sequence[tuple[int, datetime]], *,
                    current_bin: int, now: datetime | None = None) -> dict:
    """Conservative discovery case, not a BUY or confirmed executable listing.

    The recent reference is the median of independent hourly readings over
    the last 6 hours. A target can only come from the earlier 12-72h
    sustained median, not a maximum, single cheap ask or asserted fair value.
    """
    now = now or datetime.now(timezone.utc)
    current = median_window(observations, now, 0, 6)
    historical = median_window(observations, now, 12, 72)
    if current is None or historical is None or current["samples"] < 4 or historical["samples"] < 12:
        return {"qualified": False, "reason": "insufficient_sustained_history"}
    entry = current["median"]
    historical_median = historical["median"]
    if not (current["minimum"] * .90 <= current_bin <= current["maximum"] * 1.1):
        return {"qualified": False, "reason": "latest_bin_outlier"}
    if entry > 0 and abs(current_bin / entry - 1) > .10:
        return {"qualified": False, "reason": "latest_bin_outlier"}
    # If the current 6h range swings wildly, no reliable subscriber buy zone.
    if current["maximum"] / current["minimum"] > 1.18:
        return {"qualified": False, "reason": "unstable_current_market"}
    discount = (1 - entry / historical_median) * 100 if historical_median else 0
    sell_target = int(historical_median * .97) // 50 * 50
    net = sell_target - (sell_target * 5 // 100) - entry
    roi = net / entry * 100 if entry else 0
    if discount < 10:
        return {"qualified": False, "reason": "no_sustained_discount"}
    if net < 10000 or roi < 10:
        return {"qualified": False, "reason": "insufficient_investment_return"}
    return {
        "qualified": True,
        "status": "RESEARCH",
        "buy_range_low": int(entry * .98) // 50 * 50,
        "buy_range_high": int(entry * 1.02) // 50 * 50,
        "reference_price": entry,
        "historical_median": historical_median,
        "potential_sell_target": sell_target,
        "hypothetical_net_profit": net,
        "hypothetical_roi_pct": round(roi, 1),
        "discount_pct": round(discount, 1),
        "recent_hourly_samples": current["samples"],
        "historical_hourly_samples": historical["samples"],
        "market_depth_verified": False,
        "ready_to_publish": False,
        "reason": "Sustained BIN discount and recent sale activity; manually verify available supply, catalyst and exit before publishing",
    }
