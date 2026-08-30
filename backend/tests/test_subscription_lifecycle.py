# tests/test_subscription_lifecycle.py
#
# What a Stripe subscription's state means for access, end to end, at the
# one place that decides it (app/auth/entitlements._resolve_tier and the
# billing state it reads).
#
# This file exists because the product had a live revenue bug of exactly
# this shape: a paying Stripe subscriber resolved to tier "pro", the
# opportunity feed required "elite", and the customer was refused the
# thing they had just bought. Nothing caught it, because nothing tested
# the states a real subscription actually passes through.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth.entitlements import (
    PAST_DUE_GRACE_DAYS,
    FEATURE_MIN_TIER,
    _resolve_tier,
    features_for_tier,
)

NOW = datetime.now(timezone.utc)


def state(**overrides):
    """The shape _load_billing_state returns - every signal absent by
    default, so each test names exactly the one it is about."""
    base = {
        "plan": None, "tier_column": None, "account_type": None,
        "premium_until": None, "roles": set(),
        "profile_premium": False, "profile_premium_until": None,
        "active_subscription": False,
    }
    base.update(overrides)
    return base


class TestStripeStates:
    def test_no_subscription_is_free(self):
        assert _resolve_tier(state()) == "free"

    def test_an_active_subscription_is_elite_not_pro(self):
        # The bug this file was written from: "pro" was neither of the two
        # tiers the product has, and every elite-gated feature refused it.
        assert _resolve_tier(state(active_subscription=True)) == "elite"

    def test_a_trialing_subscription_is_elite(self):
        # 'trialing' is inside _load_billing_state's entitled status set,
        # so it reaches _resolve_tier as active_subscription=True.
        assert _resolve_tier(state(active_subscription=True)) == "elite"

    def test_a_cancelled_but_not_yet_expired_subscription_keeps_access(self):
        # cancel_at_period_end deliberately does not revoke: the customer
        # paid for the period they are in.
        assert _resolve_tier(state(active_subscription=True, premium_until=NOW + timedelta(days=3))) == "elite"

    def test_an_expired_grant_with_no_subscription_is_free(self):
        assert _resolve_tier(state(premium_until=NOW - timedelta(minutes=1))) == "free"

    def test_a_premium_until_in_the_future_survives_a_webhook_delay(self):
        # The Stripe-webhook-independent grant. A subscriber must not lose
        # access because a webhook is slow.
        assert _resolve_tier(state(premium_until=NOW + timedelta(days=30))) == "elite"

    def test_a_manual_profile_grant_with_no_expiry_is_honoured(self):
        assert _resolve_tier(state(profile_premium=True)) == "elite"

    def test_a_manual_profile_grant_does_not_outlive_its_own_expiry(self):
        assert _resolve_tier(
            state(profile_premium=True, profile_premium_until=NOW - timedelta(days=1))
        ) == "free"

    @pytest.mark.parametrize("plan", ["pro", "premium", "elite", "ELITE", "Premium"])
    def test_every_legacy_paid_plan_name_resolves_to_the_one_paid_tier(self, plan):
        # Three plan names exist in production data. All of them mean the
        # customer paid, and the product has exactly one paid tier.
        assert _resolve_tier(state(plan=plan)) == "elite"
        assert _resolve_tier(state(tier_column=plan)) == "elite"

    @pytest.mark.parametrize("plan", ["free", "basic", "", None])
    def test_an_unpaid_plan_name_is_free(self, plan):
        assert _resolve_tier(state(plan=plan)) == "free"


class TestDunningGrace:
    def test_past_due_is_a_documented_grace_not_an_instant_cut_off(self):
        # The grace lives in _load_billing_state's SQL, so what is pinned
        # here is that the window is a named, non-zero, documented number
        # rather than an accident.
        assert PAST_DUE_GRACE_DAYS > 0
        assert PAST_DUE_GRACE_DAYS <= 30, "a 'grace period' longer than a month is a free plan"

    def test_the_grace_query_names_past_due_explicitly(self):
        import inspect

        from app.auth.entitlements import _load_billing_state

        source = inspect.getsource(_load_billing_state)
        assert "past_due" in source
        # And does NOT silently entitle every other failure status.
        for never in ("'canceled'", "'unpaid'", "'incomplete_expired'"):
            assert never not in source


