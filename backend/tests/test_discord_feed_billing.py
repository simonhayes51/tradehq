import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.discord_feed_billing as billing


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Budget allocations - must match the spec's totals exactly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plan,expected_total", [("server_feed", 6), ("server_feed_plus", 15)])
def test_every_budget_mix_sums_to_the_plan_daily_allowance(plan, expected_total):
    for mix, (low, medium, high) in billing.BUDGET_ALLOCATIONS[plan].items():
        assert low + medium + high == expected_total, f"{plan}/{mix} sums to {low+medium+high}, expected {expected_total}"


def test_server_feed_balanced_allocation_matches_spec():
    assert billing.BUDGET_ALLOCATIONS["server_feed"]["balanced"] == (2, 2, 2)
    assert billing.BUDGET_ALLOCATIONS["server_feed"]["mostly_low"] == (4, 2, 0)
    assert billing.BUDGET_ALLOCATIONS["server_feed"]["mostly_medium"] == (1, 4, 1)
    assert billing.BUDGET_ALLOCATIONS["server_feed"]["mostly_high"] == (0, 2, 4)


def test_server_feed_plus_balanced_allocation_matches_spec():
    assert billing.BUDGET_ALLOCATIONS["server_feed_plus"]["balanced"] == (5, 5, 5)
    assert billing.BUDGET_ALLOCATIONS["server_feed_plus"]["mostly_low"] == (10, 5, 0)
    assert billing.BUDGET_ALLOCATIONS["server_feed_plus"]["mostly_medium"] == (2, 11, 2)
    assert billing.BUDGET_ALLOCATIONS["server_feed_plus"]["mostly_high"] == (0, 5, 10)


def test_plan_prices_and_daily_caps():
    assert billing.PLANS["server_feed"]["price_gbp"] == 9.99
    assert billing.PLANS["server_feed"]["max_posts_per_day"] == 6
    assert billing.PLANS["server_feed_plus"]["price_gbp"] == 19.99
    assert billing.PLANS["server_feed_plus"]["max_posts_per_day"] == 15


def test_validate_budget_allocation_rejects_over_allowance():
    with pytest.raises(billing.FeedError):
        billing.validate_budget_allocation("server_feed", 4, 4, 4)  # sums to 12 > 6


def test_validate_budget_allocation_accepts_exact_allowance():
    billing.validate_budget_allocation("server_feed", 2, 2, 2)  # no raise


def test_validate_budget_allocation_rejects_negative():
    with pytest.raises(billing.FeedError):
        billing.validate_budget_allocation("server_feed", -1, 2, 2)


# ---------------------------------------------------------------------------
# Budget band classification - reuses the app's existing price-band convention
# ---------------------------------------------------------------------------


def test_classify_budget_band_thresholds():
    assert billing.classify_budget_band(10_000) == "low"
    assert billing.classify_budget_band(24_999) == "low"
    assert billing.classify_budget_band(25_000) == "medium"
    assert billing.classify_budget_band(99_999) == "medium"
    assert billing.classify_budget_band(100_000) == "high"
    assert billing.classify_budget_band(500_000) == "high"


def test_engine_platform_mapping():
    assert billing.engine_platform_for("console") == "ps"
    assert billing.engine_platform_for("both") == "ps"
    assert billing.engine_platform_for("pc") == "pc"


# ---------------------------------------------------------------------------
# One subscription -> one guild; re-running setup updates, never duplicates
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, existing_status=None):
        self.executed = []
        self._existing_status = existing_status

    async def fetchrow(self, query, *args):
        if "SELECT id, subscription_status" in query:
            if self._existing_status is None:
                return None
            return {"id": 1, "subscription_status": self._existing_status}
        if "INSERT INTO discord_servers" in query:
            return {"id": 1}
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def fetchval(self, *a, **kw):
        return None


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False

    async def fetchrow(self, *a, **kw):
        return await self._conn.fetchrow(*a, **kw)

    async def execute(self, *a, **kw):
        return await self._conn.execute(*a, **kw)

    async def fetchval(self, *a, **kw):
        return await self._conn.fetchval(*a, **kw)


