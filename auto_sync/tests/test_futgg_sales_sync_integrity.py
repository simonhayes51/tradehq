"""
Tests for the SalesSync data-integrity fix and its supporting machinery:
never advancing next_sales_due_at on a failed fetch (the production bug -
never_read falling by 200/cycle despite zero successful reads), the
circuit breaker, the broadened throttle signal, and safe diagnostic
logging.

Pure-Python logic (is_throttle_signal, circuit_breaker_tripped, log
redaction) needs no DB and runs anywhere. apply_batch_results()'s actual
UPDATE/INSERT behaviour is tested against a REAL local Postgres (skipped
if unreachable) rather than a hand-rolled fake - NOW() + INTERVAL and
NULL-preservation semantics are exactly the kind of thing worth verifying
against the real thing, same approach used for the migration-runner and
EasySBC advisory-lock/xmax tests elsewhere in this repo.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_sales_sync as mod

ADMIN_DSN = os.getenv(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------
# Real-Postgres fixture (skipped if unreachable)
# ---------------------------------------------------------------------


async def _postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


async def _make_db() -> str:
    name = f"tradehq_sales_test_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    return name


async def _drop_db(name: str) -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


def _dsn_for(name: str) -> str:
    base = ADMIN_DSN.rsplit("/", 1)[0]
    return f"{base}/{name}"


@pytest.fixture
def dsn():
    if not asyncio.run(_postgres_reachable()):
        pytest.skip(f"no Postgres reachable at {ADMIN_DSN}")
    name = asyncio.run(_make_db())
    try:
        yield _dsn_for(name)
    finally:
        asyncio.run(_drop_db(name))


async def _seed_players(conn_dsn: str, cards: list[dict]) -> None:
    """Minimal futgg_players shape - only the columns this worker
    touches. Real production schema (migration 038) has many more
    columns; irrelevant to these tests."""
    conn = await asyncpg.connect(conn_dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE futgg_players (
                source_card_id BIGINT PRIMARY KEY,
                rating INTEGER,
                name TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                is_tradeable BOOLEAN,
                source_url TEXT,
                next_sales_due_at TIMESTAMPTZ,
                last_sales_status TEXT
            )
            """
        )
        # apply_batch_results() writes to futgg_sales_history - create it
        # via the real production DDL (ensure_schema), not a hand-rolled
        # copy that could quietly drift from the real column set.
        await mod.ensure_schema(conn)
        for card in cards:
            await conn.execute(
                """
                INSERT INTO futgg_players (source_card_id, rating, name, next_sales_due_at)
                VALUES ($1, $2, $3, $4)
                """,
                card["source_card_id"], card.get("rating", 85), card.get("name", "Test"),
                card.get("next_sales_due_at"),
            )
    finally:
        await conn.close()


async def _fetch_player(conn_dsn: str, card_id: int) -> dict:
    conn = await asyncpg.connect(conn_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT source_card_id, next_sales_due_at, last_sales_status, "
            "sales_first_read_at, sales_consecutive_failures FROM futgg_players "
            "WHERE source_card_id = $1",
            card_id,
        )
        return dict(row)
    finally:
        await conn.close()


async def _set_source_url(conn_dsn: str, card_id: int, url: str) -> None:
    conn = await asyncpg.connect(conn_dsn)
    try:
        await conn.execute(
            "UPDATE futgg_players SET source_url = $1 WHERE source_card_id = $2", url, card_id
        )
    finally:
        await conn.close()


def _row(card_id: int, rating: int = 85, next_sales_due_at=None) -> dict:
    return {"source_card_id": card_id, "rating": rating, "next_sales_due_at": next_sales_due_at}


class _Stats(dict):
    def __init__(self):
        super().__init__(ok=0, failed=0, sales_seen=0, batch_errors=0)


def _throttle():
    return mod.AdaptiveThrottle(8, 0.5)


# ---------------------------------------------------------------------
# Successful fetch advances next_sales_due_at (with and without sales)
# ---------------------------------------------------------------------


@run_async
async def test_successful_fetch_advances_next_sales_due_at(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [_row(1001, next_sales_due_at=None)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        results = [{
            "eaId": 1001,
            "completedAuctions": [{"soldPrice": 50000, "soldDate": "2026-08-03T12:00:00Z"}],
            "currentPrice": 51000,
        }]
        await mod.apply_batch_results(
            pool, [_row(1001, next_sales_due_at=None)], results, _Stats(), mod.StageTimers(), _throttle()
        )
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 1001)
    assert after["next_sales_due_at"] is not None
    assert after["last_sales_status"] == "success"


@run_async
async def test_successful_zero_sales_fetch_still_advances_it(monkeypatch, dsn):
    """A well-formed response with zero completed auctions is a real
    observation ('checked, nothing new'), not a failure - item 4."""
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [_row(1002, next_sales_due_at=None)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        results = [{"eaId": 1002, "completedAuctions": [], "currentPrice": None}]
        await mod.apply_batch_results(
            pool, [_row(1002, next_sales_due_at=None)], results, _Stats(), mod.StageTimers(), _throttle()
        )
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 1002)
    assert after["next_sales_due_at"] is not None
    assert after["last_sales_status"] == "success"


# ---------------------------------------------------------------------
# Failed fetches of every kind must NOT advance a never-read card
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    ["data_403", "sign_502", "challenge_required", "malformed_payload",
     "malformed_json", "no_signed_url", "exception:TypeError: x"],
)
@run_async
async def test_failed_fetch_never_advances_a_never_read_card(monkeypatch, dsn, error):
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [_row(2001, next_sales_due_at=None)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        results = [{"eaId": 2001, "error": error, "signStatus": 200, "dataStatus": 403}]
        await mod.apply_batch_results(
            pool, [_row(2001, next_sales_due_at=None)], results, _Stats(), mod.StageTimers(), _throttle()
        )
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 2001)
    # THE production bug: a failure must never convert "never read" into
    # "read (but failed)". That invariant is unchanged - it just no longer
    # lives in next_sales_due_at.
    #
    # It used to be expressed as "next_sales_due_at stays NULL", which
    # made one column mean both "never read" and "due right now", and THAT
    # is what produced the retry loop: the card came back on every single
    # cycle forever. The never-read fact now lives in sales_first_read_at,
    # which a failure still cannot touch, while next_sales_due_at carries
    # a real bounded retry time.
    assert after["sales_first_read_at"] is None
    assert after["next_sales_due_at"] is not None
    assert after["sales_consecutive_failures"] == 1
    assert after["last_sales_status"] == f"error:{error}"


