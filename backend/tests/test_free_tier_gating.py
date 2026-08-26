# tests/test_free_tier_gating.py
#
# Unit tests for app/services/free_tier_gating.py - the shared helpers
# every Free/Premium (£3/mo) gated surface (futgg_market.py's
# /opportunities + /trade-finder, dashboard.py's Home aggregation,
# investments.py's /signals) uses to decide what a Free caller may see.
# Pure functions, no DB/HTTP needed - same "plain sync test, no
# pytest-asyncio" bar the rest of this repo's test suite already sets.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

from app.services.free_tier_gating import (
    age_minutes,
    budget_category,
    current_utc_day,
    entry_price_of,
    locked_opportunity_preview,
    pick_deterministic_indices,
    pick_free_daily_opportunities,
    profit_band,
)


# ---- budget_category ---------------------------------------------------

def test_budget_category_buckets():
    assert budget_category(None) == "unknown"
    assert budget_category(1000) == "low"
    assert budget_category(15_000) == "mid"
    assert budget_category(99_999) == "mid"
    assert budget_category(100_000) == "high"
    assert budget_category(499_999) == "high"
    assert budget_category(500_000) == "premium"
    assert budget_category(5_000_000) == "premium"


# ---- profit_band ---------------------------------------------------------

def test_profit_band_buckets():
    assert profit_band(None) == "Profit potential varies"
    assert profit_band(500) == "Modest profit potential"
    assert profit_band(1_000) == "1,000+ potential profit"
    assert profit_band(5_000) == "5,000+ potential profit"
    assert profit_band(50_000) == "50,000+ potential profit"
    assert profit_band(100_000) == "100,000+ potential profit"
    assert profit_band(250_000) == "100,000+ potential profit"


# ---- age_minutes -----------------------------------------------------------

def test_age_minutes_none_when_no_timestamp():
    assert age_minutes(None) is None


def test_age_minutes_computes_elapsed_minutes():
    ten_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    minutes = age_minutes(ten_minutes_ago)
    assert minutes in (9, 10)  # allow for test execution jitter


def test_age_minutes_treats_naive_datetime_as_utc():
    naive = datetime.utcnow() - timedelta(minutes=5)
    minutes = age_minutes(naive)
    assert minutes is not None and minutes >= 0


# ---- locked_opportunity_preview: the forbidden-fields contract -----------
# Scenario 4: premium-only fields (exact buy/sell price, exact profit,
# card identity/name) must be absent from a locked preview - only
# budgetCategory/riskCategory/profitBand/ageMinutes/message (plus the
# "locked" flag itself) may appear.

def _fake_ci(**overrides) -> MagicMock:
    ci = MagicMock()
    ci.current_executable_buy = overrides.get("current_executable_buy", 42_000)
    ci.expected_profit_after_tax = overrides.get("expected_profit_after_tax", 6_500)
    ci.risk_level = overrides.get("risk_level", "Medium")
    ci.evaluated_at = overrides.get("evaluated_at", datetime.now(timezone.utc) - timedelta(minutes=3))
    return ci


def test_locked_preview_shape_is_exactly_the_allowed_fields():
    preview = locked_opportunity_preview(_fake_ci())
    assert set(preview.keys()) == {
        "locked", "budgetCategory", "riskCategory", "profitBand", "ageMinutes", "message",
    }
    assert preview["locked"] is True


def test_locked_preview_never_leaks_price_profit_or_identity_fields():
    # Regression guard: even though CardIntelligence carries a card_id,
    # exact prices, and a computed profit figure, none of that - and no
    # player name/rating/club, which locked_opportunity_preview never even
    # receives - may leak into the dict handed back to a Free caller.
    ci = _fake_ci(current_executable_buy=123_456, expected_profit_after_tax=98_765)
    preview = locked_opportunity_preview(ci)
    serialized = str(preview)
    forbidden_markers = ["123456", "98765", "card_id", "current_executable_buy", "name", "recommended_buy_max"]
    for marker in forbidden_markers:
        assert marker not in serialized, f"{marker!r} leaked into a locked preview: {preview}"


def test_locked_preview_handles_missing_intelligence():
    preview = locked_opportunity_preview(None)
    assert preview["locked"] is True
    assert preview["budgetCategory"] == "unknown"
    assert preview["riskCategory"] == "unknown"
    assert preview["ageMinutes"] is None


