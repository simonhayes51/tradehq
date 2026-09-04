"""
Tests for signed_fetch's crashed-anchor-page recovery.

THE BUG
-------
signed_fetch reuses ONE Playwright page for the entire process lifetime
(see futgg_sales_sync.py's own module docstring). Production evidence
(2026-09-04) showed that once that page's renderer crashes
(Playwright's "Target crashed"), it never recovers on its own: every
subsequent page.evaluate() call against it raises the exact same error
forever. Before this fix, process_batch's except-block treated that
identically to an ordinary throttled batch - backing off and retrying
against a page backing off can never fix, escalating the circuit
breaker's backoff toward its cap while producing zero sales, cycle
after cycle, until an external container restart happened to replace
it.

SignedPageHandle wraps the page/context/anchor in one shared, mutable
object so process_batch can recreate a crashed page in place; every
caller (run_forever's dispatch loop, _reprobe_signed_fetch) reads
`handle.page` fresh each call rather than holding a stale reference.

No DB and no real Playwright: process_batch's crash branch returns
before ever touching the database, so a fake page/context is enough.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_sales_sync as mod  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class _FakePage:
    def __init__(self, name: str, evaluate_error: Exception | None = None):
        self.name = name
        self.evaluate_error = evaluate_error
        self._closed = False
        self.goto_calls: list[str] = []
        self.evaluate_calls = 0

    def is_closed(self) -> bool:
        return self._closed

    async def evaluate(self, script, arg):
        self.evaluate_calls += 1
        if self.evaluate_error is not None:
            raise self.evaluate_error
        return []

    async def goto(self, url, **kwargs):
        self.goto_calls.append(url)

    async def close(self):
        self._closed = True


class _FakeContext:
    def __init__(self, pages_to_create: list[_FakePage] | None = None):
        # If given, new_page() hands these out in order; otherwise makes
        # plain healthy fakes.
        self._queue = list(pages_to_create or [])
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        if self._queue:
            return self._queue.pop(0)
        return _FakePage(f"page-{self.new_page_calls}")


def _stats():
    return {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}


def _row(card_id: int) -> dict:
    return {"source_card_id": card_id, "rating": 85, "next_sales_due_at": None,
            "sales_first_read_at": None, "sales_consecutive_failures": 0,
            "effective_tier": "NORMAL"}


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn(_NullCtx):
    def transaction(self):
        return _NullCtx()


class _FakePool:
    """Only ever needs to support an empty to_insert/updates pass-through
    - the crash-path tests below never reach apply_batch_results at all,
    and the one healthy-batch regression test has nothing to write."""

    def acquire(self):
        return _FakeConnCtx()


class _FakeConnCtx(_NullCtx):
    async def __aenter__(self):
        return _FakeConn()


class TestIsCrashedPageError:
    def test_target_crashed_is_recognised(self):
        assert mod.is_crashed_page_error(Exception("Page.evaluate: Target crashed"))

    def test_closed_target_is_recognised(self):
        assert mod.is_crashed_page_error(
            Exception("Target page, context or browser has been closed")
        )

    def test_an_ordinary_timeout_is_not_a_crash(self):
        assert not mod.is_crashed_page_error(Exception("Timeout 45000ms exceeded"))

    def test_a_malformed_payload_is_not_a_crash(self):
        assert not mod.is_crashed_page_error(Exception("malformed_payload"))


class TestSignedPageHandleRecreate:
    def test_recreate_replaces_the_page_and_closes_the_old_one(self):
        old = _FakePage("old")
        context = _FakeContext()
        handle = mod.SignedPageHandle(old, context, "https://www.fut.gg/players/anchor/")

        run(handle.recreate())

        assert handle.page is not old
        assert context.new_page_calls == 1
        assert handle.page.goto_calls == ["https://www.fut.gg/players/anchor/"]
        assert old.is_closed()

    def test_recreate_failure_leaves_the_old_page_in_place(self):
        old = _FakePage("old")

        class _ExplodingContext:
            async def new_page(self):
                raise RuntimeError("browser context is gone")

        handle = mod.SignedPageHandle(old, _ExplodingContext(), "https://www.fut.gg/players/anchor/")

        run(handle.recreate())  # must not raise

        assert handle.page is old
        assert not old.is_closed()


class TestProcessBatchCrashRecovery:
    def test_a_crashed_page_is_recreated_and_the_batch_is_recorded_failed(self):
        old = _FakePage("old", evaluate_error=Exception("Page.evaluate: Target crashed"))
        replacement = _FakePage("replacement")
        context = _FakeContext(pages_to_create=[replacement])
        handle = mod.SignedPageHandle(old, context, "https://www.fut.gg/players/anchor/")

        stats = _stats()
        throttle = mod.AdaptiveThrottle(8, 0.5)
        rows = [_row(1001)]

        run(mod.process_batch(handle, pool=None, rows=rows, stats=stats,
                               timers=mod.StageTimers(), throttle=throttle))

        assert stats["batch_errors"] == 1
        # A crash still counts as a fully-throttled batch for the
        # circuit breaker's own bookkeeping - stopping further
        # selection this cycle is still correct while the page was
        # dead; recreating it is what makes the NEXT batch able to
        # succeed instead of failing identically forever.
        assert throttle.consecutive_fully_throttled_batches == 1
        # The actual fix: the handle now points at a fresh page, not
        # the permanently-crashed one.
        assert handle.page is replacement
        assert context.new_page_calls == 1

    def test_a_non_crash_failure_does_not_recreate_the_page(self):
        page = _FakePage("page", evaluate_error=Exception("Timeout 45000ms exceeded"))
        context = _FakeContext()
        handle = mod.SignedPageHandle(page, context, "https://www.fut.gg/players/anchor/")

        stats = _stats()
        throttle = mod.AdaptiveThrottle(8, 0.5)
        rows = [_row(1002)]

        run(mod.process_batch(handle, pool=None, rows=rows, stats=stats,
                               timers=mod.StageTimers(), throttle=throttle))

        assert stats["batch_errors"] == 1
        assert handle.page is page  # unchanged - not a crash, no recreation needed
        assert context.new_page_calls == 0

    def test_a_closed_page_is_recreated_even_without_a_crash_marker(self):
        # is_closed() is checked independently of the exception text -
        # covers a page closed for some other reason (e.g. by another
        # part of the process) that doesn't happen to raise "Target
        # crashed" wording.
        page = _FakePage("page", evaluate_error=Exception("some other Playwright error"))
        page._closed = True
        replacement = _FakePage("replacement")
        context = _FakeContext(pages_to_create=[replacement])
        handle = mod.SignedPageHandle(page, context, "https://www.fut.gg/players/anchor/")

        stats = _stats()
        throttle = mod.AdaptiveThrottle(8, 0.5)

        run(mod.process_batch(handle, pool=None, rows=[_row(1003)], stats=stats,
                               timers=mod.StageTimers(), throttle=throttle))

        assert handle.page is replacement

    def test_a_healthy_batch_is_unaffected_by_the_handle_wrapper(self):
        # Regression check: wrapping the page in a handle must not
        # change ordinary successful behaviour. apply_batch_results
        # still runs and updates stats normally (no DB write, since
        # APPLY defaults True but there's nothing to insert/update from
        # an empty result set here - only exercising that no exception
        # is raised and no recreation happens).
        page = _FakePage("page")
        context = _FakeContext()
        handle = mod.SignedPageHandle(page, context, "https://www.fut.gg/players/anchor/")
        stats = _stats()
        throttle = mod.AdaptiveThrottle(8, 0.5)

        run(mod.process_batch(handle, pool=_FakePool(), rows=[], stats=stats,
                               timers=mod.StageTimers(), throttle=throttle))

        assert page.evaluate_calls == 1
        assert context.new_page_calls == 0
        assert handle.page is page