@run_async
async def test_never_read_count_does_not_fall_after_a_fully_failed_cycle(monkeypatch, dsn):
    """The exact production symptom, reproduced: a whole batch of
    never-read cards, all fetches fail (data_403) - the never_read
    coverage count must be unchanged after - now measured on
    sales_first_read_at, the column that actually records read history."""
    monkeypatch.setattr(mod, "APPLY", True)
    cards = [_row(3000 + i, next_sales_due_at=None) for i in range(10)]
    await _seed_players(dsn, cards)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        conn = await pool.acquire()
        before = await conn.fetchval(
            "SELECT count(*) FROM futgg_players WHERE sales_first_read_at IS NULL"
        )
        await pool.release(conn)
        assert before == 10

        results = [{"eaId": c["source_card_id"], "error": "data_403"} for c in cards]
        await mod.apply_batch_results(pool, cards, results, _Stats(), mod.StageTimers(), _throttle())

        conn = await pool.acquire()
        after = await conn.fetchval(
            "SELECT count(*) FROM futgg_players WHERE sales_first_read_at IS NULL"
        )
        await pool.release(conn)
        assert after == 10  # unchanged - not 0, matching the reported bug
    finally:
        await pool.close()


# ---------------------------------------------------------------------
# A previously-read card gets a short, distinct retry backoff on failure
# ---------------------------------------------------------------------


@run_async
async def test_failed_fetch_gives_previously_read_card_a_short_backoff(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    monkeypatch.setattr(mod, "FAILURE_RETRY_BACKOFF_MINUTES", 2)
    old_due = datetime.now(timezone.utc) - timedelta(minutes=1)  # already due, previously read
    await _seed_players(dsn, [_row(4001, next_sales_due_at=old_due)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        row = _row(4001, next_sales_due_at=old_due)
        results = [{"eaId": 4001, "error": "data_403"}]
        await mod.apply_batch_results(pool, [row], results, _Stats(), mod.StageTimers(), _throttle())
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 4001)
    assert after["next_sales_due_at"] is not None
    assert after["last_sales_status"] == "error:data_403"
    # Distinct from (much shorter than) any real success interval - the
    # shortest configured tier (CRITICAL) has a 5-minute floor via
    # env_int's minimum; 2 here is clearly a backoff, not coverage.
    due_in = after["next_sales_due_at"] - datetime.now(timezone.utc)
    assert due_in < timedelta(minutes=3)


# ---------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------


def test_circuit_breaker_opens_on_mass_failure(monkeypatch):
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_THRESHOLD", 0.8)
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MIN_SAMPLE", 20)
    stats = {"ok": 4, "failed": 16, "sales_seen": 0, "batch_errors": 0}  # 20 attempted, 80% failed
    assert mod.circuit_breaker_tripped(stats) is True


def test_circuit_breaker_stays_closed_under_the_sample_floor(monkeypatch):
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_THRESHOLD", 0.8)
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MIN_SAMPLE", 20)
    stats = {"ok": 0, "failed": 8, "sales_seen": 0, "batch_errors": 0}  # 100% failed but only 8 attempted
    assert mod.circuit_breaker_tripped(stats) is False


def test_circuit_breaker_stays_closed_below_threshold(monkeypatch):
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_THRESHOLD", 0.8)
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MIN_SAMPLE", 20)
    stats = {"ok": 15, "failed": 5, "sales_seen": 0, "batch_errors": 0}  # 25% failed
    assert mod.circuit_breaker_tripped(stats) is False


def test_circuit_breaker_recovers_once_stats_reset_for_the_next_cycle():
    """Each cycle starts a fresh stats dict (see run_forever) - the
    breaker must not stay permanently open once a cycle actually
    recovers, since it's evaluated against that cycle's own running
    stats, not a global sticky flag."""
    tripped_cycle = {"ok": 2, "failed": 18, "sales_seen": 0, "batch_errors": 0}
    assert mod.circuit_breaker_tripped(tripped_cycle) is True

    next_cycle = {"ok": 18, "failed": 2, "sales_seen": 0, "batch_errors": 0}
    assert mod.circuit_breaker_tripped(next_cycle) is False


class TestNextBackoffSeconds:
    """Production evidence (1 September): a cycle re-tripped within
    seconds of resuming from a full CIRCUIT_BREAKER_BACKOFF_SECONDS
    (300s) wait - cards_ok=0 failed=9, tripped again in 25.3s. The
    throttle-streak fix (see above) ruled out the "inherited counter"
    bug; this was a REAL trip, meaning FUT.GG's own rate-limit window
    had not reliably cleared within a flat 300s. next_backoff_seconds()
    makes repeated trips wait longer instead of hammering the same
    still-active limit on a fixed schedule."""

    def test_a_single_trip_gets_the_base_backoff(self, monkeypatch):
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_BACKOFF_SECONDS", 300.0)
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS", 1800.0)
        assert mod.next_backoff_seconds(1) == 300.0

    def test_zero_or_negative_trips_also_get_the_base_backoff(self, monkeypatch):
        # Defensive: next_backoff_seconds must never return something
        # shorter than a single trip's own backoff.
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_BACKOFF_SECONDS", 300.0)
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS", 1800.0)
        assert mod.next_backoff_seconds(0) == 300.0
        assert mod.next_backoff_seconds(-1) == 300.0

    def test_each_consecutive_trip_doubles_the_wait(self, monkeypatch):
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_BACKOFF_SECONDS", 300.0)
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS", 100_000.0)
        assert mod.next_backoff_seconds(1) == 300.0
        assert mod.next_backoff_seconds(2) == 600.0
        assert mod.next_backoff_seconds(3) == 1200.0
        assert mod.next_backoff_seconds(4) == 2400.0

    def test_the_wait_is_capped(self, monkeypatch):
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_BACKOFF_SECONDS", 300.0)
        monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS", 1800.0)
        assert mod.next_backoff_seconds(10) == 1800.0


def test_run_forever_resets_the_cycle_trip_streak_on_a_clean_cycle():
    """consecutive_cycle_trips must reset in the same place
    note_signed_fetch_recovered() already does - a clean cycle proves
    the wait worked, so the NEXT trip (whenever it happens) should start
    back at the base backoff, not continue escalating from an old,
    unrelated streak of trips hours earlier."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    recovered_call = source.index("note_signed_fetch_recovered()")
    following = source[recovered_call:recovered_call + 200]
    assert "consecutive_cycle_trips = 0" in following


def test_run_forever_only_escalates_on_an_unproductive_trip():
    """Production evidence (1 September): cycles that collected 142 and
    185 cards (24-31 cards/min) each still eventually tripped near the
    end, same as the true "instant retrip" failure (cards_ok=0, 0/min)
    that motivated escalation in the first place. Treating every trip
    alike drove the backoff to its 1800s cap across four consecutive
    trips even though two of them were healthy, substantial cycles that
    just happened to hit the recurring adaptive-throttle ceiling again -
    AdaptiveThrottle easing its batch size back up and eventually
    overshooting is the expected shape of a working cycle, not proof the
    prior wait was too short. Escalation must be gated on the tripped
    cycle's own throughput (the same floor write_heartbeat's `ok` uses),
    not on breaker_tripped alone."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    # Anchor on note_signed_fetch_recovered() rather than the first
    # "consecutive_cycle_trips = 0" - that string also appears at the
    # top of the function (the pre-loop initialization), and searching
    # for the next "else:" from THAT position can land on an unrelated
    # else clause elsewhere in the function body instead of the
    # breaker_tripped escalation branch this test means to inspect.
    recovered_call = source.index("note_signed_fetch_recovered()")
    else_branch = source.index("else:", recovered_call)
    increment = source.index("consecutive_cycle_trips += 1", else_branch)
    between = source[else_branch:increment]
    assert "HEARTBEAT_MIN_CARDS_PER_MIN" in between
    assert "cycle_was_productive" in between


def test_run_forever_a_productive_trip_neither_resets_nor_increments_the_streak():
    """Corrected after a full 10-hour production trace (2 September):
    the first version of this escalation RESET the streak to 0 on every
    productive-but-tripped cycle, on the theory that a healthy cycle
    proves the prior wait was sufficient. It doesn't - it only proves
    that cycle's OWN wait was sufficient, which says nothing about the
    very next one. Across 61 real cycles, productive and unproductive
    trips alternated almost metronomically (28 of 61, 46%, collected
    zero cards) for the entire window - because a productive cycle
    always intervened, the old reset-on-productive logic meant the
    streak could never reach 2 and escalation never engaged even once
    in 10 hours, despite the same near-instant wall recurring on a
    stable cadence throughout. Only a cycle that runs completely clean
    (not breaker_tripped at all) may now reset the streak; a
    productive-but-tripped cycle must leave it exactly where it was."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    recovered_call = source.index("note_signed_fetch_recovered()")
    else_branch = source.index("else:", recovered_call)
    increment = source.index("consecutive_cycle_trips += 1", else_branch)
    between = source[else_branch:increment]
    assert "consecutive_cycle_trips = 0" not in between


def test_run_forever_logs_the_wall_clock_resume_time_on_a_trip():
    """Seen twice in one evening: an operator watching the log stream
    asked whether the worker had stalled during a backoff that was, in
    fact, exactly as long as designed - the only figure logged was
    "backing off Ns", which makes the reader do the arithmetic. A
    resume-time log line, gated to breaker_tripped only (a normal
    CYCLE_SLEEP between clean cycles needs no such reassurance), turns
    that into a direct answer the log already contains."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    trip_streak_update = source.index("consecutive_cycle_trips += 1")
    # The cycle-end backoff sleep now routes through sleep_within_budget
    # (deadline-aware - see runtime_budget_exhausted's docstring), not
    # the plain sleep_or_shutdown every earlier idle/reprobe/inter-batch
    # sleep in this function still uses.
    last_sleep_call = source.rindex("sleep_within_budget(")
    between = source[trip_streak_update:last_sleep_call]
    assert "if breaker_tripped:" in between
    assert "next cycle expected around" in between


def test_run_forever_uses_the_escalating_backoff_not_the_flat_constant():
    """The sleep at the end of a tripped cycle must route through
    next_backoff_seconds(), not the flat CIRCUIT_BREAKER_BACKOFF_SECONDS
    - using the constant directly here is exactly the bug this test
    guards against regressing to."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    trip_streak_update = source.index("consecutive_cycle_trips += 1")
    # The LAST sleep_within_budget call in the function is the cycle-end
    # backoff decision (earlier ones are the idle/reprobe/inter-batch
    # sleeps, which are irrelevant here).
    last_sleep_call = source.rindex("sleep_within_budget(")
    assert last_sleep_call >= trip_streak_update
    between = source[trip_streak_update:last_sleep_call]
    assert "next_backoff_seconds(consecutive_cycle_trips)" in between
    assert "CIRCUIT_BREAKER_BACKOFF_SECONDS if breaker_tripped" not in between


def test_the_throttle_streak_also_resets_each_cycle():
    """The test above only proves the STATS-based half of
    circuit_breaker_tripped() recovers between cycles. In production the
    breaker is called with `throttle` too (see run_forever), and that
    check fires on throttle.consecutive_fully_throttled_batches alone,
    independent of stats - a tripped cycle ends with that counter already
    at or above CONSECUTIVE_FULL_THROTTLE_BATCHES. `throttle` itself
    (batch_size/delay) deliberately survives across cycles - that's the
    whole point of AdaptiveThrottle - but without resetting this streak
    too, the very next cycle's first batch could push the INHERITED count
    straight back over the threshold and re-trip in seconds, giving the
    CIRCUIT_BREAKER_BACKOFF_SECONDS backoff no real chance to let FUT.GG's
    rate-limit window clear. Production logs showed exactly this: a
    productive ~150-175 card cycle immediately followed by a near-empty
    cards_ok=0 cycle failing and re-tripping in under 10s, roughly halving
    effective throughput."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    cycle_start = source.index(
        'stats = {"ok": 0, "failed": 0, "sales_seen": 0, "batch_errors": 0}'
    )
    batch_loop_start = source.index('if transport == "signed_fetch":', cycle_start)
    between = source[cycle_start:batch_loop_start]
    assert "throttle.consecutive_fully_throttled_batches = 0" in between


