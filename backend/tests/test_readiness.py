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


def _fake_redis_module(created=None, pings=None):
    """A stand-in for `redis.asyncio`.

    The real package is not installed in this environment, and
    `import redis.asyncio as aioredis` imports the PARENT package first -
    so both `redis` and `redis.asyncio` have to be present in sys.modules
    for the patch to take.
    """
    import types

    class FakeRedis:
        def __init__(self):
            if created is not None:
                created.append(self)

        async def ping(self):
            if pings is not None:
                pings.append(1)
            return True

    asyncio_module = types.ModuleType("redis.asyncio")
    asyncio_module.from_url = lambda url, decode_responses=True: FakeRedis()
    parent = types.ModuleType("redis")
    parent.asyncio = asyncio_module
    return parent, asyncio_module


def _install_fake_redis(monkeypatch, created=None, pings=None):
    import sys

    parent, asyncio_module = _fake_redis_module(created, pings)
    monkeypatch.setitem(sys.modules, "redis", parent)
    monkeypatch.setitem(sys.modules, "redis.asyncio", asyncio_module)
    monkeypatch.setattr(rd, "_redis_client", None)
    monkeypatch.setattr(rd, "_redis_client_url", None)


class TestTheCheckDoesNotDamageWhatItChecks:
    def test_the_redis_client_is_reused_not_rebuilt_per_call(self, monkeypatch):
        # /health/ready is PUBLIC and is exactly the kind of endpoint a
        # load balancer polls every few seconds. A per-call client meant a
        # new connection pool every few seconds, none ever closed - a
        # readiness check that eventually exhausts the thing it checks.
        created = []
        monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379")
        _install_fake_redis(monkeypatch, created=created)

        for _ in range(5):
            assert run(rd._check_redis())["status"] == rd.OK
        assert len(created) == 1, "a new Redis client was built per readiness call"

    def test_a_changed_url_does_rebuild_the_client(self, monkeypatch):
        created = []
        monkeypatch.setenv("REDIS_URL", "redis://one.invalid:6379")
        _install_fake_redis(monkeypatch, created=created)

        run(rd._check_redis())
        monkeypatch.setenv("REDIS_URL", "redis://two.invalid:6379")
        run(rd._check_redis())
        assert len(created) == 2

    def test_it_is_still_a_live_check_after_caching(self, monkeypatch):
        # A cached client that is never pinged would report OK forever.
        pings = []
        monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379")
        _install_fake_redis(monkeypatch, pings=pings)

        run(rd._check_redis())
        run(rd._check_redis())
        assert len(pings) == 2


class TestCredentialMintingIsBounded:
    def test_the_companion_session_endpoint_is_rate_limited(self):
        from app.middleware.rate_limit import RATE_LIMIT_RULES

        prefixes = [rule[0] for rule in RATE_LIMIT_RULES]
        assert "/api/v2/companion/session" in prefixes, (
            "an endpoint that hands out signed tokens should not be unbounded"
        )


# ---------------------------------------------------------------------------
# The collectors
# ---------------------------------------------------------------------------
#
# This check exists because of a real outage, and the first version of it
# encoded the wrong story. The heartbeat row that showed three weeks old
# was 'futgg_price_sync' - the RETIRED per-card Playwright scraper, whose
# row is a tombstone frozen at its final run. The collector that was
# actually struggling, futgg_sales_sync, wrote no heartbeat at all.
#
# So the pipeline presented as: two fresh workers, one long-dead worker
# under a sales-shaped name, and no row anywhere for the live sales
# collector. Every market surface was empty and nothing anywhere said
# why - not because a row went stale, but because the row that mattered
# did not exist, and iterating over whatever rows happened to be present
# could never notice an absent one.
#
# Hence two rules below: check collectors BY NAME so absence is a
# failure, and treat a punctual `ok: false` as a failure too - a
# collector rate-limited down to near-zero throughput checks in exactly
# on schedule while the data behind it drains.

from datetime import datetime, timedelta, timezone  # noqa: E402


def _freshness(heartbeats, *, priced=14950, stale=100, stale_sales=None):
    return {
        "heartbeats": heartbeats,
        "cards_priced": priced,
        "cards_stale": stale,
        "stale_sales_by_priority_tier": stale_sales or {},
    }


