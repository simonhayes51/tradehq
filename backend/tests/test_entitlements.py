# tests/test_entitlements.py
#
# No test file for app/auth/entitlements.py existed before this - the
# module's tier resolution, limits, and daily-usage-counter helpers were
# only ever exercised indirectly through the routers that call them.
# pytest-asyncio isn't installed in this repo (see tests/test_watchlist_
# router.py's own note on this), so async bodies below are driven with
# asyncio.run(), same as everywhere else in this suite.
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from starlette.requests import Request

from app.auth import entitlements as ent_module
from app.auth.entitlements import (
    PREMIUM_TIERS,
    _capabilities_for,
    _limits_for,
    _resolve_tier,
    compute_entitlements,
    get_daily_usage,
    increment_daily_usage,
    is_premium_tier,
)


def run_async(coro_fn):
    return asyncio.run(coro_fn())


# ---- _resolve_tier -----------------------------------------------------
# Scenario 10/11: premium/admin/elite resolve correctly, and a lapsed
# subscription resolves back to "free" - _resolve_tier takes a plain
# dict, no DB or Stripe involved.

def _state(**overrides) -> Dict[str, Any]:
    base = {
        "plan": None,
        "tier_column": None,
        "premium_until": None,
        "active_subscription": False,
    }
    base.update(overrides)
    return base


def test_resolve_tier_defaults_to_free():
    assert _resolve_tier(_state()) == "free"


def test_resolve_tier_pro_via_plan_pro():
    assert _resolve_tier(_state(plan="pro")) == "pro"


def test_resolve_tier_pro_via_plan_premium_alias():
    assert _resolve_tier(_state(plan="premium")) == "pro"


def test_resolve_tier_elite_via_plan():
    assert _resolve_tier(_state(plan="elite")) == "elite"


def test_resolve_tier_elite_via_manual_tier_column_grant():
    # users.tier is the manual-grant channel - an admin running
    # UPDATE users SET tier='elite' must work without faking Stripe.
    assert _resolve_tier(_state(tier_column="elite")) == "elite"


def test_resolve_tier_pro_via_active_subscription_alone():
    assert _resolve_tier(_state(active_subscription=True)) == "pro"


def test_resolve_tier_pro_via_premium_until_in_the_future():
    future = datetime.now(timezone.utc) + timedelta(days=5)
    assert _resolve_tier(_state(premium_until=future)) == "pro"


def test_resolve_tier_free_when_premium_until_has_passed():
    # Scenario 11: cancellation/expiry - a lapsed premium_until with no
    # other paid signal must resolve back to free, not stay premium.
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert _resolve_tier(_state(premium_until=past)) == "free"


def test_resolve_tier_free_when_subscription_inactive_and_no_premium_until():
    assert _resolve_tier(_state(active_subscription=False, premium_until=None)) == "free"


def test_resolve_tier_free_when_plan_is_some_other_unrecognized_value():
    assert _resolve_tier(_state(plan="trial_expired")) == "free"


def test_resolve_tier_basic_tier_column_is_not_a_grant():
    # 'basic' is the tier column's legacy default value, not a grant.
    assert _resolve_tier(_state(tier_column="basic")) == "free"


# ---- is_premium_tier / PREMIUM_TIERS ------------------------------------

def test_premium_tiers_are_pro_and_elite_only():
    assert PREMIUM_TIERS == {"pro", "elite"}
    assert is_premium_tier("pro") is True
    assert is_premium_tier("elite") is True
    assert is_premium_tier("free") is False


# ---- _limits_for / _capabilities_for ------------------------------------
# Scenario 10: Premium/admin/elite get is_premium=True and every daily
# limit None (unlimited).

def test_limits_for_free_are_all_finite():
    limits = _limits_for("free")
    for key in (
        "daily_opportunity_limit", "daily_finder_limit", "active_alert_limit",
        "open_trade_limit", "portfolio_history_days", "ai_chat_daily_limit",
    ):
        assert limits[key] is not None and limits[key] > 0, f"{key} must be a finite Free limit"
    assert limits["watchlist_max"] > 0


def test_limits_for_pro_and_elite_have_every_daily_limit_unlimited():
    for tier in ("pro", "elite"):
        limits = _limits_for(tier)
        for key in (
            "daily_opportunity_limit", "daily_finder_limit", "active_alert_limit",
            "open_trade_limit", "portfolio_history_days", "ai_chat_daily_limit",
        ):
            assert limits[key] is None, f"{tier}'s {key} must be unlimited (None), got {limits[key]}"


def test_limits_for_elite_watchlist_exceeds_pro_exceeds_free():
    assert _limits_for("elite")["watchlist_max"] > _limits_for("pro")["watchlist_max"] > _limits_for("free")["watchlist_max"]


