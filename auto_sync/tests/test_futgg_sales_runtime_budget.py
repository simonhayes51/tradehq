"""
Tests for SalesSync's bounded-run runtime budget and advisory-lock
contention visibility.

THE BUGS THESE FIX
-------------------
1. MAX_RUNTIME_MINUTES was only checked at the top of run_forever's outer
   per-cycle while loop. A cycle already in flight - including every
   batch inside select_due()'s up-to-SELECT_LIMIT due list - ran to
   completion regardless of how long that took. At a degraded throttle
   (batch=1, delay=20s observed in production 2026-09-03/04), a single
   cycle selecting SELECT_LIMIT=400 cards could take on the order of two
   hours - many times the configured 45-minute bound - with nothing in
   the process able to stop it early.

2. When the advisory lock was still held by another instance after the
   full retry window, run_forever logged a warning and exited - with no
   pipeline_heartbeats row written. A scheduled invocation skipped this
   way was indistinguishable, from the outside, from one that never
   fired at all: a previous instance stuck for hours (per bug 1) could
   silently cancel the next scheduled invocation too, invisibly.

No DB and no network for the pure-function tests (runtime_deadline,
runtime_budget_exhausted, sleep_within_budget's decision logic). The
run_forever wiring itself is checked the same way this file's sibling
(test_futgg_sales_sync_integrity.py) checks other run_forever behaviour -
by inspecting the function's own source for the call sites that must be
there - since exercising the real bounded loop end-to-end would require
a live Postgres, a live Playwright browser, and FUT.GG itself.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_sales_sync as mod  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# runtime_deadline / runtime_budget_exhausted - pure functions
# ---------------------------------------------------------------------

class TestRuntimeDeadline:
    def test_unbounded_run_has_no_deadline(self, monkeypatch):
        monkeypatch.setattr(mod, "MAX_RUNTIME_MINUTES", 0)
        assert mod.runtime_deadline(started_at=1000.0) is None

    def test_bounded_run_deadline_is_started_at_plus_minutes(self, monkeypatch):
        monkeypatch.setattr(mod, "MAX_RUNTIME_MINUTES", 45)
        assert mod.runtime_deadline(started_at=1000.0) == 1000.0 + 45 * 60


class TestRuntimeBudgetExhausted:
    def test_none_deadline_never_exhausted(self):
        assert mod.runtime_budget_exhausted(None, now=1e9) is False

    def test_before_deadline_not_exhausted(self):
        assert mod.runtime_budget_exhausted(2000.0, now=1000.0) is False

    def test_at_or_after_deadline_is_exhausted(self):
        assert mod.runtime_budget_exhausted(2000.0, now=2000.0) is True
        assert mod.runtime_budget_exhausted(2000.0, now=2000.1) is True


# ---------------------------------------------------------------------
# sleep_within_budget - the deadline-aware replacement for
# sleep_or_shutdown at every backoff/throttle-delay sleep site
# ---------------------------------------------------------------------

class TestSleepWithinBudget:
    def test_unbounded_run_sleeps_normally(self):
        # deadline=None: behaves exactly like the plain sleep_or_shutdown
        # it wraps - never reports budget_exhausted.
        event = asyncio.Event()
        shutdown, exhausted = run(mod.sleep_within_budget(0.01, event, None))
        assert exhausted is False
        assert shutdown is False

    def test_deadline_already_passed_skips_sleep_entirely(self):
        event = asyncio.Event()
        # deadline in the past relative to `now` - must return instantly
        # (no real asyncio sleep), reporting budget_exhausted.
        shutdown, exhausted = run(
            mod.sleep_within_budget(5.0, event, deadline=100.0, now=200.0)
        )
        assert exhausted is True
        assert shutdown is False

    def test_sleep_that_would_cross_the_deadline_is_skipped_not_shortened(self):
        # 90s left, but the requested sleep (300s, e.g. a circuit-breaker
        # backoff) would cross the deadline - must not sleep the
        # remaining 90s and then discover the deadline at the next check;
        # it must stop now instead ("finish cleanly" rather than "sleep
        # until expiry").
        event = asyncio.Event()
        shutdown, exhausted = run(
            mod.sleep_within_budget(300.0, event, deadline=1090.0, now=1000.0)
        )
        assert exhausted is True
        assert shutdown is False

    def test_sleep_that_comfortably_fits_the_remaining_budget_still_sleeps(self):
        event = asyncio.Event()
        shutdown, exhausted = run(
            mod.sleep_within_budget(0.01, event, deadline=1000.0 + 3600, now=1000.0)
        )
        assert exhausted is False
        assert shutdown is False

    def test_shutdown_event_set_is_still_reported_when_within_budget(self):
        event = asyncio.Event()
        event.set()
        shutdown, exhausted = run(
            mod.sleep_within_budget(5.0, event, deadline=1000.0 + 3600, now=1000.0)
        )
        assert exhausted is False
        assert shutdown is True


# ---------------------------------------------------------------------
# run_forever wiring - source-inspection, matching this repo's existing
# convention for pinning run_forever's control flow (see
# test_futgg_sales_sync_integrity.py's own run_forever tests).
# ---------------------------------------------------------------------

def test_signed_fetch_batch_loop_checks_the_runtime_budget_before_dispatch():
    """The deadline check must come BEFORE process_batch() is called for
    each batch - checking after would still let one more (possibly very
    slow) batch through."""
    source = inspect.getsource(mod.run_forever)
    loop_start = source.index('if transport == "signed_fetch":')
    dispatch = source.index("await process_batch(\n", loop_start)
    between = source[loop_start:dispatch]
    assert "runtime_budget_exhausted(deadline)" in between
    assert "cycle_runtime_exhausted = True" in between


def test_page_capture_batch_loop_checks_the_runtime_budget_before_dispatch():
    source = inspect.getsource(mod.run_forever)
    branch_start = source.index("# page_capture - chunked the same way")
    dispatch = source.index("await process_batch_page_capture(", branch_start)
    between = source[branch_start:dispatch]
    assert "runtime_budget_exhausted(deadline)" in between
    assert "cycle_runtime_exhausted = True" in between


def test_throttle_delay_sleep_is_deadline_aware():
    """The per-batch throttle.delay sleep (signed_fetch only) must go
    through sleep_within_budget, not the plain sleep_or_shutdown, so a
    long delay near the end of a bounded run can't sleep past the
    deadline."""
    source = inspect.getsource(mod.run_forever)
    marker = source.index("if throttle.delay:")
    following = source[marker:marker + 400]
    assert "sleep_within_budget(" in following


def test_end_of_cycle_backoff_sleep_is_deadline_aware():
    source = inspect.getsource(mod.run_forever)
    marker = source.index("backoff_seconds = (")
    following = source[marker:marker + 1800]
    assert "sleep_within_budget(" in following
    assert "sleep_or_shutdown(backoff_seconds" not in following


def test_cycle_runtime_exhausted_exits_before_the_next_select_due_call():
    """Once a cycle stops early because the runtime budget ran out, the
    outer loop must break - not loop around into another select_due()
    call with no time left to do anything with it."""
    source = inspect.getsource(mod.run_forever)
    flag_set = source.index("if cycle_runtime_exhausted:")
    backoff_seconds_starts = source.index("backoff_seconds = (", flag_set)
    between = source[flag_set:backoff_seconds_starts]
    assert "break" in between


def test_lock_contention_writes_a_heartbeat_before_exiting():
    """A skipped invocation (lock still held by another instance) must
    no longer look, from the outside, identical to one that simply never
    fired - see write_lock_skipped_heartbeat's own docstring."""
    source = inspect.getsource(mod.run_forever)
    lock_check = source.index("if not got_lock:")
    lock_return = source.index("return", lock_check)
    between = source[lock_check:lock_return]
    assert "write_lock_skipped_heartbeat(" in between