def _install_freshness(monkeypatch, summary, fail=False):
    import app.services.market_data_provider as mdp

    class FakeProvider:
        def __init__(self, _pool):
            pass

        async def get_freshness_summary(self):
            if fail:
                raise RuntimeError("snapshot unreadable")
            return summary

    monkeypatch.setattr(mdp, "FutggMarketDataProvider", FakeProvider)


def _beat(worker, hours_ago, ok=True):
    return {
        "worker": worker,
        "last_run_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        "ok": ok,
    }


def _all_healthy(**overrides):
    """Every expected collector, fresh and ok. The baseline to deviate from."""
    beats = {w: _beat(w, 0.5) for w in rd.EXPECTED_COLLECTORS}
    beats.update(overrides)
    return [b for b in beats.values() if b is not None]


class TestTheCollectorCheck:
    def test_a_healthy_pipeline_is_ok(self, monkeypatch):
        _install_freshness(monkeypatch, _freshness(_all_healthy()))
        assert run(rd._check_market_data(FakePool()))["status"] == rd.OK

    def test_a_collector_that_never_reported_is_the_outage(self, monkeypatch):
        # The actual production shape: the live sales collector wrote no
        # heartbeat row at all, so there was nothing to find stale. An
        # absent collector is the same outage as a stopped one.
        _install_freshness(monkeypatch, _freshness(
            _all_healthy(futgg_sales_sync=None),
            stale_sales={"CRITICAL": 1718, "HIGH": 3924, "NORMAL": 1884, "LOW": 44, "DORMANT": 2950},
        ))
        result = run(rd._check_market_data(FakePool()))
        assert result["status"] == rd.DEGRADED
        assert result["reason"] == "collector_silent"
        silent = " ".join(result["detail"]["silent_collectors"])
        assert "futgg_sales_sync" in silent
        assert "never reported" in silent

    def test_a_retired_workers_tombstone_row_is_not_a_collector(self, monkeypatch):
        # futgg_price_sync is decommissioned. Its frozen row must not be
        # able to hold readiness at degraded forever, and must not stand
        # in for the sales collector that replaced it.
        _install_freshness(monkeypatch, _freshness(
            _all_healthy() + [_beat("futgg_price_sync", 21 * 24)]
        ))
        assert run(rd._check_market_data(FakePool()))["status"] == rd.OK

    def test_a_stopped_collector_is_named_with_its_age(self, monkeypatch):
        _install_freshness(monkeypatch, _freshness(
            _all_healthy(futgg_sales_sync=_beat("futgg_sales_sync", 21 * 24))
        ))
        result = run(rd._check_market_data(FakePool()))
        assert result["reason"] == "collector_silent"
        silent = " ".join(result["detail"]["silent_collectors"])
        # The two healthy workers must not hide the dead one.
        assert "futgg_sales_sync" in silent
        assert "504h" in silent

    def test_ok_true_does_not_rescue_a_dead_worker(self, monkeypatch):
        _install_freshness(monkeypatch, _freshness(
            _all_healthy(futgg_sales_sync=_beat("futgg_sales_sync", 21 * 24, ok=True))
        ))
        assert run(rd._check_market_data(FakePool()))["status"] == rd.DEGRADED

    def test_a_worker_that_has_never_run_counts_as_silent(self, monkeypatch):
        _install_freshness(monkeypatch, _freshness(_all_healthy(
            futgg_sales_sync={"worker": "futgg_sales_sync", "last_run_at": None, "ok": True},
        )))
        result = run(rd._check_market_data(FakePool()))
        assert result["status"] == rd.DEGRADED
        assert "futgg_sales_sync" in " ".join(result["detail"]["silent_collectors"])

    def test_a_punctual_collector_that_is_not_keeping_up_is_caught(self, monkeypatch):
        # The live failure: the sales worker runs every two hours exactly
        # as scheduled, but FUT.GG rate-limits its fast transport, the
        # circuit breaker falls back to page capture, and it manages
        # single-digit cards per cycle. Nothing is silent. Nothing has
        # crashed. The feed drains anyway.
        _install_freshness(monkeypatch, _freshness(
            _all_healthy(futgg_sales_sync=_beat("futgg_sales_sync", 1, ok=False))
        ))
        result = run(rd._check_market_data(FakePool()))
        assert result["status"] == rd.DEGRADED
        assert result["reason"] == "collector_failing"
        assert "futgg_sales_sync" in result["detail"]["failing_collectors"]

    def test_a_silent_collector_outranks_a_failing_one(self, monkeypatch):
        # Stopped is worse than slow, and is the first thing to say.
        _install_freshness(monkeypatch, _freshness(_all_healthy(
            futgg_sales_sync=_beat("futgg_sales_sync", 1, ok=False),
            futgg_player_sync=_beat("futgg_player_sync", 500),
        )))
        assert run(rd._check_market_data(FakePool()))["reason"] == "collector_silent"

    def test_stale_sales_are_reported_even_with_live_collectors(self, monkeypatch):
        # A collector can be running, reporting ok, and still be behind.
        _install_freshness(monkeypatch, _freshness(
            _all_healthy(), priced=1000,
            stale_sales={"CRITICAL": 900},
        ))
        result = run(rd._check_market_data(FakePool()))
        assert result["status"] == rd.DEGRADED
        assert result["reason"] == "sales_data_stale"

    def test_stale_prices_and_stale_sales_are_told_apart(self, monkeypatch):
        # They are separate workers and fail independently. Reporting the
        # wrong one sends someone to the wrong service.
        _install_freshness(monkeypatch, _freshness(_all_healthy(), priced=1000, stale=900))
        assert run(rd._check_market_data(FakePool()))["reason"] == "price_data_stale"

    def test_no_priced_cards_is_unavailable_not_degraded(self, monkeypatch):
        _install_freshness(monkeypatch, _freshness(_all_healthy(), priced=0))
        result = run(rd._check_market_data(FakePool()))
        assert result["status"] == rd.UNAVAILABLE
        assert result["reason"] == "no_priced_cards"

    def test_an_unreadable_snapshot_degrades_rather_than_crashing(self, monkeypatch):
        _install_freshness(monkeypatch, _freshness([]), fail=True)
        assert run(rd._check_market_data(FakePool()))["status"] == rd.DEGRADED

    def test_no_scrape_internals_reach_the_payload(self, monkeypatch):
        # The heartbeat `detail` string carries render failures, retry
        # counts and script versions. That is not an operator's first
        # question and it is not this check's job to publish it.
        _install_freshness(monkeypatch, _freshness(_all_healthy(
            futgg_sales_sync={
                **_beat("futgg_sales_sync", 21 * 24),
                "detail": "render_failures=28 script_version=v4",
            },
        )))
        assert "render_failures" not in json.dumps(run(rd._check_market_data(FakePool())))

    def test_the_freshness_query_reads_the_collectors_this_check_expects(self):
        # These two lists have to agree. If the provider stops returning
        # a worker this check expects, the check reports it silent
        # forever; if it returns one this check does not know about,
        # nothing watches it. That mismatch is the entire bug: the
        # provider selected the retired 'futgg_price_sync' and never
        # selected 'futgg_sales_sync', so the collector that mattered was
        # neither reported nor missed.
        import inspect
        import app.services.market_data_provider as mdp

        sql = inspect.getsource(mdp.FutggMarketDataProvider.get_freshness_summary)
        sql = sql.split("pipeline_heartbeats")[1].split('"""')[0]
        # Strip SQL comments: they discuss the retired worker by name.
        sql = "\n".join(
            line for line in sql.splitlines() if not line.strip().startswith("--")
        )
        for worker in rd.EXPECTED_COLLECTORS:
            assert f"'{worker}'" in sql, f"{worker} is checked but never selected"
        assert "'futgg_price_sync'" not in sql, (
            "futgg_price_sync is retired; its frozen row must not be served "
            "as a live collector"
        )

    def test_the_sales_collector_is_one_of_the_expected_ones(self):
        # The whole point. Sales are 70% of fair value and the gate on
        # every signal the engine emits; the collector that supplies them
        # was the one nothing watched.
        assert "futgg_sales_sync" in rd.EXPECTED_COLLECTORS

    def test_it_is_part_of_the_readiness_report(self):
        # The gap this fills: the single most important worker in the
        # product was not checked by anything.
        assert "_check_market_data" in inspect.getsource(rd.readiness)
