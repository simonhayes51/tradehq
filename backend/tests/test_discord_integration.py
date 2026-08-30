import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.routers.discord import router as discord_router
import app.services.discord_integration as discord_integration
from app.services.discord_integration import (
    DiscordMemberState,
    _finish_role_sync,
    assert_public_opportunity_payload,
    build_opportunity_digest,
    claim_stripe_event,
    enqueue_role_sync,
    entitlement_is_active,
    process_role_sync_queue,
    publish_opportunity_digest,
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
        role_sync_ready = True

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
    rows = [
        {
            "signal": "strong_buy",
            "confidence_score": 0.84,
            "liquidity_score": 0.78,
            "risk_level": "low",
            "current_executable_buy": 22_000,
        },
        {
            "signal": "buy",
            "confidence_score": 0.72,
            "liquidity_score": 0.55,
            "risk_level": "medium",
            "current_executable_buy": 140_000,
        },
    ]
    embed = build_opportunity_digest(rows, "https://app.eafc.live")
    rendered = json.dumps(embed)
    assert "genuinely new" in embed["description"]
    assert "Strong buy: 1 | Buy: 1" in rendered
    assert "Average confidence: 78%" in rendered
    assert "High: 1 | Moderate: 1 | Limited: 0" in rendered
    assert "Player" not in rendered
    assert "Buy target" not in rendered
    assert "Sell target" not in rendered
    assert "profit" not in rendered.lower()
    assert "#/opportunities" in rendered


def test_actionable_public_payload_is_rejected():
    with pytest.raises(ValueError):
        assert_public_opportunity_payload({"count": 3, "buy_target": 42_000})


class OpportunityDigestConn:
    def __init__(self, rows, *, cooldown=False):
        self.rows = rows
        self.cooldown = cooldown
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        if "SELECT EXISTS" in query:
            return self.cooldown
        if "INSERT INTO discord_publications" in query:
            return 91
        raise AssertionError(f"Unexpected fetchval query: {query}")

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        assert "last_digest" in query
        assert "INTERVAL '12 hours'" in query
        return self.rows

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "UPDATE 1"


class OpportunityDigestPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Lease:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Lease()


class OpportunityDigestClient:
    class Config:
        opportunity_channel_id = "opportunity-channel"

    config = Config()

    def __init__(self):
        self.posts = []

    async def post_embed(self, channel_id, embed, *, nonce, enforce_nonce):
        self.posts.append((channel_id, embed, nonce, enforce_nonce))
        return 200, {"id": "message-id"}


def test_opportunity_publisher_uses_quiet_defaults_and_rich_deduplicated_digest(monkeypatch):
    for name in (
        "DISCORD_OPPORTUNITY_BATCH_MIN",
        "DISCORD_OPPORTUNITY_COOLDOWN_MINUTES",
        "DISCORD_OPPORTUNITY_DEDUP_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)
    rows = [
        {
            "id": index,
            "source_card_id": 10_000 + index,
            "signal": "strong_buy" if index < 3 else "buy",
            "confidence_score": 0.8,
            "liquidity_score": 0.75,
            "risk_level": "low",
            "current_executable_buy": 20_000 * index,
        }
        for index in range(1, 6)
    ]
    conn = OpportunityDigestConn(rows)
    client = OpportunityDigestClient()

    assert asyncio.run(
        publish_opportunity_digest(OpportunityDigestPool(conn), client)
    ) is True
    assert conn.calls[0][1] == (180,)
    assert conn.calls[1][1] == (180, 24)
    assert len(client.posts) == 1
    assert client.posts[0][0] == "opportunity-channel"
    assert client.posts[0][1]["title"] == "EAFC LIVE - OPPORTUNITY UPDATE"
    assert client.posts[0][3] is True
    assert conn.calls[-1][1] == (91, "sent", None)


def test_opportunity_publisher_does_nothing_during_three_hour_cooldown(monkeypatch):
    monkeypatch.delenv("DISCORD_OPPORTUNITY_COOLDOWN_MINUTES", raising=False)
    conn = OpportunityDigestConn([], cooldown=True)
    client = OpportunityDigestClient()

    assert asyncio.run(
        publish_opportunity_digest(OpportunityDigestPool(conn), client)
    ) is False
    assert len(conn.calls) == 1
    assert conn.calls[0][1] == (180,)
    assert client.posts == []


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


class BindingAwareRoleSyncConn:
    """Implements asyncpg's relevant parameter contract for this query."""

    def __init__(self, *, queued=True):
        self.queue = (
            {
                "discord_user_id": "123456789",
                "desired_member_role": False,
                "status": "pending",
            }
            if queued
            else None
        )
        self.finish_calls = []

    async def fetchrow(self, query, *args):
        if "FROM users WHERE" in query:
            return {
                "discord_id": "123456789",
                "plan": "free",
                "tier": "free",
                "account_type": "user",
                "premium_until": None,
            }
        if "FROM subscriptions" in query:
            return None
        if "FROM discord_role_sync_queue" in query:
            return self.queue
        return None

    async def fetch(self, query, *args):
        if self.queue and self.queue["status"] != "synced":
            return [{"user_id": "user-free"}]
        return []

    async def execute(self, query, *args):
        if "INSERT INTO discord_role_sync_queue" in query:
            self.queue = {
                "discord_user_id": args[1],
                "desired_member_role": args[2],
                "status": "pending",
            }
            return "INSERT 0 1"
        if "next_attempt_at=NOW()" in query and len(args) == 4:
            assert "$3::integer" in query
            assert isinstance(args[2], int)
            self.finish_calls.append(args)
            self.queue["status"] = args[1]
            return "UPDATE 1"
        if "status='synced'" in query:
            self.finish_calls.append(args)
            self.queue["status"] = "synced"
            return "UPDATE 1"
        return "UPDATE 0"


@pytest.mark.parametrize(
    "member,expected_status,expected_delay,expected_error",
    [
        (DiscordMemberState("not_joined"), "not_joined", 60, None),
        (
            DiscordMemberState("unavailable", error="discord_http_503"),
            "unavailable",
            5,
            "discord_http_503",
        ),
    ],
)
def test_finish_role_sync_binds_integer_retry_minutes(
    member, expected_status, expected_delay, expected_error
):
    conn = BindingAwareRoleSyncConn()
    asyncio.run(_finish_role_sync(conn, "user-free", member))
    assert conn.finish_calls == [
        ("user-free", expected_status, expected_delay, expected_error)
    ]


def test_finish_role_sync_real_asyncpg_binding():
    async def exercise():
        dsn = os.getenv(
            "TEST_ADMIN_DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/postgres",
        )
        try:
            conn = await asyncpg.connect(dsn, timeout=2)
        except Exception:
            pytest.skip(f"no PostgreSQL reachable at {dsn}")
        try:
            await conn.execute(
                """
                CREATE TEMP TABLE discord_role_sync_queue (
                    user_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TIMESTAMPTZ,
                    last_attempt_at TIMESTAMPTZ,
                    synced_at TIMESTAMPTZ,
                    last_error TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                "INSERT INTO discord_role_sync_queue (user_id, status) VALUES ($1, 'pending')",
                "user-free",
            )
            await _finish_role_sync(conn, "user-free", DiscordMemberState("not_joined"))
            row = await conn.fetchrow(
                "SELECT status, attempts, next_attempt_at - last_attempt_at AS retry_delay FROM discord_role_sync_queue WHERE user_id=$1",
                "user-free",
            )
            assert row["status"] == "not_joined"
            assert row["attempts"] == 1
            assert row["retry_delay"] == timedelta(minutes=60)
        finally:
            await conn.close()

    asyncio.run(exercise())


def test_manual_reconcile_endpoint_returns_joined_free_and_is_idempotent(monkeypatch):
    conn = BindingAwareRoleSyncConn(queued=False)
    client = FakeClient(DiscordMemberState("joined", frozenset()))
    monkeypatch.setattr(discord_integration, "DiscordClient", lambda: client)

    app = FastAPI()

    @app.middleware("http")
    async def add_test_session(request, call_next):
        request.scope["session"] = {"user_id": "user-free"}
        return await call_next(request)

    async def override_db():
        yield conn

    app.dependency_overrides[get_db] = override_db
    app.include_router(discord_router)

    with TestClient(app) as http:
        first = http.post("/api/discord/reconcile")
        second = http.post("/api/discord/reconcile")

    assert first.status_code == second.status_code == 200
    assert first.json()["guild_membership"] == "joined"
    assert first.json()["desired_member_role"] is False
    assert first.json()["has_member_role"] is False
    assert first.json()["role_sync"] == "synced"
    assert second.json() == first.json()
    assert client.calls == [("123456789", False), ("123456789", False)]
    assert conn.queue["status"] == "synced"


class SingleConnectionPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Lease:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Lease()


def test_background_reconcile_returns_joined_free_and_is_idempotent():
    conn = BindingAwareRoleSyncConn()
    pool = SingleConnectionPool(conn)
    client = FakeClient(DiscordMemberState("joined", frozenset()))

    first = asyncio.run(process_role_sync_queue(pool, client))
    second = asyncio.run(process_role_sync_queue(pool, client))

    assert first == 1
    assert second == 0
    assert client.calls == [("123456789", False)]
    assert conn.queue["status"] == "synced"