class _FakeLockConn:
    """fetchval() returns False for the first `fails_before_success`
    calls, then True - standing in for a Postgres advisory lock that a
    still-shutting-down previous instance is holding."""

    def __init__(self, fails_before_success: int):
        self.fails_before_success = fails_before_success
        self.calls = 0

    async def fetchval(self, sql, key):
        self.calls += 1
        return self.calls > self.fails_before_success


class TestAdvisoryLockAcquireRetries:
    """A rolling deploy on a continuous service starts the new container
    while the old one is still up - see the module docstring on
    acquire_advisory_lock_with_retry for the full incident this guards
    against: production evidence of a deploy leaving BOTH the new and
    the old container stopped, because the new one gave up on its very
    first pg_try_advisory_lock() attempt (which is expected to lose that
    race) instead of waiting for the old one to finish its own SIGTERM
    handling and release the lock."""

    @run_async
    async def test_succeeds_immediately_when_the_lock_is_free(self):
        conn = _FakeLockConn(fails_before_success=0)
        slept = []
        got = await mod.acquire_advisory_lock_with_retry(
            conn, 123, retry_seconds=90.0, poll_seconds=5.0,
            sleep=lambda s: slept.append(s), now=lambda: 0.0,
        )
        assert got is True
        assert conn.calls == 1
        assert slept == []  # no retry needed - must not sleep at all

    @run_async
    async def test_retries_until_the_previous_instance_releases_it(self):
        conn = _FakeLockConn(fails_before_success=3)
        clock = [0.0]
        slept = []

        async def fake_sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        got = await mod.acquire_advisory_lock_with_retry(
            conn, 123, retry_seconds=90.0, poll_seconds=5.0,
            sleep=fake_sleep, now=lambda: clock[0],
        )
        assert got is True
        assert conn.calls == 4
        assert slept == [5.0, 5.0, 5.0]

    @run_async
    async def test_gives_up_once_the_retry_window_is_spent(self):
        # A genuinely second, unrelated instance - not just a deploy
        # handoff - must still result in a clean exit rather than
        # retrying forever.
        conn = _FakeLockConn(fails_before_success=10_000)
        clock = [0.0]

        async def fake_sleep(seconds):
            clock[0] += seconds

        got = await mod.acquire_advisory_lock_with_retry(
            conn, 123, retry_seconds=20.0, poll_seconds=5.0,
            sleep=fake_sleep, now=lambda: clock[0],
        )
        assert got is False
        assert clock[0] >= 20.0

    @run_async
    async def test_a_zero_retry_window_still_tries_exactly_once(self):
        # retry_seconds=0.0 must not skip the attempt entirely - it means
        # "no retrying", not "no lock check at all".
        conn = _FakeLockConn(fails_before_success=10_000)
        got = await mod.acquire_advisory_lock_with_retry(
            conn, 123, retry_seconds=0.0, poll_seconds=5.0,
            sleep=lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")),
            now=lambda: 0.0,
        )
        assert got is False
        assert conn.calls == 1


