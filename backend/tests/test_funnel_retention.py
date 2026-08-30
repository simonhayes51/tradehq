# tests/test_funnel_retention.py
#
# funnel_events grows unbounded, and was flagged as such when it shipped.
# Twelve months is the default because a year is the shortest window that
# still lets a launch month be compared with the same month a year later,
# and because a behavioural record of individual accounts is not something
# to keep forever just because disk is cheap.
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

import pytest

from app.services import funnel_retention


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, expired, batch_size, locked=True):
        self.remaining = expired
        self.batch_size = batch_size
        self.locked = locked
        self.deletes = []
        self.statements = []

    async def execute(self, sql, *args):
        self.statements.append(sql)
        if sql.strip().startswith("DELETE"):
            days, size = args
            took = min(size, self.remaining)
            self.remaining -= took
            self.deletes.append(took)
            return f"DELETE {took}"
        return "OK"

    async def fetchval(self, sql, *args):
        self.statements.append(sql)
        if "pg_try_advisory_lock" in sql:
            return self.locked
        if "COUNT(*) FROM funnel_events" in sql and "occurred_at" not in sql:
            return 10_000
        return None

    async def fetchrow(self, sql, *args):
        self.statements.append(sql)
        return {"expired": self.remaining, "oldest": datetime(2024, 1, 1, tzinfo=timezone.utc)}


class FakePool:
    def __init__(self, expired=0, batch_size=5000, locked=True):
        self.conn = FakeConn(expired, batch_size, locked)

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self): return conn
            async def __aexit__(self, *a): return False

        return Ctx()


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(funnel_retention, "BATCH_PAUSE_SECONDS", 0.0)


class TestCounting:
    def test_count_only_deletes_nothing(self):
        pool = FakePool(expired=1234)
        counts = run(funnel_retention.count_expired(pool))
        assert counts["expired"] == 1234
        assert pool.conn.deletes == []

    def test_it_reports_the_horizon_it_used(self):
        assert run(funnel_retention.count_expired(FakePool(), retention_days=90))["retention_days"] == 90


class TestDryRun:
    def test_it_reports_what_would_go_without_deleting(self):
        pool = FakePool(expired=777)
        result = run(funnel_retention.purge_expired(pool, dry_run=True))
        assert result.deleted == 777
        assert result.dry_run is True
        assert pool.conn.deletes == []


class TestBoundedBatches:
    def test_it_deletes_in_chunks_not_one_enormous_statement(self):
        # A single unqualified DELETE over millions of rows takes a long
        # lock on the table the tracking endpoint is writing to.
        pool = FakePool(expired=12_000)
        result = run(funnel_retention.purge_expired(pool, batch_size=5000))
        assert result.deleted == 12_000
        assert result.batches == 3
        assert pool.conn.deletes == [5000, 5000, 2000]

    def test_a_short_batch_ends_the_pass(self):
        pool = FakePool(expired=10)
        result = run(funnel_retention.purge_expired(pool, batch_size=5000))
        assert result.batches == 1
        assert result.remaining_estimate == 0

    def test_nothing_expired_is_one_cheap_pass(self):
        pool = FakePool(expired=0)
        result = run(funnel_retention.purge_expired(pool, batch_size=5000))
        assert result.deleted == 0
        assert result.status == "completed"

    def test_a_huge_backlog_stops_at_the_ceiling_and_says_so(self):
        # A first run against a year of accumulated rows must not run
        # unbounded; the next pass picks the rest up.
        pool = FakePool(expired=1_000_000)
        result = run(funnel_retention.purge_expired(pool, batch_size=1000, max_batches=5))
        assert result.batches == 5
        assert result.deleted == 5000
        assert result.status == "partial"

    def test_the_delete_targets_primary_keys(self):
        # `DELETE ... LIMIT n` is not valid Postgres, and deleting by
        # predicate alone would take one enormous lock.
        source = inspect.getsource(funnel_retention.purge_expired)
        assert "WHERE id IN (" in source
        assert "ORDER BY id" in source
        assert "LIMIT $2" in source

    def test_a_database_failure_is_reported_not_raised(self):
        class Broken:
            def acquire(self): raise RuntimeError("db gone")

        result = run(funnel_retention.purge_expired(Broken()))
        assert result.status == "failed"


class TestConfiguration:
    def test_the_default_horizon_is_twelve_months(self):
        assert funnel_retention.RETENTION_DAYS == 365

    def test_the_horizon_is_configurable(self, monkeypatch):
        monkeypatch.setenv("FUNNEL_RETENTION_DAYS", "730")
        import importlib

        reloaded = importlib.reload(funnel_retention)
        try:
            assert reloaded.RETENTION_DAYS == 730
        finally:
            monkeypatch.delenv("FUNNEL_RETENTION_DAYS", raising=False)
            importlib.reload(funnel_retention)

    def test_a_per_run_override_does_not_change_the_default(self):
        pool = FakePool(expired=5)
        run(funnel_retention.purge_expired(pool, retention_days=30, batch_size=10))
        assert funnel_retention.RETENTION_DAYS == 365


class TestScheduling:
    def test_it_runs_monthly_not_daily(self):
        assert funnel_retention._is_due(datetime(2026, 3, 1, tzinfo=timezone.utc)) is True
        assert funnel_retention._is_due(datetime(2026, 3, 2, tzinfo=timezone.utc)) is False
        assert funnel_retention._is_due(datetime(2026, 3, 31, tzinfo=timezone.utc)) is False

    def test_a_second_worker_does_nothing(self):
        assert run(funnel_retention.purge_with_lock(FakePool(locked=False))) is None

    def test_the_lock_is_held_for_the_whole_purge(self):
        source = inspect.getsource(funnel_retention.purge_with_lock)
        assert source.index("purge_expired") < source.index("pg_advisory_unlock")

    def test_it_uses_the_existing_worker_pattern_not_a_new_scheduler(self):
        import pathlib

        main_py = pathlib.Path(__file__).resolve().parents[1] / "main.py"
        source = main_py.read_text()
        assert "retention_loop" in source
        # No new scheduling dependency was introduced for this.
        requirements = (pathlib.Path(__file__).resolve().parents[1] / "requirements.txt").read_text().lower()
        for scheduler in ("apscheduler", "celery", "rq==", "schedule=="):
            assert scheduler not in requirements, scheduler


class TestNoAggregateWasInvented:
    def test_it_deletes_and_does_not_start_writing_a_summary_table(self):
        # The brief says to preserve longer-term aggregates IF THEY EXIST.
        # They do not - the dashboard computes everything live - so this
        # must not quietly become a new, untested source of truth about
        # the product's history.
        source = inspect.getsource(funnel_retention)
        assert "INSERT INTO" not in source.upper()
        assert "CREATE TABLE" not in source.upper()


class TestTheManualCommand:
    def test_it_offers_count_only_and_dry_run(self):
        import pathlib

        script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "purge_funnel_events.py"
        assert script.exists()
        source = script.read_text()
        assert "--count" in source
        assert "--dry-run" in source
        assert "purge_with_lock" in source
