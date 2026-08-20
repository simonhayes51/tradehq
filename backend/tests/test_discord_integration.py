import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.discord_integration import (
    DiscordMemberState,
    assert_public_opportunity_payload,
    build_opportunity_digest,
    build_proof_message,
    claim_stripe_event,
    enqueue_role_sync,
    entitlement_is_active,
    reconcile_user,
)


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "status,end,expected",
    [
        ("active", NOW + timedelta(days=10), True),
        ("trialing", NOW + timedelta(days=3), True),
        ("past_due", NOW + timedelta(days=2), True),
        ("active", NOW - timedelta(seconds=1), False),
        ("canceled", NOW + timedelta(days=10), False),
        (None, None, False),
    ],
)
def test_subscription_entitlement_drives_discord_role(status, end, expected):
    assert entitlement_is_active(status=status, current_period_end=end, now=NOW) is expected


def test_cancel_at_period_end_does_not_change_current_entitlement():
    assert entitlement_is_active(
        status="active", current_period_end=NOW + timedelta(days=7), now=NOW
    )


def test_manual_free_and_resubscription_states():
    assert not entitlement_is_active(status="canceled", current_period_end=NOW, now=NOW)
    assert entitlement_is_active(
        status="active", current_period_end=NOW + timedelta(days=30), now=NOW
    )


class FakeConn:
    def __init__(self, desired=True):
        self.desired = desired
        self.executed = []

    async def fetchrow(self, query, *args):
        if "discord_role_sync_queue" in query:
            return {"discord_user_id": "123456789", "desired_member_role": self.desired}
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))


class FakeClient:
    class Config:
        member_role_id = "member-role"

    config = Config()

    def __init__(self, state):
        self.state = state
        self.calls = []

    async def reconcile_member_role(self, discord_user_id, desired):
        self.calls.append((discord_user_id, desired))
        return self.state


def test_premium_member_role_is_applied_idempotently():
    conn = FakeConn(desired=True)
    client = FakeClient(DiscordMemberState("joined", frozenset({"member-role"})))
    result = asyncio.run(reconcile_user(conn, "user-1", client))
    assert client.calls == [("123456789", True)]
    assert result["role_sync"] == "synced"
    assert result["has_member_role"] is True
    assert any("status='synced'" in query for query, _ in conn.executed)


def test_subscribed_user_not_in_guild_stays_recoverable():
    conn = FakeConn(desired=True)
    client = FakeClient(DiscordMemberState("not_joined"))
    result = asyncio.run(reconcile_user(conn, "user-1", client))
    assert result["role_sync"] == "not_joined"
    assert any("next_attempt_at" in query for query, _ in conn.executed)


def test_discord_unavailable_does_not_raise_or_change_entitlement():
    conn = FakeConn(desired=True)
    client = FakeClient(DiscordMemberState("unavailable", error="discord_http_503"))
    result = asyncio.run(reconcile_user(conn, "user-1", client))
    assert result["role_sync"] == "unavailable"
    assert result["desired_member_role"] is True


def test_public_opportunity_message_contains_no_actionable_fields():
    payload = {
        "count": 3,
        "min_coin": 32_000,
        "max_coin": 181_000,
        "risks": ["low", "medium"],
    }
    assert_public_opportunity_payload(payload)
    message = build_opportunity_digest(
        count=payload["count"],
        min_coin=payload["min_coin"],
        max_coin=payload["max_coin"],
        risks=payload["risks"],
        app_url="https://app.eafc.live",
    )
    assert "Player" not in message
    assert "Buy target" not in message
    assert "Sell target" not in message
    assert "profit" not in message.lower()
    assert "#/v2/opportunities" in message


def test_actionable_public_payload_is_rejected():
    with pytest.raises(ValueError):
        assert_public_opportunity_payload({"count": 3, "buy_target": 42_000})


def test_proof_message_uses_recorded_loss_without_hiding_it():
    message = build_proof_message(
        {
            "name": "Example Card",
            "evaluated_at": NOW,
            "entry_price": 45_000,
            "realised_sell_price": None,
            "outcome_status": "loss_unrealised",
            "net_profit_after_tax": -2_150,
            "minutes_to_target": None,
        },
        "https://app.eafc.live",
    )
    assert "Loss Unrealised" in message
    assert "-2,150" in message
    assert "not a live recommendation" in message


class QueueConn:
    def __init__(self):
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))


@pytest.mark.parametrize("desired", [True, False])
def test_role_sync_intent_is_durable_without_calling_discord(desired):
    conn = QueueConn()
    asyncio.run(
        enqueue_role_sync(
            conn,
            "user-1",
            discord_user_id="123456789",
            desired=desired,
        )
    )
    assert conn.calls[0][1] == ("user-1", "123456789", desired)


class StripeClaimConn:
    def __init__(self, fetchvals):
        self.fetchvals = iter(fetchvals)

    async def fetchval(self, query, *args):
        return next(self.fetchvals)


def test_duplicate_processed_stripe_event_is_not_claimed_twice():
    conn = StripeClaimConn([None, None, NOW])
    result = asyncio.run(claim_stripe_event(conn, "evt_duplicate", "customer.subscription.updated"))
    assert result == "processed"


def test_in_flight_stripe_event_is_not_claimed_twice():
    conn = StripeClaimConn([None, None, None])
    result = asyncio.run(claim_stripe_event(conn, "evt_in_flight", "customer.subscription.updated"))
    assert result == "processing"
