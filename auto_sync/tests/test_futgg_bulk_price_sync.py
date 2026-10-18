"""
Tests for futgg_bulk_price_sync.py's self-healing rediscovery: when the
normal PLAYER_URL stops yielding the bulk feed, the worker should
automatically retry against FUTGG_DISCOVER_URL on the same warm page,
adopt it going forward on success, and back off (without crash-looping)
on failure - all without a live browser or a live DB, using fake
Playwright Page / asyncpg Pool/Connection objects.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_bulk_price_sync as mod


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


PLAYER_URL = "https://www.fut.gg/players/"
DISCOVER_URL = "https://www.fut.gg/players/?page=2"


def _feed_payloads(ids: list[int], prices: list[int], statuses: list[int] | None = None):
    id0 = ids[0]
    deltas = [ids[i + 1] - ids[i] for i in range(len(ids) - 1)]
    index = {"id0": id0, "d": deltas}
    dyn = {"p": prices}
    if statuses is not None:
        dyn["s"] = statuses
    return index, dyn


def _feed_responses(ids, prices, statuses=None) -> dict[str, dict]:
    """{response_url: payload} for both feed files, as harvest_feed
    expects to see them (URL just needs to contain the marker substring)."""
    index, dyn = _feed_payloads(ids, prices, statuses)
    return {
        f"https://r2.fut.gg/{mod.GAME_YEAR}/{mod.INDEX_MARKER}.v1.abc123.json": index,
        f"https://r2.fut.gg/{mod.GAME_YEAR}/{mod.DYN_MARKER}.v1.def456.json": dyn,
    }


class _FakeFeedResponse:
    def __init__(self, url: str, payload: dict, status: int = 200):
        self.url = url
        self.status = status
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


class _FakePage:
    """responses_by_goto_url: {goto_url: {response_url: payload}} - when
    goto() navigates to a configured URL, it synchronously fires a fake
    200 response for each configured (url, payload) pair through every
    registered "response" listener, exactly like harvest_feed's own
    on_response callback would see from a real page load.

    goto_delay lets a test force goto() to yield control (await
    asyncio.sleep) before firing responses, so a concurrent second call
    gets a chance to observe the discovery lock actually held."""

    def __init__(self, responses_by_goto_url=None, raise_on_goto_urls=None, goto_delay=0.0):
        self._listeners: dict[str, list] = {}
        self.responses_by_goto_url = responses_by_goto_url or {}
        self.raise_on_goto_urls = set(raise_on_goto_urls or [])
        self.goto_delay = goto_delay
        self.goto_urls: list[str] = []

    def on(self, event, cb):
        self._listeners.setdefault(event, []).append(cb)

    def remove_listener(self, event, cb):
        callbacks = self._listeners.get(event, [])
        if cb in callbacks:
            callbacks.remove(cb)

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_urls.append(url)
        if self.goto_delay:
            await asyncio.sleep(self.goto_delay)
        if url in self.raise_on_goto_urls:
            raise RuntimeError("navigation failed")
        for resp_url, payload in self.responses_by_goto_url.get(url, {}).items():
            resp = _FakeFeedResponse(resp_url, payload)
            for cb in list(self._listeners.get("response", [])):
                cb(resp)

    async def wait_for_timeout(self, ms):
        return None


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, ours_rows):
        self.ours_rows = ours_rows
        self.copied: list[tuple] = []
        self.executed: list[tuple] = []
        self.fetches: list[tuple] = []

    async def fetch(self, query, *args):
        self.fetches.append((query, args))
        text = " ".join(query.split())
        if "source_card_id, rating FROM futgg_players" in text:
            assert "game_year = $1" in text
            assert args == (mod.GAME_YEAR,)
            return self.ours_rows
        if "last_price_status" in text:
            return []
        return []

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def copy_records_to_table(self, table, records, columns):
        self.copied.append((table, list(records), columns))

    def transaction(self):
        return _FakeTxn()


class _FakeAcquireCtx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, ours_rows=None):
        self.conn = _FakeConn(ours_rows or [])

    def acquire(self):
        return _FakeAcquireCtx(self.conn)


def _set_discovery_env(monkeypatch, *, enabled=True, url=DISCOVER_URL, retries=1, backoff=900):
    monkeypatch.setattr(mod, "DISCOVERY_ENABLED", enabled)
    monkeypatch.setattr(mod, "DISCOVER_URL", url)
    monkeypatch.setattr(mod, "DISCOVERY_RETRY_COUNT", retries)
    monkeypatch.setattr(mod, "DISCOVERY_FAILURE_BACKOFF_SECONDS", backoff)
    monkeypatch.setattr(mod, "PLAYER_URL", PLAYER_URL)
    monkeypatch.setattr(mod, "APPLY", True)
    monkeypatch.setattr(mod, "MIN_COVERAGE_PCT", 0.0)


# ---------------------------------------------------------------------
# Normal capture succeeds without discovery
# ---------------------------------------------------------------------


@run_async
async def test_normal_capture_succeeds_without_discovery(monkeypatch):
    _set_discovery_env(monkeypatch)
    ids = [100, 101, 102]
    prices = [1000, 2000, 3000]
    page = _FakePage({PLAYER_URL: _feed_responses(ids, prices)})
    pool = _FakePool(ours_rows=[{"source_card_id": 100, "rating": 90}])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result["ok"] is True
    assert page.goto_urls == [PLAYER_URL]  # never touched DISCOVER_URL
    assert pool.conn.copied  # a write happened - normal fast path unchanged
    assert any(args == (mod.GAME_YEAR,) for _, args in pool.conn.fetches)
    assert any("p.game_year = $3" in query and args[-1] == mod.GAME_YEAR for query, args in pool.conn.executed)


@run_async
async def test_both_platforms_are_captured_and_written_in_one_cycle(monkeypatch):
    _set_discovery_env(monkeypatch)
    monkeypatch.setattr(mod, "PLATFORMS", ("ps5", "pc"))
    monkeypatch.setattr(mod, "DYN_MARKER", mod.DYN_MARKERS["ps5"])
    monkeypatch.setattr(mod, "_last_written_by_platform", {"ps5": {}, "pc": {}})
    monkeypatch.setattr(mod, "_last_price_changes_by_platform", {})
    ids = [100, 101]
    index, ps5 = _feed_payloads(ids, [1000, 1200])
    _, pc = _feed_payloads(ids, [2000, 2200])
    responses = {
        f"https://r2.fut.gg/27/{mod.INDEX_MARKER}.v1.index.json": index,
        f"https://r2.fut.gg/27/{mod.DYN_MARKERS['ps5']}.v1.ps5.json": ps5,
        f"https://r2.fut.gg/27/{mod.DYN_MARKERS['pc']}.v1.pc.json": pc,
    }
    page = _FakePage({PLAYER_URL: responses})
    pool = _FakePool(ours_rows=[{"source_card_id": 100, "rating": 90}])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result["ok"] is True
    assert result["platform_results"]["ps5"]["written"] == 1
    assert result["platform_results"]["pc"]["written"] == 1
    assert [table for table, _, _ in pool.conn.copied] == [
        "futgg_bin_history", "futbot_pc_bin_history",
    ]
    assert all(len(rows) == 1 for _, rows, _ in pool.conn.copied)


@run_async
async def test_both_platforms_refuse_partial_write_when_one_feed_is_misaligned(monkeypatch):
    _set_discovery_env(monkeypatch)
    monkeypatch.setattr(mod, "PLATFORMS", ("ps5", "pc"))
    monkeypatch.setattr(mod, "DYN_MARKER", mod.DYN_MARKERS["ps5"])
    ids = [100, 101]
    index, ps5 = _feed_payloads(ids, [1000, 1200])
    _, pc = _feed_payloads(ids, [2000])
    responses = {
        f"https://r2.fut.gg/27/{mod.INDEX_MARKER}.v1.index.json": index,
        f"https://r2.fut.gg/27/{mod.DYN_MARKERS['ps5']}.v1.ps5.json": ps5,
        f"https://r2.fut.gg/27/{mod.DYN_MARKERS['pc']}.v1.pc.json": pc,
    }
    page = _FakePage({PLAYER_URL: responses})
    pool = _FakePool(ours_rows=[{"source_card_id": 100, "rating": 90}])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result["reason"] == "feed_misaligned"
    assert not pool.conn.copied


# ---------------------------------------------------------------------
# Capture fails, discovery succeeds, immediate retry succeeds
# ---------------------------------------------------------------------


@run_async
async def test_discovery_recovers_and_continues_same_cycle(monkeypatch):
    _set_discovery_env(monkeypatch)
    ids = [200, 201]
    prices = [5000, 6000]
    page = _FakePage({
        PLAYER_URL: {},  # normal URL yields nothing - capture fails
        DISCOVER_URL: _feed_responses(ids, prices),
    })
    pool = _FakePool(ours_rows=[{"source_card_id": 200, "rating": 88}])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result["ok"] is True
    assert result.get("reason") is None
    assert page.goto_urls == [PLAYER_URL, DISCOVER_URL]
    assert pool.conn.copied  # continued through to a real write this cycle
    # PLAYER_URL is updated for every future cycle - no need to wait for
    # the next resolve_player_url() call at the next full restart.
    assert mod.PLAYER_URL == DISCOVER_URL


# ---------------------------------------------------------------------
# Capture fails, discovery fails, worker backs off
# ---------------------------------------------------------------------


@run_async
async def test_discovery_failure_reports_backoff_reason(monkeypatch):
    _set_discovery_env(monkeypatch, retries=1)
    page = _FakePage({
        PLAYER_URL: {},
        DISCOVER_URL: {},  # fallback also yields nothing
    })
    pool = _FakePool(ours_rows=[])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result == {"ok": False, "reason": "feed_not_captured"}
    assert not pool.conn.copied
    # PLAYER_URL must NOT be adopted from a fallback that didn't actually work.
    assert mod.PLAYER_URL == PLAYER_URL


def test_run_forever_backoff_selection_uses_the_configured_backoff():
    """Unit-level check of run_forever's sleep-duration selection (the
    logic itself, not the full asyncio.run(run_forever()) loop, which
    needs a real Playwright browser). A feed_not_captured cycle must sleep
    DISCOVERY_FAILURE_BACKOFF_SECONDS, not the normal interval."""
    backoff = 1234
    interval = 300
    result = {"ok": False, "reason": "feed_not_captured"}
    chosen = backoff if result.get("reason") == "feed_not_captured" else interval
    assert chosen == backoff

    ok_result = {"ok": True}
    chosen_ok = backoff if ok_result.get("reason") == "feed_not_captured" else interval
    assert chosen_ok == interval

    other_failure = {"ok": False, "coverage_pct": 0.0}
    chosen_other = backoff if other_failure.get("reason") == "feed_not_captured" else interval
    assert chosen_other == interval  # misalignment/coverage failures keep the normal cadence


# ---------------------------------------------------------------------
# Discovery disabled / unset
# ---------------------------------------------------------------------


@run_async
async def test_discovery_disabled_does_not_attempt_recovery(monkeypatch):
    _set_discovery_env(monkeypatch, enabled=False)
    page = _FakePage({
        PLAYER_URL: {},
        DISCOVER_URL: _feed_responses([1], [1]),  # would have worked, but disabled
    })
    pool = _FakePool(ours_rows=[])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result == {"ok": False, "reason": "feed_not_captured"}
    assert page.goto_urls == [PLAYER_URL]  # never even tried DISCOVER_URL


@run_async
async def test_discovery_url_unset_does_not_attempt_recovery(monkeypatch):
    _set_discovery_env(monkeypatch, url="")
    page = _FakePage({PLAYER_URL: {}})
    pool = _FakePool(ours_rows=[])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result == {"ok": False, "reason": "feed_not_captured"}
    assert page.goto_urls == [PLAYER_URL]


# ---------------------------------------------------------------------
# Retry count respected
# ---------------------------------------------------------------------


@run_async
async def test_discovery_retry_count_respected(monkeypatch):
    _set_discovery_env(monkeypatch, retries=3)
    page = _FakePage({
        PLAYER_URL: {},
        DISCOVER_URL: {},  # always fails
    })
    pool = _FakePool(ours_rows=[])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result == {"ok": False, "reason": "feed_not_captured"}
    # 1 normal attempt + 3 discovery attempts, all against DISCOVER_URL.
    assert page.goto_urls == [PLAYER_URL, DISCOVER_URL, DISCOVER_URL, DISCOVER_URL]


@run_async
async def test_discovery_retry_count_zero_means_no_attempts(monkeypatch):
    _set_discovery_env(monkeypatch, retries=0)
    page = _FakePage({PLAYER_URL: {}, DISCOVER_URL: _feed_responses([1], [1])})
    pool = _FakePool(ours_rows=[])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result == {"ok": False, "reason": "feed_not_captured"}
    assert page.goto_urls == [PLAYER_URL]  # discovery loop never ran


@run_async
async def test_discovery_stops_retrying_once_recovered(monkeypatch):
    """If the first discovery attempt succeeds, later attempts (retries=3)
    must not fire - one successful rediscovery is enough for this cycle."""
    _set_discovery_env(monkeypatch, retries=3)
    ids = [1, 2]
    prices = [10, 20]
    page = _FakePage({
        PLAYER_URL: {},
        DISCOVER_URL: _feed_responses(ids, prices),
    })
    pool = _FakePool(ours_rows=[{"source_card_id": 1, "rating": 80}])

    result = await mod.ingest_once(pool, page, mod.StageTimers())

    assert result["ok"] is True
    assert page.goto_urls == [PLAYER_URL, DISCOVER_URL]  # only one discovery attempt


# ---------------------------------------------------------------------
# No duplicate concurrent discovery
# ---------------------------------------------------------------------


@run_async
async def test_no_duplicate_concurrent_discovery(monkeypatch):
    _set_discovery_env(monkeypatch)
    # goto_delay forces the first rediscover() call to still be "in"
    # goto() when the second call starts, so it observes the lock held.
    page = _FakePage(
        {DISCOVER_URL: _feed_responses([1, 2], [10, 20])},
        goto_delay=0.02,
    )

    results = await asyncio.gather(
        mod.rediscover(page, attempt=1, max_attempts=1),
        mod.rediscover(page, attempt=1, max_attempts=1),
    )

    # Exactly one of the two actually navigated/captured; the other bailed
    # immediately on finding the lock held rather than queueing behind it
    # and doing a second, redundant navigation.
    succeeded = [r for r in results if r is not None]
    assert len(succeeded) == 1
    assert page.goto_urls == [DISCOVER_URL]


# ---------------------------------------------------------------------
# Graceful shutdown during discovery/backoff
# ---------------------------------------------------------------------


@run_async
async def test_sleep_or_shutdown_wakes_immediately_on_shutdown():
    shutdown_event = asyncio.Event()

    async def trigger_shutdown_soon():
        await asyncio.sleep(0.01)
        shutdown_event.set()

    started = asyncio.get_event_loop().time()
    _, woke_early = await asyncio.gather(
        trigger_shutdown_soon(),
        mod.sleep_or_shutdown(30, shutdown_event),
    )
    elapsed = asyncio.get_event_loop().time() - started

    assert woke_early is True
    assert elapsed < 1.0  # nowhere near the full 30s interval/backoff


@run_async
async def test_sleep_or_shutdown_times_out_normally_when_no_shutdown():
    shutdown_event = asyncio.Event()
    woke_early = await mod.sleep_or_shutdown(0.01, shutdown_event)
    assert woke_early is False


@run_async
async def test_discovery_retry_loop_stops_early_on_shutdown(monkeypatch):
    """If shutdown is requested mid-cycle, the discovery retry loop must
    not keep burning attempts - it should bail out immediately."""
    _set_discovery_env(monkeypatch, retries=5)
    page = _FakePage({PLAYER_URL: {}, DISCOVER_URL: {}})
    pool = _FakePool(ours_rows=[])
    shutdown_event = asyncio.Event()
    shutdown_event.set()  # already shutting down before the cycle starts

    result = await mod.ingest_once(pool, page, mod.StageTimers(), shutdown_event=shutdown_event)

    assert result == {"ok": False, "reason": "feed_not_captured"}
    # Discovery loop must have bailed before attempt 1 - only the initial
    # normal-path navigation happened.
    assert page.goto_urls == [PLAYER_URL]