def test_run_forever_retries_the_lock_instead_of_exiting_on_first_loss():
    """The old one-shot check ('if not got_lock: ... return') is exactly
    what turned a routine rolling deploy into the worker silently never
    coming back - see TestAdvisoryLockAcquireRetries. run_forever() must
    route through the retrying helper, not call pg_try_advisory_lock
    directly."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    lock_acquire = source.index("lock_conn = await pool.acquire()")
    not_got_lock = source.index("if not got_lock:", lock_acquire)
    between = source[lock_acquire:not_got_lock]
    assert "acquire_advisory_lock_with_retry(" in between


def test_shutdown_survives_a_dead_advisory_lock_connection():
    """Production evidence: a Railway "Stopping Container" teardown can cut
    the DB connection within milliseconds of the SIGTERM that triggers this
    shutdown path - before run_forever()'s finally block ever gets to run
    `pg_advisory_unlock`. asyncpg detects the dead connection and detaches
    lock_conn automatically, so calling it raises
    `asyncpg.exceptions.InterfaceError: cannot call Connection.execute():
    connection has been released back to the pool`. That must not propagate
    out of a *shutdown* path: Postgres already drops a session-scoped
    advisory lock the instant its connection dies, so the lock is gone
    either way, and an unhandled exception here only turns a routine
    redeploy into a scary "failed" traceback instead of a clean exit."""
    import inspect

    source = inspect.getsource(mod.run_forever)
    finally_start = source.index('finally:\n            log.info("shutting down')
    unlock_call = source.index("pg_advisory_unlock", finally_start)
    preceding = source[finally_start:unlock_call]
    assert "try:" in preceding, "the advisory unlock must be inside a try block"
    following = source[unlock_call:unlock_call + 400]
    assert "except Exception" in following


# ---------------------------------------------------------------------
# Adaptive throttle reacts to 403/sign failures, not only 429s
# ---------------------------------------------------------------------


class TestThrottleSignal:
    def test_429_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("data_429") is True

    def test_challenge_required_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("challenge_required") is True

    def test_data_403_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("data_403") is True

    def test_sign_5xx_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("sign_502") is True
        assert mod.is_throttle_signal("sign_500") is True

    def test_data_5xx_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("data_503") is True

    def test_malformed_payload_is_not_a_throttle_signal(self):
        # An application-level parsing problem, not the server pushing back.
        assert mod.is_throttle_signal("malformed_payload") is False
        assert mod.is_throttle_signal("no_signed_url") is False


@run_async
async def test_throttle_backs_off_on_403_batch_not_only_429(monkeypatch):
    throttle = mod.AdaptiveThrottle(8, 0.5)
    results = [{"eaId": i, "error": "data_403"} for i in range(8)]
    rows = [_row(i, next_sales_due_at=datetime.now(timezone.utc)) for i in range(8)]

    class _FakePool:
        def acquire(self):
            raise AssertionError("APPLY=false path must not touch the pool")

    monkeypatch.setattr(mod, "APPLY", False)
    await mod.apply_batch_results(_FakePool(), rows, results, _Stats(), mod.StageTimers(), throttle)

    assert throttle.delay > 0.5
    assert throttle.batch_size < 8


# ---------------------------------------------------------------------
# Diagnostic logging redacts tokens
# ---------------------------------------------------------------------


def test_log_failure_sample_never_logs_a_signed_url_or_token(caplog):
    result = {
        "error": "data_403",
        "signStatus": 200,
        "dataStatus": 403,
        "dataContentType": "text/html",
        "challengeRequired": False,
        "expiresIn": 120,
        "signedUrlIsAbsolute": False,
        "signedUrlOrigin": "https://www.fut.gg",
        "sameOriginAsPage": True,
        "signedUrlHasVerifyParam": True,
        # A body preview containing something token-shaped - must come
        # out redacted, not verbatim.
        "dataBodyPreview": "verify=" + ("a" * 80) + " some html body",
    }
    with caplog.at_level(logging.WARNING, logger="futgg_sales_sync"):
        mod.log_failure_sample(2001, result)

    text = " ".join(r.message for r in caplog.records)
    assert "a" * 80 not in text  # the long token-shaped run itself is redacted
    assert "card=2001" in text
    assert "error=data_403" in text


def test_fetch_script_never_returns_the_full_signed_url():
    """Source-level guard: none of the JS's `return { ... }` result
    objects may contain the bare `signedUrl` variable (which carries the
    verify token in its query string) - only the derived safe fields
    (signedUrlOrigin/signedUrlPath/signedUrlIsAbsolute/...) may appear.
    `signedUrl` legitimately appears elsewhere as a function argument
    (new URL(signedUrl, ...), fetch(signedUrl, ...)) - this only checks
    the returned result objects themselves.

    _FETCH_SCRIPT is now built from futgg_price_access.PRICE_ACCESS_CORE_JS
    (build_batch_script()), which carries exactly one deliberate,
    explicitly-guarded exception for the CLI probe's --show-signed-url
    debug path (see that module's own test_fetch_script_never_returns_
    the_full_signed_url for the full reasoning) - this batch script never
    triggers it (the per-item mapper always omits the 5th argument), but
    the guarded source text is still embedded since there is exactly one
    canonical implementation, not a per-caller copy. Strengthened, not
    weakened: any OTHER return block referencing signedUrl still fails
    this test outright."""
    import re
    script = mod._FETCH_SCRIPT
    return_blocks = re.findall(r"return\s*\{[^}]*\}", script)
    assert return_blocks, "expected at least one return {...} in the fetch script"
    signed_url_blocks = [b for b in return_blocks if re.search(r"\bsignedUrl\b", b)]
    assert len(signed_url_blocks) == 1, (
        f"expected exactly one return block to reference signedUrl "
        f"(the guarded reveal path), found {len(signed_url_blocks)}: {signed_url_blocks}"
    )
    only_block = signed_url_blocks[0]
    position = script.index(only_block)
    preceding_context = script[max(0, position - 300):position]
    assert "if (revealSignedUrl)" in preceding_context, (
        "the one return block that references signedUrl must be reachable "
        f"only through an immediately-preceding `if (revealSignedUrl)` guard - "
        f"preceding context was: {preceding_context!r}"
    )
    assert "parsed.origin" in script
    assert "parsed.pathname" in script


# ---------------------------------------------------------------------
# zero-sales vs with-sales success both use the same rating interval
# ---------------------------------------------------------------------


@run_async
async def test_zero_sales_and_nonzero_sales_both_use_the_rating_interval(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [
        _row(5001, rating=90, next_sales_due_at=None),
        _row(5002, rating=90, next_sales_due_at=None),
    ])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = [
            _row(5001, rating=90, next_sales_due_at=None),
            _row(5002, rating=90, next_sales_due_at=None),
        ]
        results = [
            {"eaId": 5001, "completedAuctions": [], "currentPrice": None},
            {"eaId": 5002, "completedAuctions": [
                {"soldPrice": 1000, "soldDate": "2026-08-03T12:00:00Z"}
            ], "currentPrice": 1000},
        ]
        await mod.apply_batch_results(pool, rows, results, _Stats(), mod.StageTimers(), _throttle())
    finally:
        await pool.close()

    zero_sales = await _fetch_player(dsn, 5001)
    with_sales = await _fetch_player(dsn, 5002)
    # Both succeeded -> both get the SAME 90-rated interval, regardless of
    # whether any sales actually came back this time.
    assert zero_sales["last_sales_status"] == "success"
    assert with_sales["last_sales_status"] == "success"
    gap = abs((zero_sales["next_sales_due_at"] - with_sales["next_sales_due_at"]).total_seconds())
    assert gap < 5


# ---------------------------------------------------------------------
# Repair SQL only targets failed attempts from the incident window
# ---------------------------------------------------------------------

_REPAIR_SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "repair_salessync_20260804_403_incident.sql"


def _extract_statement(sql_text: str, after_marker: str, before_marker: str) -> str:
    return sql_text.split(after_marker, 1)[1].split(before_marker, 1)[0].strip()


@run_async
async def test_repair_sql_only_resets_incident_window_failures(monkeypatch, dsn):
    """Seeds a realistic mix - pre-incident errors, in-incident errors
    (both never-read-turned-error and previously-read-turned-error), a
    genuinely successful card, and a post-incident (post-fix) error using
    the NEW shorter backoff - and asserts the repair SQL only resets the
    in-incident rows, exactly as documented in the SQL file's own header."""
    await _seed_players(dsn, [_row(i, next_sales_due_at=None) for i in range(9001, 9006)])

    incident_start = datetime(2026, 8, 4, 18, 56, 0, tzinfo=timezone.utc)

    conn = await asyncpg.connect(dsn)
    try:
        # 9001: pre-incident error (old bug, but well before the incident
        # window) - must NOT be touched.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:data_403' "
            "WHERE source_card_id = $1",
            9001, incident_start - timedelta(hours=2) + timedelta(minutes=5),
        )
        # 9002: in-incident, was never read before (the core bug case) -
        # must be reset back to NULL.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:data_403' "
            "WHERE source_card_id = $1",
            9002, incident_start + timedelta(minutes=10, seconds=5),
        )
        # 9003: in-incident, was previously read (pushed-out case) - must
        # also be reset.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:sign_502' "
            "WHERE source_card_id = $1",
            9003, incident_start + timedelta(hours=1, minutes=5),
        )
        # 9004: in-incident window, but a genuine SUCCESS - must never be
        # touched regardless of timing.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'success' "
            "WHERE source_card_id = $1",
            9004, incident_start + timedelta(minutes=10),
        )
        # 9005: at/after the incident start exactly - boundary case,
        # inclusive per the SQL's >= comparison - must be reset.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:data_403' "
            "WHERE source_card_id = $1",
            9005, incident_start + timedelta(minutes=5),
        )

        sql_text = _REPAIR_SQL_PATH.read_text()
        update_stmt = _extract_statement(
            sql_text,
            "-- STEP 2 - THE REPAIR",
            "-- STEP 3",
        )
        # Strip the leading comment lines the marker split left behind,
        # keep only the actual UPDATE statement.
        update_stmt = "UPDATE" + update_stmt.split("UPDATE", 1)[1]
        await conn.execute(update_stmt)

        rows = {
            r["source_card_id"]: r
            for r in await conn.fetch(
                "SELECT source_card_id, next_sales_due_at, last_sales_status FROM futgg_players "
                "WHERE source_card_id = ANY($1::bigint[])",
                [9001, 9002, 9003, 9004, 9005],
            )
        }

        assert rows[9001]["next_sales_due_at"] is not None  # pre-incident: untouched
        assert rows[9002]["next_sales_due_at"] is None       # in-incident, never-read: reset
        assert rows[9003]["next_sales_due_at"] is None       # in-incident, previously-read: reset
        assert rows[9004]["last_sales_status"] == "success"  # genuine success: never touched
        assert rows[9004]["next_sales_due_at"] is not None
        assert rows[9005]["next_sales_due_at"] is None        # boundary, inclusive: reset
    finally:
        await conn.close()