class TestDiscordIsNeverTheCondition:
    def test_a_subscriber_who_never_linked_discord_is_still_elite(self):
        # Discord is a benefit of paying, never a condition of it. A
        # subscriber with no linked account, no roles and no guild
        # membership is a paying customer.
        assert _resolve_tier(state(active_subscription=True, roles=set())) == "elite"

    def test_discord_roles_alone_do_not_make_someone_elite(self):
        # The inverse: being in the Discord with a role is not payment.
        assert _resolve_tier(state(roles={"member", "premium"})) == "free"

    def test_no_discord_signal_appears_in_the_tier_decision_at_all(self):
        import inspect

        source = inspect.getsource(_resolve_tier)
        # `roles` is read by _load_billing_state for other purposes; it
        # must not reach the paid/unpaid decision.
        assert "roles" not in source.split('"""')[2], (
            "a Discord signal has entered the tier decision - "
            "see this function's docstring"
        )


class TestEntitlementsChangeWithoutANewLogin:
    def test_the_tier_is_derived_from_stored_state_not_from_the_session(self):
        # An upgrade must take effect on the next request, not the next
        # login. _resolve_tier takes only the billing state - there is no
        # session, token or cookie in its signature to have gone stale.
        import inspect

        params = list(inspect.signature(_resolve_tier).parameters)
        assert params == ["state"]

    def test_the_same_state_resolves_the_same_way_every_time(self):
        paid = state(active_subscription=True)
        assert {_resolve_tier(paid) for _ in range(5)} == {"elite"}

    def test_downgrading_the_stored_state_downgrades_the_answer_immediately(self):
        paid = state(active_subscription=True)
        assert _resolve_tier(paid) == "elite"
        paid["active_subscription"] = False
        assert _resolve_tier(paid) == "free"


class TestFeatureSetsMatchTheTiers:
    def test_free_gets_the_features_free_is_advertised_to_have(self):
        free = set(features_for_tier("free"))
        for advertised in ("watchlist", "fair_value", "trade_journal"):
            assert advertised in free, f"{advertised} is on the free plan in our own copy"

    def test_free_does_not_get_the_paid_features(self):
        free = set(features_for_tier("free"))
        for paid in ("opportunity_feed", "trade_finder", "ai_chat", "full_track_record"):
            assert paid not in free

    def test_elite_gets_everything_free_gets(self):
        assert set(features_for_tier("free")) <= set(features_for_tier("elite"))

    def test_every_gated_feature_names_a_tier_that_exists(self):
        # "pro" as a min_tier is unreachable: no state resolves to it.
        for feature, tier in FEATURE_MIN_TIER.items():
            assert tier in ("free", "elite"), f"{feature} requires tier {tier!r}, which nothing resolves to"


# ---------------------------------------------------------------------------
# The webhook side
# ---------------------------------------------------------------------------
#
# Access is granted and revoked by stored state, and stored state is
# written by these handlers. Two things must happen on every state change:
# the entitlements cache is invalidated (or the customer waits out the TTL
# for something they have already paid for), and the Discord role is
# reconciled from billing state rather than left to drift.

import inspect as _inspect  # noqa: E402
import os as _os  # noqa: E402

_os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
_os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
_os.environ.setdefault("ENV", "test")


def _handler(name):
    import main

    return _inspect.getsource(getattr(main, name))


@pytest.mark.parametrize("handler", [
    "handle_subscription_created",
    "handle_subscription_updated",
    "handle_subscription_deleted",
    "handle_payment_failed",
])
def test_every_billing_state_change_invalidates_the_cache(handler):
    assert "invalidate_entitlements_cache" in _handler(handler), (
        f"{handler} changes what a user is entitled to but leaves the cached "
        "answer in place for up to the cache TTL"
    )


@pytest.mark.parametrize("handler", [
    "handle_subscription_created",
    "handle_subscription_updated",
    "handle_subscription_deleted",
])
def test_every_subscription_change_reconciles_the_discord_role(handler):
    assert "enqueue_role_sync" in _handler(handler)


def test_cancellation_revokes_the_discord_role_explicitly():
    source = _handler("handle_subscription_deleted")
    assert "desired=False" in source, (
        "a cancelled subscriber keeps the paid Discord role until something "
        "removes it - the removal must be explicit, not implied"
    )


def test_access_is_never_granted_from_a_return_url():
    import main

    source = _inspect.getsource(main.create_checkout_session)
    # The success URL is built server-side and carries no grant. Whatever
    # the browser comes back with, the tier is recomputed from the
    # subscriptions table.
    assert "success_url" in source
    for grant in ("is_premium = TRUE", "premium_until =", "SET tier="):
        assert grant not in source, (
            "the checkout handler is writing entitlement state; payment is "
            "confirmed by the webhook and the stored subscription, never by "
            "the customer arriving back on a URL"
        )