def test_capabilities_for_premium_tiers_are_fully_unlocked():
    for tier in ("pro", "elite"):
        limits = _limits_for(tier)
        caps = _capabilities_for(tier, limits)
        assert caps["is_premium"] is True
        assert caps["all_live_opportunities"] is True
        assert caps["full_price_history"] is True
        assert caps["full_history"] is True
        assert caps["companion_access"] is True
        assert caps["invest_full_feed"] is True
        for key in (
            "daily_opportunity_limit", "daily_finder_limit", "active_alert_limit",
            "open_trade_limit", "portfolio_history_days", "ai_chat_daily_limit",
        ):
            assert caps[key] is None


def test_capabilities_for_free_mirrors_free_limits_and_is_not_premium():
    limits = _limits_for("free")
    caps = _capabilities_for("free", limits)
    assert caps["is_premium"] is False
    assert caps["all_live_opportunities"] is False
    assert caps["full_price_history"] is False
    assert caps["companion_access"] is False
    assert caps["daily_opportunity_limit"] == limits["daily_opportunity_limit"]
    assert caps["watchlist_limit"] == limits["watchlist_max"]


# ---- compute_entitlements: SessionMiddleware tolerance ------------------
# The commit's own fix: compute_entitlements must default to anonymous/
# free instead of raising when SessionMiddleware isn't installed (no
# "session" key in the ASGI scope at all), so routers that call it can be
# exercised in tests that never wire up SessionMiddleware.

def _bare_request(scope_extra: Optional[Dict[str, Any]] = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "app": None,
    }
    if scope_extra:
        scope.update(scope_extra)
    return Request(scope)


def test_compute_entitlements_with_no_session_middleware_installed_is_anonymous_free():
    request = _bare_request()  # no "session" key in scope at all
    result = run_async(lambda: compute_entitlements(request))
    assert result["user_id"] is None
    assert result["tier"] == "free"
    assert result["is_premium"] is False
    assert result["capabilities"]["is_premium"] is False


def test_compute_entitlements_with_session_present_but_no_user_id_is_free():
    request = _bare_request({"session": {}})
    result = run_async(lambda: compute_entitlements(request))
    assert result["user_id"] is None
    assert result["tier"] == "free"


def test_compute_entitlements_anonymous_limits_match_free_limits():
    request = _bare_request()
    result = run_async(lambda: compute_entitlements(request))
    assert result["limits"] == _limits_for("free")


# ---- increment_daily_usage / get_daily_usage ----------------------------

class FakeUsagePool:
    """In-memory stand-in for the `daily_usage_counters` table (migration
    072) - one row per (user_id, usage_key, day), same shape
    increment_daily_usage's real INSERT .. ON CONFLICT DO UPDATE ..
    RETURNING count statement maintains. fetchrow does the increment in
    one call with no `await` between reading and writing the new count -
    mirroring the atomicity the real single SQL statement (and Postgres's
    per-row lock) gives increment_daily_usage against concurrent callers,
    which is exactly the property scenario 9 below is checking for."""

    def __init__(self):
        self.rows: Dict[tuple, int] = {}

    async def fetchrow(self, query: str, *args):
        user_id, usage_key, day = args
        key = (str(user_id), usage_key, day)
        if "INSERT INTO daily_usage_counters" in query:
            self.rows[key] = self.rows.get(key, 0) + 1
            return {"count": self.rows[key]}
        if "SELECT count FROM daily_usage_counters" in query:
            count = self.rows.get(key)
            return {"count": count} if count is not None else None
        raise AssertionError(f"unexpected query: {query}")


def test_increment_daily_usage_unlimited_skips_the_table_entirely():
    pool = FakeUsagePool()

    async def run():
        return await increment_daily_usage(pool, "u1", "ai_chat_message", limit=None)

    result = run_async(run)
    assert result == {"allowed": True, "used": None, "limit": None, "remaining": None}
    assert pool.rows == {}, "Premium (limit=None) must never write a usage row"


def test_increment_daily_usage_counts_up_and_reports_remaining():
    pool = FakeUsagePool()

    async def run():
        first = await increment_daily_usage(pool, "u1", "trade_finder_search", limit=3)
        second = await increment_daily_usage(pool, "u1", "trade_finder_search", limit=3)
        return first, second

    first, second = run_async(run)
    assert first == {"allowed": True, "used": 1, "limit": 3, "remaining": 2}
    assert second == {"allowed": True, "used": 2, "limit": 3, "remaining": 1}


def test_increment_daily_usage_blocks_once_over_limit():
    pool = FakeUsagePool()

    async def run():
        results = []
        for _ in range(5):
            results.append(await increment_daily_usage(pool, "u1", "trade_finder_search", limit=3))
        return results

    results = run_async(run)
    allowed_flags = [r["allowed"] for r in results]
    assert allowed_flags == [True, True, True, False, False]
    assert results[-1]["remaining"] == 0


def test_increment_daily_usage_is_per_user():
    pool = FakeUsagePool()

    async def run():
        a = await increment_daily_usage(pool, "user-a", "ai_chat_message", limit=1)
        b = await increment_daily_usage(pool, "user-b", "ai_chat_message", limit=1)
        return a, b

    a, b = run_async(run)
    assert a["allowed"] is True and a["used"] == 1
    assert b["allowed"] is True and b["used"] == 1  # user-b's own separate allowance