# ---------------------------------------------------------------------
# Network diagnostics (Cloudflare investigation instrumentation)
# ---------------------------------------------------------------------


class TestIsCfCookie:
    def test_known_cloudflare_cookie_names(self):
        for name in ("__cf_bm", "cf_clearance", "__cflb", "__cfruid", "__cfwaitingroom"):
            assert mod._is_cf_cookie(name) is True

    def test_cf_prefixed_unknown_cookie_still_flagged(self):
        assert mod._is_cf_cookie("cf_something_new") is True
        assert mod._is_cf_cookie("__cf_future_cookie") is True

    def test_unrelated_cookie_not_flagged(self):
        assert mod._is_cf_cookie("session_id") is False
        assert mod._is_cf_cookie("next-auth.csrf-token") is False

    def test_case_insensitive(self):
        assert mod._is_cf_cookie("CF_Clearance") is True


class _FakeDiagPage:
    def __init__(self, webdriver=False, user_agent="FakeUA/1.0"):
        self._webdriver = webdriver
        self._ua = user_agent
        self._listeners: dict[str, list] = {}

    async def evaluate(self, script, *args):
        if "navigator.webdriver" in script:
            return self._webdriver
        if "navigator.userAgent" in script:
            return self._ua
        return None

    def on(self, event, cb):
        self._listeners.setdefault(event, []).append(cb)

    def fire_request(self, request):
        for cb in list(self._listeners.get("request", [])):
            cb(request)

    def fire_response(self, response):
        for cb in list(self._listeners.get("response", [])):
            cb(response)


