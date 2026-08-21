import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
    build_proof_embed,
    claim_stripe_event,
    enqueue_role_sync,
    entitlement_is_active,
    proof_row_is_defensible,
    process_role_sync_queue,
    publish_proof_digest,
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


def _proof_row(status="target_hit", *, snapshot_id=1, evaluated_at=None):
    enabled_at = NOW
    detected = evaluated_at or enabled_at + timedelta(minutes=1)
    entry_at = detected + timedelta(minutes=5)
    outcome_at = entry_at + timedelta(minutes=20)
    entry = 1_000
    outcome_price = 1_500 if status == "target_hit" else 900
    if status == "profitable_unrealised":
        outcome_price = 1_200
    if status == "flat":
        outcome_price = 1_053
    return {
        "snapshot_id": snapshot_id,
        "outcome_source_card_id": 12345,
        "snapshot_source_card_id": 12345,
        "player_source_card_id": 12345,
        "name": "Example Card",
        "rating": 90,
        "rarity": "Special",
        "proof_enabled_at": enabled_at,
        "evaluated_at": detected,
        "graded_at": outcome_at + timedelta(hours=24),
        "window_end": detected + timedelta(hours=24),
        "entry_achieved": True,
        "entry_at": entry_at,
        "entry_price": entry,
        "entry_observation_verified": True,
        "exit_achieved": status == "target_hit",
        "exit_at": outcome_at if status == "target_hit" else None,
        "realised_sell_price": outcome_price if status == "target_hit" else None,
        "outcome_price": outcome_price,
        "outcome_observed_at": outcome_at,
        "outcome_observation_verified": True,
        "outcome_price_type": "target_exit" if status == "target_hit" else "window_mark",
        "net_profit_after_tax": Decimal(outcome_price) * Decimal("0.95") - Decimal(entry),
        "target_hit": status == "target_hit",
        "downside_hit": status == "downside_hit",
        "max_adverse_excursion": Decimal("-0.15") if status == "downside_hit" else Decimal("-0.02"),
        "minutes_to_target": 20 if status == "target_hit" else None,
        "recommended_sell_target": 1_400,
        "observation_count": 3,
        "outcome_status": status,
    }


def test_proof_embed_uses_structured_honest_result():
    embed = build_proof_embed(_proof_row("loss_unrealised"), "https://app.eafc.live")
    assert embed["title"] == "Example Card"
    assert any(field["value"] == "Loss Unrealised" for field in embed["fields"])
    assert "not a live recommendation" in embed["description"]
    assert embed["url"].endswith("/#/v2/opportunities")


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


class ProofState:
    def __init__(self, rows):
        self.rows = rows
        self.publications = {}
        self.next_id = 1
        self.claim_locked = False


class ProofConn:
    def __init__(self, state):
        self.state = state

    def transaction(self):
        state = self.state

        class Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                state.claim_locked = False
                return False

        return Transaction()

    async def fetchval(self, query, *args):
        if "pg_try_advisory_xact_lock" in query:
            if self.state.claim_locked:
                return False
            self.state.claim_locked = True
            return True
        if "SELECT EXISTS" in query:
            return any(
                item["status"] in {"sending", "sent"}
                for item in self.state.publications.values()
            )
        if "INSERT INTO discord_publications" in query:
            source_key, payload = args
            existing = self.state.publications.get(source_key)
            if existing and not (
                existing["status"] == "failed"
                and existing["last_error"] == "discord_http_429"
            ):
                return None
            publication_id = existing["id"] if existing else self.state.next_id
            if not existing:
                self.state.next_id += 1
            self.state.publications[source_key] = {
                "id": publication_id,
                "status": "sending",
                "last_error": None,
                "payload": payload,
            }
            return publication_id
        raise AssertionError(f"Unexpected fetchval query: {query}")

    async def fetch(self, query, *args):
        assert "s.evaluated_at >= st.proof_enabled_at" in query
        assert "o.graded_at >= st.proof_enabled_at" in query
        assert "s.invalidated_at IS NOT NULL OR s.expires_at <= NOW()" in query
        return self.state.rows

    async def execute(self, query, *args):
        publication_ids, status, error = args
        for publication in self.state.publications.values():
            if publication["id"] in publication_ids:
                publication["status"] = status
                publication["last_error"] = error
        return f"UPDATE {len(publication_ids)}"