def test_setup_rejects_new_checkout_when_subscription_already_active(monkeypatch):
    conn = _FakeConn(existing_status="active")
    pool = _FakePool(conn)

    async def fake_get_core_pool():
        return pool

    monkeypatch.setattr(billing, "get_core_pool", fake_get_core_pool)
    with pytest.raises(billing.FeedError, match="already has an active"):
        run(billing.upsert_pending_setup(
            guild_id="g1", guild_name="Test Guild", owner_discord_id="1",
            plan="server_feed", channel_id="c1", platform_scope="console", budget_mix="balanced",
        ))


def test_setup_allows_new_checkout_when_unpaid(monkeypatch):
    conn = _FakeConn(existing_status="unpaid")
    pool = _FakePool(conn)

    async def fake_get_core_pool():
        return pool

    monkeypatch.setattr(billing, "get_core_pool", fake_get_core_pool)
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={"id": 1, "plan": "server_feed"}))
    result = run(billing.upsert_pending_setup(
        guild_id="g1", guild_name="Test Guild", owner_discord_id="1",
        plan="server_feed", channel_id="c1", platform_scope="console", budget_mix="balanced",
    ))
    assert result == {"id": 1, "plan": "server_feed"}


# ---------------------------------------------------------------------------
# Preferences: budget-mix changes are still validated against the plan cap
# ---------------------------------------------------------------------------


def test_set_preferences_rejects_a_mix_that_would_exceed_plan_allowance(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "plan": "server_feed", "budget_mix": "balanced",
    }))
    # server_feed has no mix that exceeds 6 by construction, so simulate a
    # corrupted/future plan value to prove the guard actually runs.
    monkeypatch.setitem(billing.PLANS, "server_feed", {**billing.PLANS["server_feed"], "max_posts_per_day": 1})
    with pytest.raises(billing.FeedError):
        run(billing.set_preferences(guild_id="g1", admin_id="1", budget_mix="balanced"))


def test_set_preferences_rejects_unlisted_confidence_value(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "plan": "server_feed", "budget_mix": "balanced",
    }))
    with pytest.raises(billing.FeedError):
        run(billing.set_preferences(guild_id="g1", admin_id="1", min_confidence=0.42))


# ---------------------------------------------------------------------------
# Posting window validation and persistence
# ---------------------------------------------------------------------------


def test_validate_posting_window_accepts_no_window():
    billing.validate_posting_window(None, None, None)  # no raise


def test_validate_posting_window_requires_both_hours_together():
    with pytest.raises(billing.FeedError):
        billing.validate_posting_window(8, None, "UTC")
    with pytest.raises(billing.FeedError):
        billing.validate_posting_window(None, 23, "UTC")


def test_validate_posting_window_rejects_out_of_range_hours():
    with pytest.raises(billing.FeedError):
        billing.validate_posting_window(-1, 23, None)
    with pytest.raises(billing.FeedError):
        billing.validate_posting_window(8, 24, None)


def test_validate_posting_window_rejects_unknown_timezone():
    with pytest.raises(billing.FeedError, match="recognised timezone"):
        billing.validate_posting_window(8, 23, "Not/AZone")


def test_validate_posting_window_accepts_a_real_timezone():
    billing.validate_posting_window(8, 23, "Europe/London")  # no raise


def test_set_preferences_rejects_invalid_posting_window(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "plan": "server_feed", "budget_mix": "balanced",
    }))
    with pytest.raises(billing.FeedError):
        run(billing.set_preferences(guild_id="g1", admin_id="1", posting_window_start_hour=8))