class _FakePoolConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def acquire(self):
        return _FakePoolConn()


class _RaisingPool:
    def acquire(self):
        raise RuntimeError("pool exhausted")


@pytest.fixture
def fake_monitoring(monkeypatch):
    """Install a fake `monitoring` module - same technique
    test_futgg_sales_heartbeat.py uses for write_heartbeat, since both
    functions import monitoring lazily so a missing optional dependency
    cannot stop the collector at load time."""
    calls = []

    async def fake_heartbeat(conn, worker, ok=True, detail=None):
        calls.append({"worker": worker, "ok": ok, "detail": detail})

    module = types.ModuleType("monitoring")
    module.heartbeat = fake_heartbeat
    monkeypatch.setitem(sys.modules, "monitoring", module)
    return calls


class TestWriteLockSkippedHeartbeat:
    def test_writes_ok_false_with_skipped_marker_in_detail(self, fake_monitoring):
        run(mod.write_lock_skipped_heartbeat(_FakePool(), waited_seconds=90.0))

        assert len(fake_monitoring) == 1
        assert fake_monitoring[0]["worker"] == mod.HEARTBEAT_WORKER
        assert fake_monitoring[0]["ok"] is False
        assert "skipped_due_to_lock" in fake_monitoring[0]["detail"]
        assert "90" in fake_monitoring[0]["detail"]

    def test_never_raises_if_heartbeat_write_fails(self, fake_monitoring):
        # Must not propagate - a monitoring failure must never crash the
        # worker it watches (same contract as write_heartbeat).
        run(mod.write_lock_skipped_heartbeat(_RaisingPool(), waited_seconds=90.0))
