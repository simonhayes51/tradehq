# tests/test_entitlement_single_source.py
#
# The Account page could show "CURRENT PLAN: ELITE" directly above
# "Membership access: Free", because two code paths answered "is this
# user paid?" with different rules. These tests pin the answer to one
# resolver.
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.auth import entitlements
from app.services.discord_integration import desired_member_access

NOW = datetime.now(timezone.utc)


class FakeDb:
    """Minimal stand-in for an asyncpg connection: only fetchrow is used
    by the code under test."""

    def __init__(self, *, user=None, profile=None, subscription=None):
        self.user = user
        self.profile = profile
        self.subscription = subscription

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        if "FROM users" in q:
            return self.user
        if "FROM user_profiles" in q:
            return self.profile
        if "FROM subscriptions" in q:
            # _load_billing_state's query filters status and expiry in
            # SQL ("SELECT 1 ... AND status IN (...) AND (end IS NULL OR
            # end > NOW())"), so the fake has to apply the same filter or
            # it would report an expired subscription as active and the
            # test would be asserting against a fiction.
            sub = self.subscription
            if sub is None:
                return None
            if "SELECT 1" in q:
                active = str(sub.get("status") or "").lower() in {"active", "trialing", "trial", "past_due"}
                end = sub.get("current_period_end")
                return {"?column?": 1} if active and (end is None or end > NOW) else None
            return sub
        return None


def _user(**overrides):
    base = {
        "discord_id": "9999", "plan": None, "tier": None, "account_type": None,
        "premium_until": None, "roles": None,
    }
    base.update(overrides)
    return base


def test_elite_plan_is_entitled_in_discord_too():
    db = FakeDb(user=_user(plan="elite"))
    discord_id, desired = asyncio.run(desired_member_access(db, "user-1"))
    assert discord_id == "9999"
    assert desired is True
    assert asyncio.run(entitlements.resolve_user_tier(db, "user-1", discord_id="9999")) == "elite"


def test_admin_granted_by_env_is_entitled_in_discord_too(monkeypatch):
    # The concrete divergence: entitlements.py honours ADMIN_DISCORD_IDS
    # and grants elite; the Discord side never looked at it, so an admin
    # saw ELITE beside "Free".
    monkeypatch.setattr(entitlements, "ADMIN_DISCORD_IDS", {"9999"})
    db = FakeDb(user=_user())
    _, desired = asyncio.run(desired_member_access(db, "user-1"))
    assert desired is True


def test_profile_premium_grant_is_entitled_in_discord_too():
    # Second divergence: user_profiles.is_premium + an active subscription
    # made someone paid for the app, and Free for Discord.
    db = FakeDb(
        user=_user(),
        profile={"is_premium": True, "premium_until": NOW + timedelta(days=30)},
        subscription={"status": "active", "current_period_end": NOW + timedelta(days=30)},
    )
    _, desired = asyncio.run(desired_member_access(db, "user-1"))
    assert desired is True


def test_entitlement_and_discord_link_are_independent_facts():
    # Third divergence: no linked Discord id used to force "not paid".
    # Being unable to grant a Discord role says nothing about whether the
    # account is paid, and the two must be reported separately.
    db = FakeDb(user=_user(discord_id=None, plan="elite"))
    discord_id, desired = asyncio.run(desired_member_access(db, "user-1"))
    assert discord_id is None       # nothing to sync a role to
    assert desired is True          # ...but they are still a paying customer


def test_free_user_is_free_on_both_sides():
    db = FakeDb(user=_user())
    _, desired = asyncio.run(desired_member_access(db, "user-1"))
    assert desired is False
    assert asyncio.run(entitlements.resolve_user_tier(db, "user-1")) == "free"


def test_expired_subscription_is_not_entitled():
    db = FakeDb(
        user=_user(),
        subscription={"status": "active", "current_period_end": NOW - timedelta(days=1)},
    )
    _, desired = asyncio.run(desired_member_access(db, "user-1"))
    assert desired is False


def test_missing_user_row_is_not_entitled():
    _, desired = asyncio.run(desired_member_access(FakeDb(), "ghost"))
    assert desired is False


@pytest.mark.parametrize("plan,expected", [("pro", True), ("premium", True), ("basic", False), (None, False)])
def test_plan_column_agrees_across_both_paths(plan, expected):
    db = FakeDb(user=_user(plan=plan))
    _, desired = asyncio.run(desired_member_access(db, "user-1"))
    assert desired is expected