def test_set_preferences_persists_posting_window(monkeypatch):
    conn = _FakeConn()
    pool = _FakePool(conn)
    monkeypatch.setattr(billing, "get_core_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "plan": "server_feed", "budget_mix": "balanced",
    }))
    run(billing.set_preferences(
        guild_id="g1", admin_id="1",
        posting_window_start_hour=8, posting_window_end_hour=23, posting_timezone="Europe/London",
    ))
    server_update = next(q for q, a in conn.executed if "posting_window_start_hour = $7" in q)
    assert server_update  # the window UPDATE branch ran, not the plain one


def test_set_preferences_clears_posting_window(monkeypatch):
    conn = _FakeConn()
    pool = _FakePool(conn)
    monkeypatch.setattr(billing, "get_core_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "plan": "server_feed", "budget_mix": "balanced",
    }))
    run(billing.set_preferences(guild_id="g1", admin_id="1", clear_posting_window=True))
    server_update = next(q for q, a in conn.executed if "posting_window_start_hour = NULL" in q)
    assert server_update


# ---------------------------------------------------------------------------
# Sell-time channel: a server may route opportunity_update ("TARGET HIT" /
# result) posts to a separate channel from its main buy feed. None clears
# it back to the default (post results in the main feed channel).
# ---------------------------------------------------------------------------


def test_set_sell_channel_requires_setup_first(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value=None))
    with pytest.raises(billing.FeedError):
        run(billing.set_sell_channel(guild_id="g1", admin_id="1", channel_id="chan-sell"))


def test_set_sell_channel_verifies_bot_access_before_saving(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={"id": 1}))
    monkeypatch.setattr(billing, "confirm_channel_access", AsyncMock(return_value=(False, "missing_permissions")))
    with pytest.raises(billing.FeedError):
        run(billing.set_sell_channel(guild_id="g1", admin_id="1", channel_id="chan-sell"))


def test_set_sell_channel_persists_the_channel(monkeypatch):
    conn = _FakeConn()
    pool = _FakePool(conn)
    monkeypatch.setattr(billing, "get_core_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={"id": 1}))
    monkeypatch.setattr(billing, "confirm_channel_access", AsyncMock(return_value=(True, None)))
    run(billing.set_sell_channel(guild_id="g1", admin_id="1", channel_id="chan-sell"))
    query, args = next(qa for qa in conn.executed if "sell_channel_id" in qa[0])
    assert args == ("g1", "chan-sell")


def test_set_sell_channel_none_clears_it_without_checking_access(monkeypatch):
    """channel_id=None means 'go back to the main feed channel' - there is
    no channel to verify bot access to, so confirm_channel_access must
    never be called."""
    conn = _FakeConn()
    pool = _FakePool(conn)
    monkeypatch.setattr(billing, "get_core_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={"id": 1}))
    confirm = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(billing, "confirm_channel_access", confirm)
    run(billing.set_sell_channel(guild_id="g1", admin_id="1", channel_id=None))
    confirm.assert_not_awaited()
    query, args = next(qa for qa in conn.executed if "sell_channel_id" in qa[0])
    assert args == ("g1", None)


# ---------------------------------------------------------------------------
# Activation gating: subscription + channel + test post, all required
# ---------------------------------------------------------------------------


def test_activate_requires_active_subscription(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "subscription_status": "unpaid", "feed_channel_id": "c1", "test_post_confirmed_at": datetime.now(timezone.utc),
    }))
    with pytest.raises(billing.FeedError, match="subscription"):
        run(billing.activate(guild_id="g1", admin_id="1"))


def test_activate_requires_a_channel(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "subscription_status": "active", "feed_channel_id": None, "test_post_confirmed_at": datetime.now(timezone.utc),
    }))
    with pytest.raises(billing.FeedError, match="channel"):
        run(billing.activate(guild_id="g1", admin_id="1"))


def test_activate_requires_a_confirmed_test_post(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "subscription_status": "active", "feed_channel_id": "c1", "test_post_confirmed_at": None,
    }))
    monkeypatch.setattr(billing, "confirm_channel_access", AsyncMock(return_value=(True, None)))
    with pytest.raises(billing.FeedError, match="test"):
        run(billing.activate(guild_id="g1", admin_id="1"))