class _FakeDiagContext:
    def __init__(self, cookies):
        self._cookies = cookies

    async def cookies(self):
        return self._cookies


class _FakeDiagRequest:
    def __init__(self, url, method, headers, resource_type="fetch"):
        self.url = url
        self.method = method
        self._headers = headers
        self.resource_type = resource_type

    async def all_headers(self):
        return self._headers


class _FakeDiagResponse:
    def __init__(self, url, status, headers):
        self.url = url
        self.status = status
        self._headers = headers

    async def all_headers(self):
        return self._headers


@run_async
async def test_capture_context_diagnostics_reports_webdriver_and_cf_cookies():
    page = _FakeDiagPage(webdriver=False, user_agent="Mozilla/5.0 test")
    context = _FakeDiagContext([
        {"name": "cf_clearance", "value": "secret-token-value"},
        {"name": "session_id", "value": "abc123"},
    ])

    diag = await mod.capture_context_diagnostics(context, page)

    assert diag["navigator_webdriver"] is False
    assert diag["runtime_user_agent"] == "Mozilla/5.0 test"
    assert diag["route_interception_active"] is False
    assert "page.evaluate" in diag["fetch_execution_context"]
    assert diag["cloudflare_cookies_present_before_request"] == ["cf_clearance"]
    assert set(diag["all_cookie_names_in_context"]) == {"cf_clearance", "session_id"}
    # The cookie VALUE must never appear in the diagnostics dict at all -
    # only names.
    assert "secret-token-value" not in str(diag)


@run_async
async def test_network_diagnostics_logs_cf_headers_and_cookie_presence_without_leaking_values(caplog):
    page = _FakeDiagPage()
    pending = mod._attach_network_diagnostics(page, label="TESTDIAG")

    request = _FakeDiagRequest(
        "https://www.fut.gg/api/fut/price-access/sign/",
        "POST",
        {
            "content-type": "application/json",
            "cookie": "cf_clearance=super-secret-value-should-not-leak; session=xyz",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 test",
        },
    )
    response = _FakeDiagResponse(
        "https://www.fut.gg/api/fut/player-prices/26/84131117/?verify=tok",
        403,
        {
            "content-type": "text/html; charset=UTF-8",
            "cf-ray": "8a1b2c3d4e5f-LHR",
            "cf-cache-status": "DYNAMIC",
            "server": "cloudflare",
        },
    )

    import logging
    with caplog.at_level(logging.INFO, logger="futgg_sales_sync"):
        page.fire_request(request)
        page.fire_response(response)
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)

    text = "\n".join(r.message for r in caplog.records)
    assert "super-secret-value-should-not-leak" not in text
    assert "TESTDIAG REQUEST" in text
    assert "hasCookieHeader=True" in text
    assert "TESTDIAG RESPONSE" in text
    assert "status=403" in text
    assert "'cf-ray': '8a1b2c3d4e5f-LHR'" in text or "cf-ray" in text
    assert "cloudflare" in text.lower()


@run_async
async def test_network_diagnostics_detects_missing_cookie_header(caplog):
    page = _FakeDiagPage()
    pending = mod._attach_network_diagnostics(page, label="TESTDIAG2")
    request = _FakeDiagRequest(
        "https://www.fut.gg/api/fut/price-access/sign/", "POST",
        {"content-type": "application/json"},  # no cookie header at all
    )

    with caplog.at_level(logging.INFO, logger="futgg_sales_sync"):
        page.fire_request(request)
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)

    text = "\n".join(r.message for r in caplog.records)
    assert "hasCookieHeader=False" in text


# ---------------------------------------------------------------------
# page_capture transport: passive response capture via real player-page
# navigation (no injected fetch(), no manual sign call - item 3/4/8).
# ---------------------------------------------------------------------


class _FakeCaptureResponse:
    def __init__(self, url, status=200, headers=None, json_body=None, raw_body=b""):
        self.url = url
        self.status = status
        self._headers = headers or {}
        self._body = json.dumps(json_body).encode() if json_body is not None else raw_body

    async def all_headers(self):
        return self._headers

    async def body(self):
        return self._body


class _FakeCapturePage:
    """Minimal Playwright Page double: on()/remove_listener() for the
    "response" event, goto() that optionally fires queued responses (as
    a real navigation's hydration would), wait_for_timeout() as a no-op
    async sleep. Reused across multiple fetch_via_page_capture() calls in
    a single test so listener leakage across cards is directly
    observable."""

    def __init__(self):
        self._listeners: dict[str, list] = {}
        self.goto_calls: list[str] = []
        self.goto_exception: Exception | None = None
        self.responses_to_fire: list = []
        self.nav_response = None  # what page.goto() itself returns

    async def goto(self, url, wait_until="domcontentloaded", timeout=None):
        self.goto_calls.append(url)
        if self.goto_exception is not None:
            raise self.goto_exception
        for response in self.responses_to_fire:
            for cb in list(self._listeners.get("response", [])):
                cb(response)
        return self.nav_response

    def on(self, event, cb):
        self._listeners.setdefault(event, []).append(cb)

    def remove_listener(self, event, cb):
        listeners = self._listeners.get(event, [])
        if cb in listeners:
            listeners.remove(cb)

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)

    def listener_count(self, event="response") -> int:
        return len(self._listeners.get(event, []))


def _player_prices_response(card_id, *, completed=None, current_price=250, status=200,
                             cf_mitigated=None, game_year="26"):
    headers = {"content-type": "application/json"}
    if cf_mitigated:
        headers["cf-mitigated"] = cf_mitigated
    return _FakeCaptureResponse(
        f"https://www.fut.gg/api/fut/player-prices/{game_year}/{card_id}/?platform=ps5",
        status=status,
        headers=headers,
        json_body={"data": {"completedAuctions": completed or [], "currentPrice": current_price}},
    )