class ProofPool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        conn = ProofConn(self.state)

        class Lease:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Lease()


class ProofClient:
    class Config:
        proof_channel_id = "proof-channel"

    config = Config()

    def __init__(self, statuses=(200,)):
        self.statuses = iter(statuses)
        self.posts = []

    async def post_embeds(self, channel_id, embeds):
        self.posts.append((channel_id, embeds))
        return next(self.statuses), {}


def test_pre_enable_opportunity_never_posts():
    row = _proof_row(evaluated_at=NOW - timedelta(seconds=1))
    assert proof_row_is_defensible(row) is False


def test_backlog_of_100_old_rows_produces_zero_posts():
    old_rows = [
        _proof_row(snapshot_id=index, evaluated_at=NOW - timedelta(days=1))
        for index in range(100)
    ]
    state = ProofState(old_rows)
    client = ProofClient()
    assert asyncio.run(publish_proof_digest(ProofPool(state), client)) is False
    assert client.posts == []
    assert state.publications == {}


def test_post_enable_closed_outcome_posts_once_and_restart_does_not_replay():
    state = ProofState([_proof_row()])
    first_client = ProofClient()
    assert asyncio.run(publish_proof_digest(ProofPool(state), first_client)) is True
    assert len(first_client.posts) == 1
    assert len(first_client.posts[0][1]) == 1

    restarted_client = ProofClient()
    assert asyncio.run(publish_proof_digest(ProofPool(state), restarted_client)) is False
    assert restarted_client.posts == []
    assert state.publications["1"]["status"] == "sent"


def test_restart_does_not_replay_a_claim_left_sending():
    state = ProofState([_proof_row()])
    state.publications["1"] = {
        "id": 1,
        "status": "sending",
        "last_error": None,
        "payload": "{}",
    }
    client = ProofClient()

    assert asyncio.run(publish_proof_digest(ProofPool(state), client)) is False
    assert client.posts == []
    assert state.publications["1"]["status"] == "sending"


def test_two_workers_cannot_double_post():
    async def run_workers():
        state = ProofState([_proof_row()])
        client = ProofClient(statuses=(200,))
        results = await asyncio.gather(
            publish_proof_digest(ProofPool(state), client),
            publish_proof_digest(ProofPool(state), client),
        )
        return state, client, results

    state, client, results = asyncio.run(run_workers())
    assert sorted(results) == [False, True]
    assert len(client.posts) == 1
    assert len(state.publications) == 1


def test_discord_429_is_the_only_safe_retry():
    state = ProofState([_proof_row()])
    client = ProofClient(statuses=(429, 200))
    assert asyncio.run(publish_proof_digest(ProofPool(state), client)) is False
    assert state.publications["1"]["status"] == "failed"
    assert state.publications["1"]["last_error"] == "discord_http_429"
    assert asyncio.run(publish_proof_digest(ProofPool(state), client)) is True
    assert len(client.posts) == 2
    assert state.publications["1"]["status"] == "sent"


@pytest.mark.parametrize(
    "status", ["target_hit", "profitable_unrealised", "loss_unrealised", "downside_hit", "flat"]
)
def test_win_loss_and_flat_outcomes_are_supported(status):
    row = _proof_row(status)
    assert proof_row_is_defensible(row) is True
    assert build_proof_embed(row, "https://app.eafc.live")["fields"][0]["value"]


def test_insufficient_data_is_excluded():
    row = _proof_row()
    row["outcome_status"] = "insufficient_observations"
    assert proof_row_is_defensible(row) is False