def test_increment_daily_usage_is_per_usage_key():
    pool = FakeUsagePool()

    async def run():
        opp = await increment_daily_usage(pool, "u1", "trade_finder_search", limit=1)
        chat = await increment_daily_usage(pool, "u1", "ai_chat_message", limit=1)
        return opp, chat

    opp, chat = run_async(run)
    assert opp["used"] == 1
    assert chat["used"] == 1  # a separate counter, not shared with trade_finder_search


def test_get_daily_usage_is_read_only_and_does_not_increment():
    pool = FakeUsagePool()

    async def run():
        await increment_daily_usage(pool, "u1", "ai_chat_message", limit=3)
        before = await get_daily_usage(pool, "u1", "ai_chat_message", limit=3)
        after = await get_daily_usage(pool, "u1", "ai_chat_message", limit=3)
        return before, after

    before, after = run_async(run)
    assert before == after == {"used": 1, "limit": 3, "remaining": 2}


def test_get_daily_usage_unlimited_short_circuits():
    pool = FakeUsagePool()

    async def run():
        return await get_daily_usage(pool, "u1", "ai_chat_message", limit=None)

    result = run_async(run)
    assert result == {"used": None, "limit": None, "remaining": None}


def test_get_daily_usage_with_no_row_yet_reports_zero_used():
    pool = FakeUsagePool()

    async def run():
        return await get_daily_usage(pool, "brand-new-user", "ai_chat_message", limit=3)

    result = run_async(run)
    assert result == {"used": 0, "limit": 3, "remaining": 3}


# ---- Scenario 9: concurrency-safe increments ----------------------------

def test_increment_daily_usage_concurrent_calls_are_not_double_counted_or_lost():
    """Two "simultaneous" increments for the same user (e.g. two browser
    tabs both hitting Trade Finder at once) must land as two distinct,
    correctly-numbered uses - never both reading count=0 and both writing
    count=1 (lost update), and never skipping a number."""
    pool = FakeUsagePool()

    async def run():
        results = await asyncio.gather(
            increment_daily_usage(pool, "u1", "trade_finder_search", limit=2),
            increment_daily_usage(pool, "u1", "trade_finder_search", limit=2),
            increment_daily_usage(pool, "u1", "trade_finder_search", limit=2),
        )
        return results

    results = run_async(run)
    used_values = sorted(r["used"] for r in results)
    assert used_values == [1, 2, 2]  # third use is capped-displayed at 2 (== limit) per increment_daily_usage's min(used, limit)
    allowed_flags = sorted(r["allowed"] for r in results)
    assert allowed_flags == [False, True, True]
    # The underlying counter itself must have actually reached 3 (all
    # three increments recorded), even though only 2 were "allowed".
    assert pool.rows[("u1", "trade_finder_search", pool.rows and next(iter(pool.rows))[2])] == 3


def test_increment_daily_usage_high_concurrency_reaches_exact_final_count():
    pool = FakeUsagePool()

    async def run():
        return await asyncio.gather(
            *(increment_daily_usage(pool, "u1", "ai_chat_message", limit=100) for _ in range(25))
        )

    results = run_async(run)
    used_values = sorted(r["used"] for r in results)
    assert used_values == list(range(1, 26)), "25 concurrent increments must produce exactly 1..25, no gaps or dupes"


# ---- Scenario 12: usage reset across the UTC day boundary ---------------

def test_increment_daily_usage_resets_across_day_boundary(monkeypatch):
    pool = FakeUsagePool()
    day_one = datetime(2026, 8, 25, 23, 59, tzinfo=timezone.utc)
    day_two = datetime(2026, 8, 26, 0, 1, tzinfo=timezone.utc)

    async def run_day(fixed_now):
        monkeypatch.setattr(ent_module, "_now", lambda: fixed_now)
        return await increment_daily_usage(pool, "u1", "ai_chat_message", limit=3)

    yesterday_result = run_async(lambda: run_day(day_one))
    today_result = run_async(lambda: run_day(day_two))

    assert yesterday_result["used"] == 1
    # A new UTC day is a completely separate counter - not a continuation
    # of yesterday's count.
    assert today_result["used"] == 1
    assert len(pool.rows) == 2


def test_get_daily_usage_does_not_see_yesterdays_count_today(monkeypatch):
    pool = FakeUsagePool()
    yesterday = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    today = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)

    async def bump_yesterday():
        monkeypatch.setattr(ent_module, "_now", lambda: yesterday)
        for _ in range(3):
            await increment_daily_usage(pool, "u1", "trade_finder_search", limit=3)

    async def read_today():
        monkeypatch.setattr(ent_module, "_now", lambda: today)
        return await get_daily_usage(pool, "u1", "trade_finder_search", limit=3)

    run_async(bump_yesterday)
    today_usage = run_async(read_today)
    assert today_usage == {"used": 0, "limit": 3, "remaining": 3}
