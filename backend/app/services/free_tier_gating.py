# app/services/free_tier_gating.py
"""
Shared helpers for the Free vs Premium (£3/mo) tier separation - used by
every surface that has to turn a full CardIntelligence-scored opportunity
into either (a) the real thing, for Premium, or (b) a non-actionable
locked preview, for Free. Centralised here (rather than duplicated per
router) so "what counts as safe to show Free" is defined exactly once.

Two call sites today: app/routers/v2/futgg_market.py's /opportunities and
/trade-finder (the real, live endpoints the frontend actually calls), and
app/routers/v2/dashboard.py's Home Dashboard aggregation. Both work off
the same (row, CardIntelligence) pairs from _segmented_candidates() +
evaluate_card(), so the same redaction/selection logic applies unchanged.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.auth.entitlements import DAILY_RESET_TIMEZONE
from app.services.futgg_intelligence import CardIntelligence

_BUDGET_BUCKETS: List[Tuple[int, str]] = [
    (500_000, "premium"),
    (100_000, "high"),
    (15_000, "mid"),
]


def budget_category(entry_price: Optional[int]) -> str:
    if entry_price is None:
        return "unknown"
    for threshold, label in _BUDGET_BUCKETS:
        if entry_price >= threshold:
            return label
    return "low"


_PROFIT_BANDS: List[Tuple[int, str]] = [
    (100_000, "100,000+ potential profit"),
    (50_000, "50,000+ potential profit"),
    (15_000, "15,000+ potential profit"),
    (5_000, "5,000+ potential profit"),
    (1_000, "1,000+ potential profit"),
]


def profit_band(profit_amount: Optional[float]) -> str:
    if profit_amount is None:
        return "Profit potential varies"
    for threshold, label in _PROFIT_BANDS:
        if profit_amount >= threshold:
            return label
    return "Modest profit potential"


def age_minutes(evaluated_at: Optional[datetime]) -> Optional[int]:
    if evaluated_at is None:
        return None
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - evaluated_at).total_seconds() // 60))


def entry_price_of(ci: Optional[CardIntelligence]) -> Optional[int]:
    return int(ci.current_executable_buy) if (ci and ci.current_executable_buy is not None) else None


def locked_opportunity_preview(ci: Optional[CardIntelligence]) -> Dict[str, Any]:
    """A Free-tier teaser for an opportunity that isn't part of today's
    allowance: enough to show a real opportunity exists without leaking
    anything that identifies or lets someone act on the card (no player
    identity, no exact prices, no reasoning text that could identify the
    card - see the product spec's explicit forbidden-fields list)."""
    entry_price = entry_price_of(ci)
    profit = float(ci.expected_profit_after_tax) if (ci and ci.expected_profit_after_tax is not None) else None
    minutes = age_minutes(ci.evaluated_at if ci else None)
    age_text = f"{minutes} minute{'s' if minutes != 1 else ''} ago" if minutes is not None else "recently"
    return {
        "locked": True,
        "budgetCategory": budget_category(entry_price),
        "riskCategory": (ci.risk_level or "unknown").lower() if ci else "unknown",
        "profitBand": profit_band(profit),
        "ageMinutes": minutes,
        "message": f"Premium members received this opportunity {age_text}.",
    }


def current_utc_day() -> str:
    # UTC is EAFC Live's documented daily-reset convention (see
    # entitlements.py's DAILY_RESET_TIMEZONE) - there is no other "day
    # boundary" standard anywhere else in this codebase to defer to.
    assert DAILY_RESET_TIMEZONE == "UTC"
    return datetime.now(timezone.utc).date().isoformat()


def pick_deterministic_indices(count_total: int, user_id: Optional[str], limit: int, salt: str = "") -> List[int]:
    """Simpler sibling of pick_free_daily_opportunities for callers with no
    budget/risk mix requirement (e.g. Invest's single preview) - still a
    pure function of (user_id, today's UTC date, candidate count), so it's
    stable all day and needs no database row. `salt` differentiates
    multiple independent pickers over the same user/day (e.g. so Invest's
    pick isn't the same index as Opportunities')."""
    if count_total <= 0 or limit <= 0:
        return []
    seed_key = f"{salt}:{user_id or 'anon'}:{current_utc_day()}"
    seed = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest(), 16)
    if limit >= count_total:
        return list(range(count_total))
    start = seed % count_total
    return [(start + i) % count_total for i in range(limit)]


def pick_free_daily_opportunities(
    candidates: Sequence[Tuple[Dict[str, Any], Optional[CardIntelligence]]],
    user_id: Optional[str],
    daily_limit: int,
) -> List[int]:
    """Returns the indexes (into `candidates`) of the opportunities Free
    should see today - a pure function of (user_id, today's UTC date, the
    current candidate list), so it needs no database row and can't be
    bypassed by concurrent requests: nothing is written, so there's
    nothing to race. Refreshing, switching devices, or calling the API
    repeatedly all recompute the exact same hash and get the exact same
    answer for the rest of the day.

    Picks a mix where possible: one from the cheapest ("low"/"mid" budget,
    or unpriced) candidates and one from everything else, rather than two
    from the same bucket. Never pads with a lower-quality candidate just
    to reach daily_limit - if fewer than daily_limit candidates exist at
    all, Free simply sees fewer today.
    """
    if not candidates or daily_limit <= 0:
        return []
    seed_key = f"{user_id or 'anon'}:{current_utc_day()}"
    seed = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest(), 16)

    cheap_idx = [
        i for i, (_row, ci) in enumerate(candidates)
        if budget_category(entry_price_of(ci)) in ("low", "mid", "unknown")
    ]
    other_idx = [i for i in range(len(candidates)) if i not in cheap_idx]

    picked: List[int] = []
    if cheap_idx:
        picked.append(cheap_idx[seed % len(cheap_idx)])
    pool_for_second = other_idx or [i for i in cheap_idx if i not in picked]
    if pool_for_second:
        second = pool_for_second[(seed // 7) % len(pool_for_second)]
        if second not in picked:
            picked.append(second)

    # Fill up to daily_limit from whatever's left, deterministically, if
    # the two-bucket pick came up short (e.g. everything is one bucket).
    remaining = [i for i in range(len(candidates)) if i not in picked]
    idx = 0
    while len(picked) < daily_limit and idx < len(remaining):
        picked.append(remaining[idx])
        idx += 1

    return picked[:daily_limit]
