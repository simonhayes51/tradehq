# tests/test_discord_reconcile.py
#
# The periodic Discord role sweep.
#
# The queue (migration 056) handles every ordinary billing change. This
# exists for the jobs it LOSES: a Discord outage long enough to exhaust
# the retries leaves an account permanently wrong, because nothing will
# ever look at it again. A queue cannot notice it is out of step.
#
# The most important property here is not "it grants roles" but "it does
# not invent a second answer to who has paid".
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, List, Optional

import pytest

from app.services import discord_reconcile
from app.services.discord_integration import DiscordMemberState


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, accounts):
        self.accounts = accounts
        self.locked = True

    async def fetch(self, sql, *args):
        if "FROM users" in sql:
            limit, offset = args
            return self.accounts[offset:offset + limit]
        return []

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_lock" in sql:
            return self.locked
        return None

    async def execute(self, sql, *args):
        return "OK"


class FakePool:
    def __init__(self, accounts=(), locked=True):
        self.conn = FakeConn(list(accounts))
        self.conn.locked = locked

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self): return conn
            async def __aexit__(self, *a): return False

        return Ctx()


class FakeConfig:
    member_role_id = "ROLE"
    role_sync_ready = True


class FakeClient:
    """A Discord that behaves however a test needs it to."""

    def __init__(self, members: Dict[str, Any], ready=True):
        self.config = FakeConfig()
        self.config.role_sync_ready = ready
        self.members = members
        self.granted: List[str] = []
        self.revoked: List[str] = []

    async def get_member(self, discord_user_id):
        return self.members.get(discord_user_id, DiscordMemberState("not_joined"))

    async def reconcile_member_role(self, discord_user_id, desired):
        (self.granted if desired else self.revoked).append(discord_user_id)
        roles = set(self.members[discord_user_id].roles)
        roles.add("ROLE") if desired else roles.discard("ROLE")
        # Persisted, because a real Discord remembers. Without this the
        # idempotence test would pass against a double that forgets, which
        # is the one thing that test exists to rule out.
        state = DiscordMemberState("joined", frozenset(roles))
        self.members[discord_user_id] = state
        return state


def accounts(*pairs):
    return [{"user_id": u, "discord_user_id": d} for u, d in pairs]


def joined(*roles):
    return DiscordMemberState("joined", frozenset(roles))


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    # The politeness pause is real behaviour, not something to sit through.
    monkeypatch.setattr(discord_reconcile, "PER_ACCOUNT_DELAY_SECONDS", 0.0)


def sweep(pool, client, tiers, **kwargs):
    async def fake_tier(_pool, user_id, discord_id=None):
        return tiers.get(user_id, "free")

    import app.auth.entitlements as ent

    original = ent.resolve_user_tier
    ent.resolve_user_tier = fake_tier
    try:
        return run(discord_reconcile.reconcile_once(pool, client=client, **kwargs))
    finally:
        ent.resolve_user_tier = original


class TestItUsesTheOneEntitlementResolver:
    def test_the_sweep_calls_resolve_user_tier(self):
        # Not a second paid/free calculation. That disagreement is the bug
        # Phase 2 removed - the account page showing "Current plan: ELITE"
        # above "Membership access: Free" - and reintroducing it in a
        # background job would be worse, because nobody would be looking.
        source = inspect.getsource(discord_reconcile.reconcile_once)
        assert "resolve_user_tier" in source
        assert "entitlement_is_active" not in source

    def test_it_does_not_read_billing_columns_itself(self):
        source = inspect.getsource(discord_reconcile)
        for column in ("premium_until", "subscriptions", "is_premium", "stripe"):
            assert column not in source.lower(), column


class TestGrantRevokeSkip:
    def test_an_elite_member_without_the_role_gets_it(self):
        client = FakeClient({"d1": joined()})
        summary = sweep(FakePool(accounts(("u1", "d1"))), client, {"u1": "elite"})
        assert summary.roles_granted == 1
        assert client.granted == ["d1"]

    def test_a_free_member_holding_the_role_loses_it(self):
        client = FakeClient({"d1": joined("ROLE")})
        summary = sweep(FakePool(accounts(("u1", "d1"))), client, {"u1": "free"})
        assert summary.roles_revoked == 1
        assert client.revoked == ["d1"]

    def test_an_account_already_correct_is_left_alone(self):
        client = FakeClient({"d1": joined("ROLE"), "d2": joined()})
        summary = sweep(
            FakePool(accounts(("u1", "d1"), ("u2", "d2"))), client,
            {"u1": "elite", "u2": "free"},
        )
        assert summary.already_correct == 2
        assert client.granted == [] and client.revoked == []

    def test_someone_not_in_the_server_is_skipped_not_failed(self):
        # The role only exists inside the guild. Being in the product and
        # not in the Discord is normal.
        client = FakeClient({})
        summary = sweep(FakePool(accounts(("u1", "d1"))), client, {"u1": "elite"})
        assert summary.skipped == 1
        assert summary.failed == 0

    def test_an_account_with_no_linked_discord_is_skipped(self):
        # And is never a reason to ask anyone to reconnect anything.
        client = FakeClient({})
        summary = sweep(FakePool(accounts(("u1", None))), client, {"u1": "elite"})
        assert summary.skipped == 1
        assert summary.accounts_checked == 0


class TestIdempotence:
    def test_running_it_twice_changes_nothing_the_second_time(self):
        client = FakeClient({"d1": joined()})
        pool = FakePool(accounts(("u1", "d1")))
        first = sweep(pool, client, {"u1": "elite"})
        second = sweep(pool, client, {"u1": "elite"})
        assert first.roles_granted == 1
        assert second.roles_granted == 0
        assert second.already_correct == 1


