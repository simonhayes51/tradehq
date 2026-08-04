"""
Tests for easysbc_sbc_sync.py: HTTP retry/backoff behaviour, malformed
payload handling, insert-vs-update tracking, transactional writes, the
overlap-guarding advisory lock, and the missing-DATABASE_URL fail-fast.
No live network or live Postgres - fake aiohttp session/asyncpg
connection objects throughout, matching this repo's existing test style
(see test_futgg_player_sync.py).
"""
from __future__ import annotations

import asyncio
import functools
import importlib
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import easysbc_sbc_sync as mod


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Each .get() call pops the next canned item from `responses` (in
    order) - either a _FakeResponse (used as the async context manager)
    or an Exception instance (raised synchronously from .get() itself,
    simulating a connection failure before any response exists)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeLockConn:
    def __init__(self, acquire_lock=True):
        self.acquire_lock = acquire_lock
        self.unlocked = False
        self.closed = False

    async def fetchval(self, query, *args):
        return self.acquire_lock

    async def execute(self, query, *args):
        if "pg_advisory_unlock" in query:
            self.unlocked = True

    async def close(self):
        self.closed = True


class _FakeEventConn:
    """Simulates upsert_event's ON CONFLICT ... RETURNING id,
    (xmax = 0) AS inserted - first time a given external_id is seen,
    inserted=True; every time after, inserted=False (an update)."""

    def __init__(self):
        self.seen_external_ids: set[str] = set()

    async def fetchrow(self, query, *args):
        external_id = args[0]
        inserted = external_id not in self.seen_external_ids
        self.seen_external_ids.add(external_id)
        return {"id": (hash(external_id) % 100000) + 1, "inserted": inserted}


class _FakeWriteConn:
    """Records every execute() call; can be told to raise on the Nth
    call (1-indexed) to simulate a write failing partway through
    write_detail's transaction."""

    def __init__(self, fail_after: int | None = None):
        self.calls: list[str] = []
        self.fail_after = fail_after

    def transaction(self):
        return _FakeTxn()

    async def execute(self, query, *args):
        self.calls.append(" ".join(query.split()))
        if self.fail_after is not None and len(self.calls) == self.fail_after:
            raise RuntimeError("simulated database failure")


def _diag():
    return defaultdict(int)


_real_sleep = asyncio.sleep


async def _fast_sleep(*_args, **_kwargs):
    """Stand-in for asyncio.sleep in retry/backoff tests - awaits the
    REAL asyncio.sleep(0) (a single event-loop tick, not the module's own
    now-patched asyncio.sleep) so backoff waits don't slow the suite down
    without needing to actually monkeypatch time.sleep semantics."""
    await _real_sleep(0)


# ---------------------------------------------------------------------
# HTTP retry / backoff (429, 5xx, network exceptions, bounded retries)
# ---------------------------------------------------------------------


@run_async
async def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(mod.asyncio, "sleep", _fast_sleep)
    session = _FakeSession([
        _FakeResponse(429, headers={}),
        _FakeResponse(429, headers={"Retry-After": "0.01"}),
        _FakeResponse(200, payload={"ok": True}),
    ])
    diag = _diag()

    result = await mod._get_json_with_retry(session, "https://x/y", diag)

    assert result == {"ok": True}
    assert session.calls == 3
    assert diag["http_429"] == 2


@run_async
async def test_retries_on_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(mod.asyncio, "sleep", _fast_sleep)
    session = _FakeSession([
        _FakeResponse(503),
        _FakeResponse(200, payload={"ok": True}),
    ])
    diag = _diag()

    result = await mod._get_json_with_retry(session, "https://x/y", diag)

    assert result == {"ok": True}
    assert diag["http_5xx"] == 1