def test_activate_fails_if_channel_permissions_are_missing(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "subscription_status": "active", "feed_channel_id": "c1", "test_post_confirmed_at": datetime.now(timezone.utc),
    }))
    monkeypatch.setattr(billing, "confirm_channel_access", AsyncMock(return_value=(False, "missing_permissions")))
    with pytest.raises(billing.FeedError, match="View Channel"):
        run(billing.activate(guild_id="g1", admin_id="1"))


def test_activate_succeeds_when_every_condition_is_met(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(side_effect=[
        {"id": 1, "subscription_status": "active", "feed_channel_id": "c1", "test_post_confirmed_at": datetime.now(timezone.utc)},
        {"id": 1, "status": "active", "feed_channel_id": "c1"},
    ]))
    monkeypatch.setattr(billing, "confirm_channel_access", AsyncMock(return_value=(True, None)))

    conn = _FakeConn()
    pool = _FakePool(conn)

    async def fake_get_core_pool():
        return pool

    monkeypatch.setattr(billing, "get_core_pool", fake_get_core_pool)
    result = run(billing.activate(guild_id="g1", admin_id="1"))
    assert result["status"] == "active"


# ---------------------------------------------------------------------------
# Pause never touches billing; resume re-validates subscription+channel
# ---------------------------------------------------------------------------


def test_pause_does_not_touch_subscription_status(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={"id": 1, "subscription_status": "active"}))
    conn = _FakeConn()
    pool = _FakePool(conn)

    async def fake_get_core_pool():
        return pool

    monkeypatch.setattr(billing, "get_core_pool", fake_get_core_pool)
    run(billing.pause(guild_id="g1", admin_id="1"))
    queries = " ".join(q for q, _ in conn.executed)
    assert "subscription_status" not in queries
    assert "status='paused'" in queries


def test_resume_blocked_without_active_subscription(monkeypatch):
    monkeypatch.setattr(billing, "get_guild_feed", AsyncMock(return_value={
        "id": 1, "subscription_status": "canceled", "feed_channel_id": "c1",
    }))
    with pytest.raises(billing.FeedError, match="subscription"):
        run(billing.resume(guild_id="g1", admin_id="1"))


# ---------------------------------------------------------------------------
# Stripe webhook routing: discord_feed objects never touch the per-user path
# ---------------------------------------------------------------------------


def test_is_discord_feed_object_true_for_tagged_metadata():
    assert billing.is_discord_feed_object({"metadata": {"kind": "discord_feed", "guild_id": "1"}}) is True


def test_is_discord_feed_object_false_for_untagged_or_missing_metadata():
    assert billing.is_discord_feed_object({"metadata": {"user_id": "abc"}}) is False
    assert billing.is_discord_feed_object({}) is False


def test_subscription_created_writes_guild_scoped_fields(monkeypatch):
    conn = _FakeConn()
    subscription = {
        "id": "sub_123", "customer": "cus_123", "status": "active",
        "current_period_end": 1700000000,
        "metadata": {"kind": "discord_feed", "guild_id": "g1"},
    }
    run(billing.handle_discord_feed_subscription_created(subscription, conn))
    assert len(conn.executed) == 1
    query, args = conn.executed[0]
    assert "discord_servers" in query
    assert "g1" in args


def test_subscription_created_falls_back_to_item_level_period_end(monkeypatch):
    """Regression: on newer Stripe API versions (confirmed via a real
    webhook payload on 2025-08-27.basil) current_period_end is absent
    from the top-level Subscription object and only present on its first
    item - without the fallback, /feed status's PAID THROUGH silently
    showed 'n/a' even for a genuinely active, paid subscription."""
    conn = _FakeConn()
    subscription = {
        "id": "sub_123", "customer": "cus_123", "status": "active",
        "metadata": {"kind": "discord_feed", "guild_id": "g1"},
        "items": {"data": [{"current_period_end": 1790244881, "current_period_start": 1787566481}]},
    }
    run(billing.handle_discord_feed_subscription_created(subscription, conn))
    query, args = conn.executed[0]
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(1790244881, tz=timezone.utc) in args


