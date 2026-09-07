"""
Tests for the periodic browser recycle (BROWSER_RECYCLE_MINUTES).

Production incident (2026-09-07): the sales-sync container's CPU climbed
steadily for ~2 hours, pinned at its ceiling, then the process was killed
outright with no exception and no shutdown log line - Coolify's own
CPU/memory graphs showed a vertical cliff to 0 at the exact moment the
log output stopped, the signature of an external SIGKILL rather than
anything the script itself decided to do. It did not recover on its own.

This worker deliberately keeps one headed Chromium browser/context (and,
for signed_fetch, one anchor page) alive for the entire process lifetime -
see SignedPageHandle's own docstring. SignedPageHandle.recreate() already
replaces a single crashed/closed PAGE, but nothing before this recycled
the browser/context itself, so a slow accumulation inside one long-lived
renderer had no way to be released short of an unplanned kill.

These tests cover _launch_browser_resources() (the extracted helper both
the initial startup and the periodic recycle now share) and pin
run_forever's control flow: the recycle must sit strictly between cycles
(after the runtime-budget/shutdown checks, before the cycle-end backoff
sleep), never mid-batch.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import pytest

import futgg_sales_sync as mod


def run_async(coro_fn):
    import asyncio
    import functools

    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))

    return wrapper


class FakePage:
    def __init__(self):
        self.goto_calls = []

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))


class FakeContext:
    def __init__(self):
        self.pages: list[FakePage] = []

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self):
        self.closed = False
        self.contexts: list[FakeContext] = []

    async def new_context(self, **kwargs):
        context = FakeContext()
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.launched: list[FakeBrowser] = []

    async def launch(self, **kwargs):
        browser = FakeBrowser()
        self.launched.append(browser)
        return browser


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()


class TestLaunchBrowserResources:
    @run_async
    async def test_signed_fetch_only_creates_one_anchor_page_and_no_capture_pages(self):
        pw = FakePlaywright()
        browser, context, signed_page_handle, capture_pages = await mod._launch_browser_resources(
            pw, need_signed=True, need_page_capture=False, anchor="https://example.test/anchor",
        )
        assert signed_page_handle is not None
        assert signed_page_handle.page.goto_calls == [
            ("https://example.test/anchor", {"wait_until": "domcontentloaded", "timeout": mod.TIMEOUT_MS})
        ]
        assert capture_pages == []
        assert len(context.pages) == 1

    @run_async
    async def test_page_capture_only_creates_page_concurrency_pages_and_no_anchor(self):
        pw = FakePlaywright()
        browser, context, signed_page_handle, capture_pages = await mod._launch_browser_resources(
            pw, need_signed=False, need_page_capture=True, anchor="https://example.test/anchor",
        )
        assert signed_page_handle is None
        assert len(capture_pages) == mod.PAGE_CONCURRENCY
        assert len(context.pages) == mod.PAGE_CONCURRENCY

    @run_async
    async def test_auto_mode_creates_both_on_the_same_browser_and_context(self):
        # "auto" transport mode needs both an anchor page AND a
        # page_capture pool ready, sharing one browser/context, so a
        # mid-run transport switch never needs a fresh browser.
        pw = FakePlaywright()
        browser, context, signed_page_handle, capture_pages = await mod._launch_browser_resources(
            pw, need_signed=True, need_page_capture=True, anchor="https://example.test/anchor",
        )
        assert signed_page_handle is not None
        assert len(capture_pages) == mod.PAGE_CONCURRENCY
        assert signed_page_handle.context is context
        # One shared browser/context, not one per transport.
        assert len(pw.chromium.launched) == 1
        assert len(pw.chromium.launched[0].contexts) == 1

    @run_async
    async def test_repeated_calls_launch_independent_browsers(self):
        # This is exactly what the periodic recycle does: call this
        # helper again against the SAME playwright object to get a
        # brand-new browser instance, distinct from the one it replaces.
        pw = FakePlaywright()
        first = await mod._launch_browser_resources(
            pw, need_signed=True, need_page_capture=False, anchor="https://example.test/anchor",
        )
        second = await mod._launch_browser_resources(
            pw, need_signed=True, need_page_capture=False, anchor="https://example.test/anchor",
        )
        assert first[0] is not second[0]
        assert len(pw.chromium.launched) == 2


class TestBrowserRecycleMinutesConfig:
    def test_defaults_to_a_positive_bound_not_disabled(self):
        # A silent regression to 0 here would quietly turn this feature
        # back off - the whole point is that it defends against an
        # unbounded browser lifetime by default, not only when someone
        # remembers to opt in.
        assert mod.BROWSER_RECYCLE_MINUTES > 0

    def test_reads_from_the_documented_env_var(self, monkeypatch):
        monkeypatch.setenv("FUTGG_SALES_BROWSER_RECYCLE_MINUTES", "45")
        import importlib

        reloaded = importlib.reload(mod)
        try:
            assert reloaded.BROWSER_RECYCLE_MINUTES == 45
        finally:
            monkeypatch.delenv("FUTGG_SALES_BROWSER_RECYCLE_MINUTES", raising=False)
            importlib.reload(mod)


class TestRunForeverRecycleWiring:
    """Source-inspection tests, matching this repo's existing convention
    for pinning run_forever's control flow (see
    test_futgg_sales_sync_integrity.py's own run_forever tests) - a full
    functional test of run_forever would need to fake nearly everything
    it touches (pool, playwright, select_due, ...), so the established
    pattern here is to pin the ORDER and GATING of the control flow
    directly against the source instead."""

    def test_recycle_check_is_gated_on_browser_recycle_minutes(self):
        source = inspect.getsource(mod.run_forever)
        assert "BROWSER_RECYCLE_MINUTES" in source
        assert "_launch_browser_resources(" in source

    def test_recycle_happens_after_the_shutdown_and_runtime_budget_checks(self):
        # Must never fire on an iteration that is about to break out of
        # the loop anyway.
        source = inspect.getsource(mod.run_forever)
        shutdown_check = source.index("if shutdown_event.is_set():")
        runtime_exhausted_check = source.index("if cycle_runtime_exhausted:", shutdown_check)
        recycle_check = source.index("BROWSER_RECYCLE_MINUTES", runtime_exhausted_check)
        assert recycle_check > runtime_exhausted_check > shutdown_check

    def test_recycle_happens_before_the_cycle_end_backoff_sleep(self):
        # The recycle must land between cycles - after a cycle's own
        # finalization, before the sleep that separates it from the
        # next one - never in the middle of a batch loop.
        source = inspect.getsource(mod.run_forever)
        recycle_check = source.index("BROWSER_RECYCLE_MINUTES", source.index("if shutdown_event.is_set():"))
        backoff_assignment = source.index("backoff_seconds = (", recycle_check)
        assert backoff_assignment > recycle_check

    def test_recycle_closes_the_old_browser_before_relaunching(self):
        source = inspect.getsource(mod.run_forever)
        recycle_check = source.index("BROWSER_RECYCLE_MINUTES", source.index("if shutdown_event.is_set():"))
        relaunch = source.index("_launch_browser_resources(", recycle_check)
        between = source[recycle_check:relaunch]
        assert "browser.close()" in between

    def test_recycle_resets_the_browser_started_at_clock(self):
        source = inspect.getsource(mod.run_forever)
        recycle_check = source.index("BROWSER_RECYCLE_MINUTES", source.index("if shutdown_event.is_set():"))
        relaunch = source.index("_launch_browser_resources(", recycle_check)
        following = source[relaunch:relaunch + 300]
        assert "browser_started_at = time.monotonic()" in following

    def test_initial_startup_also_uses_the_shared_launch_helper(self):
        # Both the first launch and every later recycle must build the
        # browser/context/pages identically - a hand-duplicated initial
        # setup would silently drift from what recycling produces.
        source = inspect.getsource(mod.run_forever)
        recycle_check_index = source.index("BROWSER_RECYCLE_MINUTES", source.index("if shutdown_event.is_set():"))
        initial_setup = source[:recycle_check_index]
        assert initial_setup.count("_launch_browser_resources(") == 1