@run_async
async def test_page_capture_captures_the_correct_target_response(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.responses_to_fire = [
        _player_prices_response(84131117, completed=[{"soldPrice": 1000}], current_price=1200),
    ]

    result = await mod.fetch_via_page_capture(
        page, 84131117, "https://www.fut.gg/players/84131117-eric-garcia/26-84131117/",
    )

    assert result["eaId"] == 84131117
    assert result["completedAuctions"] == [{"soldPrice": 1000}]
    assert result["currentPrice"] == 1200
    assert "error" not in result
    assert page.goto_calls == ["https://www.fut.gg/players/84131117-eric-garcia/26-84131117/"]


@run_async
async def test_page_capture_ignores_a_response_for_an_unrelated_player(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    # A different card's response fires during the same navigation (e.g. a
    # sidebar "similar players" widget) - must never be mistaken for the
    # requested card's own data.
    page.responses_to_fire = [_player_prices_response(999999, completed=[{"soldPrice": 1}])]

    result = await mod.fetch_via_page_capture(page, 84131117, "https://www.fut.gg/players/x/")

    assert result["eaId"] == 84131117
    assert result["error"] == "page_capture_timeout"


@run_async
async def test_page_capture_removes_the_listener_between_cards(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()

    page.responses_to_fire = [_player_prices_response(111)]
    await mod.fetch_via_page_capture(page, 111, "https://www.fut.gg/players/a/")
    assert page.listener_count() == 0, "listener must be removed after the first card"

    # A second card's response must never be readable by anything left
    # over from the first card - and the first card's response, if it
    # somehow fired again, must not leak into the second card's result.
    page.responses_to_fire = [_player_prices_response(222, completed=[{"soldPrice": 5}])]
    result = await mod.fetch_via_page_capture(page, 222, "https://www.fut.gg/players/b/")
    assert page.listener_count() == 0
    assert result["eaId"] == 222
    assert result["completedAuctions"] == [{"soldPrice": 5}]


@run_async
async def test_page_capture_zero_sales_response_is_a_success_not_a_failure(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.responses_to_fire = [_player_prices_response(333, completed=[])]

    result = await mod.fetch_via_page_capture(page, 333, "https://www.fut.gg/players/c/")

    assert "error" not in result
    assert result["completedAuctions"] == []


@run_async
async def test_page_capture_timeout_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.responses_to_fire = []  # nothing ever fires - simulates a hang

    result = await mod.fetch_via_page_capture(page, 444, "https://www.fut.gg/players/d/")

    assert result["error"] == "page_capture_timeout"


@run_async
async def test_page_capture_403_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.responses_to_fire = [_player_prices_response(555, status=403)]

    result = await mod.fetch_via_page_capture(page, 555, "https://www.fut.gg/players/e/")

    assert result["error"] == "page_capture_403"


@run_async
async def test_page_capture_cloudflare_challenge_is_reported_distinctly(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.responses_to_fire = [_player_prices_response(666, status=403, cf_mitigated="challenge")]

    result = await mod.fetch_via_page_capture(page, 666, "https://www.fut.gg/players/f/")

    assert result["error"] == "page_capture_challenge"


@run_async
async def test_page_capture_navigation_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.goto_exception = TimeoutError("navigation timed out")

    result = await mod.fetch_via_page_capture(page, 777, "https://www.fut.gg/players/g/")

    assert result["eaId"] == 777
    assert result["error"].startswith("navigation_failed:")


@run_async
async def test_page_capture_distinguishes_nav_level_challenge_from_api_level(monkeypatch):
    """2026-08-04 finding: a page_capture_timeout could mean either "the
    API subrequest never fired for some unrelated reason" or "the player
    page navigation itself was Cloudflare's interstitial, so the real app
    never ran to make its own request at all" - these need to be
    distinguishable, not both collapsed into the same opaque timeout."""
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.nav_response = _FakeCaptureResponse(
        "https://www.fut.gg/players/x/", status=403,
        headers={"cf-mitigated": "challenge", "content-type": "text/html; charset=UTF-8"},
    )
    page.responses_to_fire = []  # the real app never loaded, so no API subrequest ever fires

    result = await mod.fetch_via_page_capture(page, 888, "https://www.fut.gg/players/x/")

    assert result["error"] == "page_capture_nav_challenge"
    assert result["navStatus"] == 403
    assert result["navCfMitigated"] == "challenge"


@run_async
async def test_page_capture_reports_nav_status_even_when_nav_itself_succeeds(monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    page = _FakeCapturePage()
    page.nav_response = _FakeCaptureResponse("https://www.fut.gg/players/y/", status=200)
    page.responses_to_fire = []  # page loaded fine, but no API subrequest ever fired regardless

    result = await mod.fetch_via_page_capture(page, 999, "https://www.fut.gg/players/y/")

    assert result["error"] == "page_capture_timeout"
    assert result["navStatus"] == 200
    assert result["navCfMitigated"] is None


@run_async
async def test_page_capture_failure_never_advances_a_never_read_card(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    await _seed_players(dsn, [_row(84131117, next_sales_due_at=None)])
    await _set_source_url(dsn, 84131117, "https://www.fut.gg/players/x/")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            "SELECT source_card_id, rating, next_sales_due_at, source_url FROM futgg_players"
        )
        page = _FakeCapturePage()
        page.responses_to_fire = [_player_prices_response(84131117, status=403, cf_mitigated="challenge")]
        stats = _Stats()
        await mod.process_batch_page_capture(
            [page], pool, rows, stats, mod.StageTimers(),
        )
        after = await _fetch_player(dsn, 84131117)
        # A Cloudflare challenge must not count as a read. Read history
        # stays NULL; the card gets a bounded retry rather than being left
        # permanently due, which under the old encoding meant it was
        # re-challenged on every cycle indefinitely.
        assert after["sales_first_read_at"] is None
        assert after["next_sales_due_at"] is not None
        assert stats["failed"] == 1
        assert stats["ok"] == 0
    finally:
        await pool.close()


@run_async
async def test_page_capture_success_advances_the_card_and_writes_sales(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    await _seed_players(dsn, [_row(84131117, next_sales_due_at=None)])
    await _set_source_url(dsn, 84131117, "https://www.fut.gg/players/84131117-eric-garcia/26-84131117/")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            "SELECT source_card_id, rating, next_sales_due_at, source_url FROM futgg_players"
        )
        page = _FakeCapturePage()
        page.responses_to_fire = [_player_prices_response(84131117, completed=[{"soldPrice": 900}])]
        stats = _Stats()
        await mod.process_batch_page_capture([page], pool, rows, stats, mod.StageTimers())
        after = await _fetch_player(dsn, 84131117)
        assert after["next_sales_due_at"] is not None
        assert after["last_sales_status"] == "success"
        assert stats["ok"] == 1
    finally:
        await pool.close()


@run_async
async def test_page_capture_missing_source_url_counts_as_failure(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    await _seed_players(dsn, [_row(84131117, next_sales_due_at=None)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            "SELECT source_card_id, rating, next_sales_due_at, source_url FROM futgg_players"
        )
        assert rows[0]["source_url"] is None
        page = _FakeCapturePage()
        stats = _Stats()
        await mod.process_batch_page_capture([page], pool, rows, stats, mod.StageTimers())
        after = await _fetch_player(dsn, 84131117)
        # Read history untouched; a bounded retry scheduled rather than
        # the card being left permanently due (the retry-loop fix).
        assert after["sales_first_read_at"] is None
        assert after["next_sales_due_at"] is not None
        assert stats["failed"] == 1
        assert page.goto_calls == []
    finally:
        await pool.close()


@run_async
async def test_page_capture_worker_pool_stops_promptly_on_shutdown(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", False)
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    await _seed_players(dsn, [
        _row(1, next_sales_due_at=None), _row(2, next_sales_due_at=None), _row(3, next_sales_due_at=None),
    ])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            "SELECT source_card_id, rating, next_sales_due_at, source_url FROM futgg_players"
        )
        page = _FakeCapturePage()
        shutdown_event = asyncio.Event()
        shutdown_event.set()  # already shutting down before the worker ever starts a card
        stats = _Stats()
        await asyncio.wait_for(
            mod.process_batch_page_capture(
                [page], pool, rows, stats, mod.StageTimers(), shutdown_event=shutdown_event,
            ),
            timeout=5,
        )
        # Nothing was attempted - the worker must check the shutdown event
        # before pulling a card, not mid-navigation-and-crash.
        assert page.goto_calls == []
        assert stats["ok"] == 0
        assert stats["failed"] == 0
    finally:
        await pool.close()


class TestTransportAutoFallback:
    """Auto-mode transport switching (item 4): switches from signed_fetch
    to page_capture on a Cloudflare-challenge-shaped failure run, does not
    thrash back on every cycle, and only reprobes signed_fetch after the
    configured cooldown."""

    def setup_method(self):
        # Each test starts from a clean slate regardless of import-time
        # state or a previous test's mutation of these globals.
        mod._active_transport = "signed_fetch"
        mod._next_signed_reprobe_at = None

    def teardown_method(self):
        mod._active_transport = "signed_fetch"
        mod._next_signed_reprobe_at = None

    def test_mostly_challenge_failures_true_when_all_challenge_shaped(self):
        stats = {"failed": 4, "errors": {"page_capture_challenge": 3, "page_capture_403": 1}}
        assert mod._mostly_challenge_failures(stats) is True

    def test_mostly_challenge_failures_false_for_unrelated_bug(self):
        stats = {"failed": 4, "errors": {"malformed_payload": 4}}
        assert mod._mostly_challenge_failures(stats) is False

    def test_mostly_challenge_failures_false_when_nothing_failed(self):
        assert mod._mostly_challenge_failures({"failed": 0, "errors": {}}) is False

    def test_switch_to_page_capture_sets_reprobe_deadline(self, monkeypatch):
        monkeypatch.setattr(mod, "TRANSPORT_MODE", "auto")
        monkeypatch.setattr(mod, "SIGNED_REPROBE_MINUTES", 60)
        mod.switch_to_page_capture("challenge threshold tripped")
        assert mod.current_transport() == "page_capture"
        assert mod._next_signed_reprobe_at is not None
        assert mod.signed_reprobe_due() is False  # deadline is 60 minutes out, not now

    def test_signed_reprobe_due_false_before_deadline_true_after(self, monkeypatch):
        monkeypatch.setattr(mod, "TRANSPORT_MODE", "auto")
        mod.switch_to_page_capture("challenge threshold tripped")
        assert mod.signed_reprobe_due() is False
        mod._next_signed_reprobe_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert mod.signed_reprobe_due() is True

    def test_signed_reprobe_never_due_outside_auto_mode(self, monkeypatch):
        monkeypatch.setattr(mod, "TRANSPORT_MODE", "page_capture")
        mod._active_transport = "page_capture"
        mod._next_signed_reprobe_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert mod.signed_reprobe_due() is False

    def test_switching_to_the_already_active_transport_does_not_thrash(self, monkeypatch, caplog):
        monkeypatch.setattr(mod, "TRANSPORT_MODE", "auto")
        mod.switch_to_page_capture("first trip")
        with caplog.at_level(logging.WARNING, logger="futgg_sales_sync"):
            caplog.clear()
            mod.switch_to_page_capture("still failing")  # already on page_capture
        # No repeated "TRANSPORT SWITCH" log line for a no-op switch - only
        # a genuine transition should be logged once.
        switch_lines = [r.message for r in caplog.records if "TRANSPORT SWITCH" in r.message]
        assert switch_lines == []

    def test_switch_back_to_signed_fetch_clears_the_reprobe_deadline(self, monkeypatch):
        monkeypatch.setattr(mod, "TRANSPORT_MODE", "auto")
        mod.switch_to_page_capture("trip")
        assert mod._next_signed_reprobe_at is not None
        mod.switch_to_signed_fetch("reprobe succeeded")
        assert mod.current_transport() == "signed_fetch"
        assert mod._next_signed_reprobe_at is None


@run_async
async def test_diagnostic_mode_page_capture_never_writes_when_apply_is_false(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", False)
    monkeypatch.setattr(mod, "TRANSPORT_MODE", "page_capture")
    monkeypatch.setattr(mod, "PAGE_SETTLE_MS", 5)
    await _seed_players(dsn, [{
        "source_card_id": 84131117, "rating": 91, "name": "Eric Garcia",
        "next_sales_due_at": None,
    }])
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE futgg_players SET source_url = $1 WHERE source_card_id = $2",
            "https://www.fut.gg/players/84131117-eric-garcia/26-84131117/", 84131117,
        )
    finally:
        await conn.close()

    class _FakeDiagCapturePage(_FakeCapturePage):
        async def evaluate(self, script, *args):
            if "navigator.webdriver" in script:
                return False
            if "navigator.userAgent" in script:
                return "FakeUA/1.0"
            return None

    class _FakeContext:
        async def cookies(self):
            return []

        async def new_page(self):
            page = _FakeDiagCapturePage()
            page.responses_to_fire = [_player_prices_response(84131117, completed=[{"soldPrice": 700}])]
            return page

    class _FakeBrowser:
        async def new_context(self, **kwargs):
            return _FakeContext()

        async def close(self):
            return None

    class _FakePlaywright:
        class chromium:
            @staticmethod
            async def launch(**kwargs):
                return _FakeBrowser()

    class _FakePlaywrightCM:
        async def __aenter__(self):
            return _FakePlaywright()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "playwright.async_api.async_playwright", lambda: _FakePlaywrightCM(),
    )

    original_dsn = mod.DATABASE_URL
    monkeypatch.setattr(mod, "DATABASE_URL", dsn)
    try:
        ok = await mod.run_diagnostic_card(84131117)
    finally:
        mod.DATABASE_URL = original_dsn

    assert ok is True
    after = await _fetch_player(dsn, 84131117)
    # Diagnostic mode with FUTGG_SALES_APPLY=false must never write -
    # next_sales_due_at stays exactly as seeded (NULL).
    assert after["next_sales_due_at"] is None
