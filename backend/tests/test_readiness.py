# tests/test_readiness.py
#
# "Healthy / unhealthy" cannot express what an operator needs here,
# because most of what this product depends on is optional. A staging
# environment with no Stripe key is correctly configured for what it is,
# not broken - and reporting it as unhealthy trains people to ignore the
# check, which is worse than having no check.
#
# The other thing this file guards: /health used to return `str(e)` on a
# database failure, and an asyncpg connection error message contains the
# DSN it failed to reach. The one moment the database was down was the one
# moment the endpoint published the connection string.
from __future__ import annotations

import asyncio
import inspect
import json
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
os.environ.setdefault("ENV", "test")

from app.services import readiness as rd  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, tables=(), fail=False):
        self.tables = list(tables)
        self.fail = fail

    async def fetchval(self, sql, *args):
        if self.fail:
            raise RuntimeError(
                "connection failed: postgresql://user:hunter2@db.internal:5432/core"
            )
        return 1

    async def fetch(self, sql, *args):
        if self.fail:
            raise RuntimeError("connection failed: postgresql://user:hunter2@db.internal")
        return [{"table_name": t} for t in self.tables]


class FakePool:
    def __init__(self, tables=(), fail=False):
        self.conn = FakeConn(tables, fail)

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self): return conn
            async def __aexit__(self, *a): return False

        return Ctx()


ALL_TABLES = tuple(rd.REQUIRED_TABLES)


class TestTheFourStates:
    def test_a_working_required_dependency_is_ok(self):
        assert run(rd._check_database(FakePool(), "core_database", required=True))["status"] == rd.OK

    def test_a_dead_required_dependency_is_unavailable(self):
        result = run(rd._check_database(FakePool(fail=True), "core_database", required=True))
        assert result["status"] == rd.UNAVAILABLE

    def test_a_dead_optional_dependency_is_only_degraded(self):
        result = run(rd._check_database(FakePool(fail=True), "watchlist_database", required=False))
        assert result["status"] == rd.DEGRADED

    def test_an_integration_that_was_never_set_up_is_unconfigured(self, monkeypatch):
        for key in ("STRIPE_SECRET_KEY", "STRIPE_MONTHLY_PRICE_ID", "STRIPE_SEASON_PRICE_ID"):
            monkeypatch.delenv(key, raising=False)
        assert rd._check_stripe()["status"] == rd.UNCONFIGURED

    def test_unconfigured_does_not_drag_the_overall_status_down(self):
        # A staging environment with no Stripe key is not unhealthy.
        checks = [
            {"name": "a", "status": rd.OK},
            {"name": "b", "status": rd.UNCONFIGURED},
        ]
        assert rd.overall(checks) == rd.OK

    def test_the_overall_status_is_the_worst_thing_that_is_wrong(self):
        assert rd.overall([{"name": "a", "status": rd.DEGRADED},
                           {"name": "b", "status": rd.OK}]) == rd.DEGRADED
        assert rd.overall([{"name": "a", "status": rd.DEGRADED},
                           {"name": "b", "status": rd.UNAVAILABLE}]) == rd.UNAVAILABLE


class TestMigrations:
    def test_all_tables_present_is_ok(self):
        assert run(rd._check_migrations(FakePool(ALL_TABLES)))["status"] == rd.OK

    def test_a_pending_migration_is_degraded_not_unavailable(self):
        # Every feature behind these tables degrades honestly on its own:
        # free picks fall back to an in-memory selection, onboarding
        # reports itself degraded, funnel events return recorded:false.
        # The product serves; the features are incomplete.
        result = run(rd._check_migrations(FakePool(ALL_TABLES[:2])))
        assert result["status"] == rd.DEGRADED
        assert result["reason"] == "pending"

    def test_it_names_which_migrations_are_missing(self):
        result = run(rd._check_migrations(FakePool(())))
        missing = " ".join(result["detail"]["missing"])
        for number in ("072", "073", "074", "075", "076", "078"):
            assert number in missing

    def test_it_checks_the_schema_not_a_ledger(self):
        # A migrations ledger can itself be out of step with the schema.
        source = inspect.getsource(rd._check_migrations)
        assert "information_schema.tables" in source

    def test_every_phase_three_migration_is_covered(self):
        for table in ("funnel_events", "ai_usage_events", "discord_reconciliation_runs"):
            assert table in rd.REQUIRED_TABLES