# ---- pick_free_daily_opportunities: determinism (scenario 2) -------------

def _candidates(n: int, budgets: Optional[List[int]] = None) -> List[Tuple[Dict[str, Any], MagicMock]]:
    budgets = budgets or [10_000] * n
    out = []
    for i in range(n):
        row = {"source_card_id": i}
        ci = _fake_ci(current_executable_buy=budgets[i % len(budgets)])
        out.append((row, ci))
    return out


def test_pick_free_daily_opportunities_is_deterministic_same_user_same_day():
    candidates = _candidates(10, budgets=[5_000, 200_000, 12_000, 600_000, 8_000, 300_000, 9_000, 20_000, 1_000, 700_000])
    first = pick_free_daily_opportunities(candidates, "user-42", 2)
    for _ in range(5):
        again = pick_free_daily_opportunities(candidates, "user-42", 2)
        assert again == first, "repeated calls for the same user/day/candidates must return the same picks"


def test_pick_free_daily_opportunities_differs_across_users():
    # Not a strict requirement of the contract, but with a reasonably
    # sized candidate pool two distinct users hashing to the exact same
    # picks would be a red flag that the seed isn't actually keyed by
    # user_id at all.
    candidates = _candidates(12, budgets=[5_000, 200_000, 12_000, 600_000, 8_000, 300_000])
    picks_a = pick_free_daily_opportunities(candidates, "user-a", 2)
    picks_b = pick_free_daily_opportunities(candidates, "user-b", 2)
    picks_c = pick_free_daily_opportunities(candidates, "user-c", 2)
    assert len({tuple(picks_a), tuple(picks_b), tuple(picks_c)}) > 1


def test_pick_free_daily_opportunities_respects_daily_limit():
    candidates = _candidates(20, budgets=[5_000, 200_000])
    picks = pick_free_daily_opportunities(candidates, "user-1", 2)
    assert len(picks) == 2
    assert len(set(picks)) == 2  # no duplicate index


def test_pick_free_daily_opportunities_never_pads_beyond_available_candidates():
    candidates = _candidates(1, budgets=[5_000])
    picks = pick_free_daily_opportunities(candidates, "user-1", 2)
    assert picks == [0]


def test_pick_free_daily_opportunities_empty_candidates():
    assert pick_free_daily_opportunities([], "user-1", 2) == []


def test_pick_free_daily_opportunities_zero_limit():
    candidates = _candidates(5)
    assert pick_free_daily_opportunities(candidates, "user-1", 0) == []


# ---- pick_deterministic_indices (Invest's single-preview picker) --------

def test_pick_deterministic_indices_is_deterministic():
    first = pick_deterministic_indices(9, "user-7", 1, salt="invest")
    for _ in range(5):
        assert pick_deterministic_indices(9, "user-7", 1, salt="invest") == first


def test_pick_deterministic_indices_returns_everything_when_limit_exceeds_count():
    assert pick_deterministic_indices(3, "user-7", 10, salt="invest") == [0, 1, 2]


def test_pick_deterministic_indices_empty_when_no_candidates_or_limit():
    assert pick_deterministic_indices(0, "user-7", 1, salt="invest") == []
    assert pick_deterministic_indices(5, "user-7", 0, salt="invest") == []


def test_pick_deterministic_indices_salt_differentiates_pickers():
    # Opportunities and Invest must not be forced to always agree on
    # which single index they pick for the same user/day just because
    # they happen to share the same candidate count.
    picks_by_salt = {
        pick_deterministic_indices(10, "same-user", 1, salt="invest")[0],
        pick_deterministic_indices(10, "same-user", 1, salt="opportunities")[0],
        pick_deterministic_indices(10, "same-user", 1, salt="")[0],
    }
    assert len(picks_by_salt) > 1


def test_entry_price_of_none_when_no_intelligence_or_no_buy_price():
    assert entry_price_of(None) is None
    ci = _fake_ci(current_executable_buy=None)
    assert entry_price_of(ci) is None


def test_current_utc_day_is_isoformat_date_string():
    day = current_utc_day()
    # Must parse as YYYY-MM-DD and match "now" in UTC.
    assert day == datetime.now(timezone.utc).date().isoformat()