@run_async
async def test_retries_are_bounded_not_infinite(monkeypatch):
    monkeypatch.setattr(mod.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(mod, "MAX_RETRIES", 3)
    # Always 500 - one more response queued than could possibly be consumed,
    # so an off-by-one that retried an extra time would surface as an
    # IndexError from the fake session running out of canned responses.
    session = _FakeSession([_FakeResponse(500) for _ in range(5)])
    diag = _diag()

    result = await mod._get_json_with_retry(session, "https://x/y", diag)

    assert result is None
    assert session.calls == 4  # 1 initial attempt + MAX_RETRIES(3) retries


@run_async
async def test_network_exception_retries_then_gives_up(monkeypatch):
    monkeypatch.setattr(mod.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(mod, "MAX_RETRIES", 2)
    session = _FakeSession([
        ConnectionError("boom"),
        ConnectionError("boom"),
        ConnectionError("boom"),
    ])
    diag = _diag()

    result = await mod._get_json_with_retry(session, "https://x/y", diag)

    assert result is None
    assert diag["http_exceptions"] == 1
    assert session.calls == 3


@run_async
async def test_non_200_non_retryable_status_returns_none_immediately(monkeypatch):
    session = _FakeSession([_FakeResponse(404)])
    diag = _diag()

    result = await mod._get_json_with_retry(session, "https://x/y", diag)

    assert result is None
    assert session.calls == 1  # no retry for a plain 404
    assert diag["http_non_200"] == 1


# ---------------------------------------------------------------------
# Malformed payload handling
# ---------------------------------------------------------------------


def test_extract_list_accepts_bare_array():
    assert mod._extract_list([1, 2, 3]) == [1, 2, 3]


def test_extract_list_accepts_known_wrapper_keys():
    assert mod._extract_list({"data": [1, 2]}) == [1, 2]
    assert mod._extract_list({"sets": [1]}) == [1]


def test_extract_list_rejects_unrecognised_shape():
    assert mod._extract_list({"totally_unexpected": True}) is None
    assert mod._extract_list("not even a dict or list") is None
    assert mod._extract_list(None) is None


@run_async
async def test_fetch_listing_stops_cleanly_on_malformed_payload(monkeypatch):
    monkeypatch.setattr(mod, "MAX_LISTING_PAGES", 5)

    async def fake_get_json(session, url, diag):
        return {"unexpected_shape": True}

    monkeypatch.setattr(mod, "_get_json_with_retry", fake_get_json)
    diag = _diag()

    result = await mod.fetch_listing(object(), diag)

    assert result == []
    assert diag["listing_shape_unrecognised"] == 1


@run_async
async def test_fetch_listing_stops_cleanly_on_request_failure(monkeypatch):
    async def fake_get_json(session, url, diag):
        return None  # _get_json_with_retry exhausted its own retries

    monkeypatch.setattr(mod, "_get_json_with_retry", fake_get_json)
    diag = _diag()

    result = await mod.fetch_listing(object(), diag)

    assert result == []


# ---------------------------------------------------------------------
# Successful fetch/write + no-change rerun (insert vs update tracking)
# ---------------------------------------------------------------------


@run_async
async def test_upsert_event_reports_insert_then_update_on_rerun():
    conn = _FakeEventConn()
    set_fields = {
        "external_id": "1293", "set_id": 1293, "title": "Test Set",
        "description": None,
    }

    event_id_1, inserted_1 = await mod.upsert_event(conn, set_fields)
    event_id_2, inserted_2 = await mod.upsert_event(conn, set_fields)

    assert inserted_1 is True
    assert inserted_2 is False  # rerun with the same external_id is an update
    assert event_id_1 == event_id_2


@run_async
async def test_write_detail_writes_event_details_and_challenges():
    conn = _FakeWriteConn()
    set_fields = {
        "title": "Test Set", "category": None, "total_cost_coins": 50000,
        "repeatable": False, "reward_description": "85+ Player Pick",
        "ends_at": None,
    }
    challenges = [
        mod.parse_challenge_fields({"name": "Challenge 1", "requirements": ["Min. 1 Rare"]}, 0),
    ]

    await mod.write_detail(conn, event_id=1, set_fields=set_fields, challenges=challenges)

    joined = " ".join(conn.calls)
    assert "UPDATE market_events" in joined
    assert "INSERT INTO sbc_details" in joined
    assert "DELETE FROM sbc_challenges" in joined
    assert "INSERT INTO sbc_challenges" in joined


# ---------------------------------------------------------------------
# Database failure rollback
# ---------------------------------------------------------------------


@run_async
async def test_write_detail_failure_propagates_and_stops_further_writes():
    """A mid-transaction failure must propagate (so the real
    conn.transaction() context manager issues a ROLLBACK) rather than
    being swallowed, and must not proceed to statements after the
    failure point."""
    # Fail on the 3rd execute() call (UPDATE market_events, INSERT
    # sbc_details, then DELETE sbc_challenges is where this lands).
    conn = _FakeWriteConn(fail_after=3)
    set_fields = {
        "title": "Test Set", "category": None, "total_cost_coins": 50000,
        "repeatable": False, "reward_description": None, "ends_at": None,
    }
    challenges = [
        mod.parse_challenge_fields({"name": "Challenge 1", "requirements": []}, 0),
    ]

    with pytest.raises(RuntimeError, match="simulated database failure"):
        await mod.write_detail(conn, event_id=1, set_fields=set_fields, challenges=challenges)

    # Exactly 3 statements ran before the failure; the per-challenge
    # INSERT after the DELETE never executed.
    assert len(conn.calls) == 3
    assert not any("INSERT INTO sbc_challenges" in c for c in conn.calls)


# ---------------------------------------------------------------------
# Overlapping-run advisory lock
# ---------------------------------------------------------------------


@run_async
async def test_crawl_once_skips_cleanly_when_lock_not_acquired(monkeypatch):
    monkeypatch.setattr(mod, "DIAGNOSTIC_MODE", False)
    lock_conn = _FakeLockConn(acquire_lock=False)

    async def fake_connect(dsn):
        return lock_conn

    async def fake_create_pool(*a, **kw):
        raise AssertionError("pool must not be created when the lock isn't held")

    monkeypatch.setattr(mod.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(mod.asyncpg, "create_pool", fake_create_pool)

    result = await mod.crawl_once()

    assert result is True  # a benign skip, not a failure
    assert lock_conn.closed is True
    assert lock_conn.unlocked is False  # never held it, nothing to unlock


@run_async
async def test_crawl_once_releases_lock_when_acquired(monkeypatch):
    """Even when something downstream blows up, the lock must still be
    released - proven here by making pool creation itself fail right
    after the lock is acquired."""
    monkeypatch.setattr(mod, "DIAGNOSTIC_MODE", False)
    lock_conn = _FakeLockConn(acquire_lock=True)

    async def fake_connect(dsn):
        return lock_conn

    async def fake_create_pool(*a, **kw):
        raise RuntimeError("simulated pool creation failure")

    monkeypatch.setattr(mod.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(mod.asyncpg, "create_pool", fake_create_pool)

    with pytest.raises(RuntimeError, match="simulated pool creation failure"):
        await mod.crawl_once()

    assert lock_conn.unlocked is True
    assert lock_conn.closed is True


# ---------------------------------------------------------------------
# Missing required env vars
# ---------------------------------------------------------------------


def test_missing_database_url_raises_a_clear_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            importlib.reload(mod)
    finally:
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        importlib.reload(mod)  # restore working module state for later tests


# ---------------------------------------------------------------------
# Diagnostic mode
# ---------------------------------------------------------------------


@run_async
async def test_run_diagnostic_reports_success_on_recognisable_shape():
    session = _FakeSession([
        _FakeResponse(200, payload=[{"id": 1293, "name": "Test Set"}]),
        _FakeResponse(200, payload={"id": 1293, "name": "Test Set"}),
        _FakeResponse(200, payload=[{"name": "Challenge 1", "requirements": ["Min. 1 Rare"]}]),
    ])
    diag = _diag()

    ok = await mod.run_diagnostic(session, diag)

    assert ok is True


@run_async
async def test_run_diagnostic_reports_failure_on_unrecognisable_listing_shape():
    session = _FakeSession([_FakeResponse(200, payload={"nonsense": True})])
    diag = _diag()

    ok = await mod.run_diagnostic(session, diag)

    assert ok is False


@run_async
async def test_diagnostic_mode_skips_the_lock_and_the_db(monkeypatch):
    """DIAGNOSTIC_MODE must be pure network + logging - crawl_once() must
    never touch asyncpg at all in this mode."""
    monkeypatch.setattr(mod, "DIAGNOSTIC_MODE", True)

    async def fail_if_called(*a, **kw):
        raise AssertionError("diagnostic mode must not touch the database")

    monkeypatch.setattr(mod.asyncpg, "connect", fail_if_called)
    monkeypatch.setattr(mod.asyncpg, "create_pool", fail_if_called)

    async def fake_run_diagnostic(session, diag):
        return True

    monkeypatch.setattr(mod, "run_diagnostic", fake_run_diagnostic)

    result = await mod.crawl_once()

    assert result is True