class TestOptionalIntegrations:
    def test_no_redis_is_unconfigured_not_degraded(self, monkeypatch):
        # Nothing stops working without Redis; the AI rate limits just
        # stop being fleet-wide.
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert run(rd._check_redis())["status"] == rd.UNCONFIGURED

    def test_an_unreachable_redis_is_degraded_not_unavailable(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://nonexistent.invalid:6379")
        assert run(rd._check_redis())["status"] == rd.DEGRADED

    def test_no_ai_key_is_unconfigured(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert rd._check_ai()["status"] == rd.UNCONFIGURED

    def test_the_ai_kill_switch_reads_as_deliberate_not_broken(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("AI_CHAT_ENABLED", "0")
        result = rd._check_ai()
        assert result["status"] == rd.UNCONFIGURED
        assert result["reason"] == "disabled_by_operator"

    def test_stripe_without_a_webhook_secret_is_degraded(self, monkeypatch):
        # This matters more than it looks: without it nothing confirms a
        # payment server-side, so subscriptions never activate.
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
        monkeypatch.setenv("STRIPE_MONTHLY_PRICE_ID", "price_x")
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        result = rd._check_stripe()
        assert result["status"] == rd.DEGRADED
        assert result["reason"] == "no_webhook_secret"

    def test_discord_not_configured_is_unconfigured(self, monkeypatch):
        for key in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "DISCORD_MEMBER_ROLE_ID",
                    "DISCORD_PREMIUM_ROLE_ID"):
            monkeypatch.delenv(key, raising=False)
        assert rd._check_discord()["status"] == rd.UNCONFIGURED


class TestNothingSensitiveIsReturned:
    def test_a_database_failure_never_returns_the_connection_string(self):
        result = run(rd._check_database(FakePool(fail=True), "core_database", required=True))
        blob = json.dumps(result)
        assert "hunter2" not in blob
        assert "db.internal" not in blob
        assert "postgresql://" not in blob
        # A code, not a message.
        assert result["reason"] == "unreachable"

    def test_a_schema_failure_says_nothing_either(self):
        result = run(rd._check_migrations(FakePool(fail=True)))
        assert "hunter2" not in json.dumps(result)

    def test_the_public_summary_drops_reasons_and_detail(self):
        report = {
            "status": rd.DEGRADED,
            "checks": [
                {"name": "core_database", "status": rd.OK},
                {"name": "stripe", "status": rd.DEGRADED, "reason": "no_webhook_secret",
                 "detail": {"plans_configured": 2}},
            ],
        }
        summary = rd.public_summary(report)
        assert summary == {
            "status": rd.DEGRADED,
            "checks": {"core_database": rd.OK, "stripe": rd.DEGRADED},
        }
        blob = json.dumps(summary)
        assert "webhook" not in blob
        assert "plans_configured" not in blob

    def test_no_env_var_value_appears_in_any_check(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_secret")
        monkeypatch.setenv("STRIPE_MONTHLY_PRICE_ID", "price_x")
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_secret")
        blob = json.dumps([rd._check_ai(), rd._check_stripe()])
        for secret in ("sk-super-secret-value", "sk_live_secret", "whsec_secret"):
            assert secret not in blob

    def test_the_liveness_endpoint_no_longer_returns_exception_text(self):
        import ast
        import textwrap

        import main

        # Parsed, not grepped: this function's own docstring explains the
        # bug by quoting `str(e)`, so a substring search over the raw
        # source matches the explanation rather than a violation of it.
        tree = ast.parse(textwrap.dedent(inspect.getsource(main.health_check)))
        function = tree.body[0]
        if (function.body and isinstance(function.body[0], ast.Expr)
                and isinstance(function.body[0].value, ast.Constant)):
            function.body = function.body[1:]        # drop the docstring
        body = ast.unparse(function)
        assert "str(e)" not in body
        assert "exc_info=True" in body


class TestTheEndpoints:
    def test_readiness_covers_everything_the_objective_named(self):
        source = inspect.getsource(rd.readiness)
        for check in ("core_database", "watchlist_database", "_check_migrations",
                      "_check_redis", "_check_ai", "_check_stripe", "_check_discord",
                      "_check_free_picks", "_check_funnel_events"):
            assert check in source

    def test_the_public_endpoint_only_503s_when_the_product_cannot_serve(self):
        import main

        source = inspect.getsource(main.readiness_check)
        # A degraded optional feature is not a reason to pull an instance
        # out of the load balancer.
        assert 'report["status"] == "unavailable"' in source

    def test_the_detailed_report_is_admin_only(self):
        from app.routers.admin import require_admin
        from app.routers.admin_reporting import router

        for route in router.routes:
            if route.path.endswith("/readiness"):
                deps = [d.call for d in route.dependant.dependencies]
                assert require_admin in deps
                break
        else:
            pytest.fail("no /readiness route")