def test_subscription_deleted_forces_paused_and_canceled(monkeypatch):
    conn = _FakeConn()
    subscription = {"id": "sub_123", "metadata": {"kind": "discord_feed", "guild_id": "g1"}}
    run(billing.handle_discord_feed_subscription_deleted(subscription, conn))
    query, args = conn.executed[0]
    assert "canceled" in query
    assert "paused" in query


def test_subscription_created_with_no_resolvable_guild_is_a_noop():
    conn = _FakeConn()
    subscription = {"id": "sub_x", "metadata": {"kind": "discord_feed"}}  # no guild_id anywhere
    run(billing.handle_discord_feed_subscription_created(subscription, conn))
    assert conn.executed == []


# ---------------------------------------------------------------------------
# Checkout session must allow promo codes - without this Stripe Checkout
# shows no promo-code field at all, regardless of any Coupon/Promotion
# Code configured in the Dashboard.
# ---------------------------------------------------------------------------


def test_checkout_session_allows_promotion_codes(monkeypatch):
    monkeypatch.setenv("STRIPE_SERVER_FEED_PRICE_ID", "price_test123")
    conn = _FakeConn()
    pool = _FakePool(conn)

    async def fake_get_core_pool():
        return pool

    monkeypatch.setattr(billing, "get_core_pool", fake_get_core_pool)

    import stripe

    fake_session = MagicMock(id="cs_test_1", url="https://checkout.stripe.com/x")
    create_mock = MagicMock(return_value=fake_session)
    monkeypatch.setattr(stripe.checkout.Session, "create", create_mock)

    run(billing.create_checkout_session(
        guild_id="g1", owner_discord_id="1", plan="server_feed",
        success_url="https://app.eafc.live/success", cancel_url="https://app.eafc.live/cancel",
    ))
    assert create_mock.call_args.kwargs["allow_promotion_codes"] is True


# ---------------------------------------------------------------------------
# Regression: webhook handlers require a plain dict, not a typed stripe-python
# resource object. Newer stripe-python releases stopped making Subscription/
# Session dict subclasses - .get() on one raises AttributeError ("'get' is a
# dict method, but a Subscription is not a dict"), which is exactly what
# stripe_events.processing_error recorded for the real failing
# customer.subscription.created event. main.py's webhook route now parses
# the raw JSON payload instead of using stripe.Webhook.construct_event()'s
# typed event.data.object specifically so these handlers always receive a
# real dict - this test locks in that these handlers require exactly that.
# ---------------------------------------------------------------------------


def test_subscription_created_raises_on_a_typed_stripe_object_not_a_dict():
    import stripe

    typed_subscription = stripe.Subscription.construct_from(
        {"id": "sub_123", "customer": "cus_123", "status": "active",
         "metadata": {"kind": "discord_feed", "guild_id": "g1"}},
        "sk_test_x",
    )
    assert not isinstance(typed_subscription, dict)
    with pytest.raises(AttributeError):
        run(billing.handle_discord_feed_subscription_created(typed_subscription, _FakeConn()))


def test_subscription_created_succeeds_on_a_plain_dict():
    """Same payload shape, as a plain dict (what main.py now guarantees
    by parsing the raw JSON body) - must not raise."""
    conn = _FakeConn()
    plain_subscription = {
        "id": "sub_123", "customer": "cus_123", "status": "active",
        "metadata": {"kind": "discord_feed", "guild_id": "g1"},
    }
    run(billing.handle_discord_feed_subscription_created(plain_subscription, conn))
    assert len(conn.executed) == 1