class TestDryRun:
    def test_it_reports_what_would_change_and_touches_nothing(self):
        client = FakeClient({"d1": joined(), "d2": joined("ROLE")})
        summary = sweep(
            FakePool(accounts(("u1", "d1"), ("u2", "d2"))), client,
            {"u1": "elite", "u2": "free"}, dry_run=True,
        )
        assert summary.roles_granted == 1
        assert summary.roles_revoked == 1
        assert summary.dry_run is True
        # Nothing was actually called.
        assert client.granted == [] and client.revoked == []


class TestFailureHandling:
    def test_one_bad_account_does_not_end_the_sweep(self):
        client = FakeClient({"d1": DiscordMemberState("unavailable"), "d2": joined()})
        summary = sweep(
            FakePool(accounts(("u1", "d1"), ("u2", "d2"))), client,
            {"u1": "elite", "u2": "elite"},
        )
        assert summary.failed == 1
        assert summary.roles_granted == 1

    def test_a_sustained_outage_aborts_rather_than_hammering_discord(self, monkeypatch):
        monkeypatch.setattr(discord_reconcile, "MAX_CONSECUTIVE_FAILURES", 3)
        client = FakeClient({f"d{i}": DiscordMemberState("unavailable") for i in range(10)})
        summary = sweep(
            FakePool(accounts(*[(f"u{i}", f"d{i}") for i in range(10)])), client,
            {f"u{i}": "elite" for i in range(10)},
        )
        assert summary.status == "failed"
        assert summary.failure_reason == "discord_unavailable"
        assert summary.failed == 3

    def test_a_recovery_resets_the_failure_streak(self, monkeypatch):
        monkeypatch.setattr(discord_reconcile, "MAX_CONSECUTIVE_FAILURES", 3)
        client = FakeClient({
            "d1": DiscordMemberState("unavailable"),
            "d2": joined("ROLE"),
            "d3": DiscordMemberState("unavailable"),
            "d4": DiscordMemberState("unavailable"),
        })
        summary = sweep(
            FakePool(accounts(("u1", "d1"), ("u2", "d2"), ("u3", "d3"), ("u4", "d4"))),
            client, {f"u{i}": "elite" for i in range(1, 5)},
        )
        # Three failures total but never three IN A ROW.
        assert summary.status == "completed"
        assert summary.failed == 3

    def test_an_unconfigured_deployment_says_so_rather_than_reporting_success(self):
        client = FakeClient({}, ready=False)
        summary = sweep(FakePool(accounts(("u1", "d1"))), client, {"u1": "elite"})
        assert summary.status == "failed"
        assert summary.failure_reason == "not_configured"
        assert summary.accounts_checked == 0

    def test_the_failure_reason_is_always_a_known_code(self):
        # Never an exception string: an HTTP client's error text can carry
        # a bot token, and this value is stored and shown to an admin.
        source = inspect.getsource(discord_reconcile)
        assert "_FAILURE_REASONS" in source
        for reason in ("not_configured", "discord_unavailable", "database_error"):
            assert reason in discord_reconcile._FAILURE_REASONS

    def test_no_credential_can_reach_the_summary(self):
        summary = discord_reconcile.ReconcileSummary(
            failure_reason="discord_unavailable", failed=2,
        )
        blob = str(summary.as_dict())
        for secret in ("token", "Bot ", "Authorization"):
            assert secret not in blob


class TestBatching:
    def test_it_pages_through_a_large_member_list(self):
        client = FakeClient({f"d{i}": joined("ROLE") for i in range(25)})
        summary = sweep(
            FakePool(accounts(*[(f"u{i}", f"d{i}") for i in range(25)])),
            client, {f"u{i}": "elite" for i in range(25)}, batch_size=10,
        )
        assert summary.accounts_checked == 25
        assert summary.already_correct == 25

    def test_a_limit_bounds_one_pass(self):
        client = FakeClient({f"d{i}": joined("ROLE") for i in range(25)})
        summary = sweep(
            FakePool(accounts(*[(f"u{i}", f"d{i}") for i in range(25)])),
            client, {f"u{i}": "elite" for i in range(25)},
            batch_size=10, limit_accounts=7,
        )
        assert summary.accounts_checked == 7


class TestConcurrencySafety:
    def test_a_second_worker_does_nothing_rather_than_doubling_the_work(self):
        # Every worker runs the same lifespan, so every worker will try.
        pool = FakePool(accounts(("u1", "d1")), locked=False)
        assert run(discord_reconcile.reconcile_with_lock(pool)) is None

    def test_the_lock_is_held_for_the_whole_pass(self):
        source = inspect.getsource(discord_reconcile.reconcile_with_lock)
        # Released in a finally AFTER the sweep, not right after the
        # SELECT - the same discipline the alerts poller uses.
        assert source.index("reconcile_once") < source.index("pg_advisory_unlock")

    def test_the_scheduled_loop_delays_its_first_pass(self):
        # A worker that starts a Discord sweep the instant it boots makes
        # every deploy a thundering herd against the Discord API.
        source = inspect.getsource(discord_reconcile.reconcile_loop)
        assert "await asyncio.sleep(min(interval_seconds" in source

    def test_it_uses_the_projects_existing_worker_pattern(self):
        # An asyncio task in the app lifespan with an advisory lock - not
        # a new scheduler.
        import pathlib

        main_py = pathlib.Path(__file__).resolve().parents[1] / "main.py"
        source = main_py.read_text()
        assert "reconcile_loop" in source
        assert "asyncio.create_task" in source


class TestTheManualCommand:
    def test_it_exists_and_offers_a_dry_run(self):
        import pathlib

        script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "reconcile_discord_roles.py"
        assert script.exists()
        source = script.read_text()
        assert "--dry-run" in source
        assert "--status" in source
        assert "reconcile_with_lock" in source
