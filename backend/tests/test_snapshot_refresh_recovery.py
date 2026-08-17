"""
The snapshot refresher must recover on its own.

WHY THIS EXISTS
---------------
Production, on fully-deployed code:

    newest price_checked_at              : 1.4 min ago
    futgg_market_snapshot last refreshed : 117.6 min ago
    stale_market                    337   67.4%

The price sync was healthy. The snapshot simply stopped advancing, and
nothing noticed or recovered. Because everything reads the snapshot, a
two-hour-old snapshot makes every card at least two hours stale, and
special-tier cards - a 20-minute limit - fail STALE_MARKET en masse.

Three properties were missing, and all three are needed regardless of
WHY a given refresh fails:

  1. The loop gated on the WATERMARK ("has the source data changed"),
     which is the wrong question once the snapshot has stopped
     advancing. The watermark moves every bulk cycle, a refresh is
     attempted, and if that refresh never SUCCEEDS the loop keeps trying
     and keeps not noticing. It must measure the snapshot's own age.
  2. The core pool sets no command_timeout, so a slow refresh runs
     unbounded while holding the advisory lock - which is exactly how a
     snapshot reaches 117 minutes stale with nothing in the logs.
  3. REFRESH ... CONCURRENTLY builds the new snapshot AND diffs it
     against the old. If that cannot finish in time, a plain refresh
     (build and swap) is several times cheaper - measured at 546ms vs
     889ms over 480k rows of bin history, and the gap widens with size.
     A few seconds of lock on the view beats data hours old.
"""
from __future__ import annotations

import asyncio
import functools

import pytest

from app.services import market_data_provider as mdp


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.new_event_loop().run_until_complete(fn(*a, **k))
    return wrapper


class _Conn:
    """Records the SQL it is asked to run; optionally stalls the refresh."""

    def __init__(self, *, refresh_delay=0.0):
        self.executed = []
        self._refresh_delay = refresh_delay

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_lock" in sql:
            return True
        if "pg_advisory_unlock" in sql:
            return True
        return None

    async def execute(self, sql, *args):
        self.executed.append(" ".join(sql.split()))
        if "REFRESH MATERIALIZED VIEW" in sql and self._refresh_delay:
            await asyncio.sleep(self._refresh_delay)
        return "OK"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture(autouse=True)
def _reset():
    """Restore every module-level knob these tests mutate.

    Without this the tests that shorten REFRESH_TIMEOUT_SECONDS to force
    a timeout leak that value into the tests that assert on its real
    configured value - which is exactly the kind of false failure that
    makes a suite untrustworthy."""
    saved = (
        mdp.REFRESH_TIMEOUT_SECONDS,
        mdp.CONCURRENT_FAILURES_BEFORE_PLAIN_REFRESH,
        mdp.REFRESH_FAILURE_BACKOFF_SECONDS,
    )
    mdp._concurrent_refresh_failures = 0
    mdp._last_refresh_failure_at = 0.0
    yield
    (
        mdp.REFRESH_TIMEOUT_SECONDS,
        mdp.CONCURRENT_FAILURES_BEFORE_PLAIN_REFRESH,
        mdp.REFRESH_FAILURE_BACKOFF_SECONDS,
    ) = saved
    mdp._concurrent_refresh_failures = 0
    mdp._last_refresh_failure_at = 0.0


class TestRefreshIsBounded:
    @sync
    async def test_a_refresh_that_overruns_is_cancelled_not_left_running(self, monkeypatch=None):
        """Unbounded is how the snapshot reached 117 minutes stale: the
        refresh holds the advisory lock for its whole duration, so every
        later attempt is silently skipped while it runs."""
        mdp.REFRESH_TIMEOUT_SECONDS = 0.05
        conn = _Conn(refresh_delay=1.0)
        did = await mdp.refresh_snapshot(_Pool(conn))
        assert did is False
        assert mdp._concurrent_refresh_failures == 1

    @sync
    async def test_repeated_timeouts_fall_back_to_a_plain_refresh(self):
        """CONCURRENTLY diffs against the existing view; a plain refresh
        builds and swaps. If the expensive one cannot finish, taking a
        few seconds of lock is strictly better than never refreshing."""
        mdp.REFRESH_TIMEOUT_SECONDS = 0.05
        mdp.CONCURRENT_FAILURES_BEFORE_PLAIN_REFRESH = 2
        slow = _Pool(_Conn(refresh_delay=1.0))
        assert await mdp.refresh_snapshot(slow) is False
        assert await mdp.refresh_snapshot(slow) is False

        mdp.REFRESH_TIMEOUT_SECONDS = 5.0
        fast = _Conn()
        assert await mdp.refresh_snapshot(_Pool(fast)) is True
        refreshes = [q for q in fast.executed if "REFRESH MATERIALIZED VIEW" in q]
        assert refreshes, "no refresh was issued"
        assert "CONCURRENTLY" not in refreshes[0], refreshes[0]

    @sync
    async def test_a_success_returns_to_the_concurrent_path(self):
        """The fallback is a recovery mode, not a permanent downgrade -
        CONCURRENTLY does not block readers and stays preferred."""
        mdp.REFRESH_TIMEOUT_SECONDS = 5.0
        conn = _Conn()
        assert await mdp.refresh_snapshot(_Pool(conn)) is True
        assert mdp._concurrent_refresh_failures == 0
        refreshes = [q for q in conn.executed if "REFRESH MATERIALIZED VIEW" in q]
        assert "CONCURRENTLY" in refreshes[0]


class TestStalenessDrivesTheRefresh:
    def test_the_age_limit_sits_inside_the_tightest_freshness_gate(self):
        """The real constraint, not an arbitrary number.

        Snapshot age is a floor under every card's apparent price age, so
        if the snapshot may be older than the tightest tier gate, the
        snapshot ALONE can fail every card in that tier - which is what
        happened: 117 minutes stale against a 20-minute special-tier
        limit, 337 of 500 cards rejected as stale_market.
        """
        from app.services.futgg_config import ENGINE_CONFIG

        tightest_gate_minutes = min(ENGINE_CONFIG.max_price_age_minutes_by_tier.values())
        assert mdp.SNAPSHOT_MAX_AGE_SECONDS / 60.0 < tightest_gate_minutes, (
            f"snapshot may age to {mdp.SNAPSHOT_MAX_AGE_SECONDS / 60:.0f} min, "
            f"but the tightest tier gate is {tightest_gate_minutes} min - the "
            "snapshot's own staleness would fail every card in that tier"
        )


class _StandaloneConn(_Conn):
    """_Conn plus the close() a real asyncpg.connect() result needs -
    refresh_snapshot(pool, dsn=...) always closes its standalone
    connection in a finally block."""

    async def close(self):
        pass


class TestDsnPathGetsSameProtections:
    """refresh_snapshot(pool, dsn=...) - what main.py actually calls in
    production (see its own docstring for why) - used to reimplement
    only the advisory-lock/REFRESH/unlock skeleton inline, none of the
    timeout/plain-refresh-fallback/failure-tracking below. Every one of
    TestRefreshIsBounded's guarantees must hold on this path too, not
    just on the pool.acquire() path the other tests exercise."""

    @sync
    async def test_a_slow_refresh_is_still_cancelled_on_the_dsn_path(self, monkeypatch):
        mdp.REFRESH_TIMEOUT_SECONDS = 0.05
        conn = _StandaloneConn(refresh_delay=1.0)

        async def fake_connect(dsn):
            return conn

        monkeypatch.setattr(mdp.asyncpg, "connect", fake_connect)
        did = await mdp.refresh_snapshot(_Pool(_Conn()), dsn="postgres://irrelevant")
        assert did is False
        assert mdp._concurrent_refresh_failures == 1
        assert mdp._last_refresh_failure_at > 0.0

    @sync
    async def test_a_successful_dsn_refresh_still_uses_concurrently(self, monkeypatch):
        mdp.REFRESH_TIMEOUT_SECONDS = 5.0
        conn = _StandaloneConn()

        async def fake_connect(dsn):
            return conn

        monkeypatch.setattr(mdp.asyncpg, "connect", fake_connect)
        did = await mdp.refresh_snapshot(_Pool(_Conn()), dsn="postgres://irrelevant")
        assert did is True
        refreshes = [q for q in conn.executed if "REFRESH MATERIALIZED VIEW" in q]
        assert "CONCURRENTLY" in refreshes[0]


class TestFailureBackoff:
    """The regression that took the site down.

    Forcing a refresh on staleness WITHOUT a failure backoff is strictly
    worse than not forcing at all. The refresh runs for its whole timeout,
    is cancelled, leaves the snapshot exactly as stale as before - which
    immediately re-triggers the staleness check - and starts again. A
    permanent, heavy, never-completing query.

    Production, consecutive passes after the forced refresh shipped:

        futgg scan ... in   3.2s
        futgg scan ... in   7.5s
        futgg scan ... in 148.0s
        priority_recompute  9.43s -> 157.13s

    with both CONCURRENTLY and plain refreshes hitting the 300s timeout
    back to back. Nothing on the site responded.
    """

    def test_a_failure_is_timestamped_so_the_loop_can_hold_off(self):
        assert hasattr(mdp, "_last_refresh_failure_at")
        assert hasattr(mdp, "REFRESH_FAILURE_BACKOFF_SECONDS")

    def test_the_backoff_is_long_enough_to_actually_yield(self):
        """It has to exceed the refresh timeout, or the next attempt
        starts before the database has had any quiet time at all."""
        assert mdp.REFRESH_FAILURE_BACKOFF_SECONDS > mdp.REFRESH_TIMEOUT_SECONDS

    def test_the_timeout_is_not_shorter_than_the_work_it_guards(self):
        """300s cancelled a refresh that needed longer, every time. The
        timeout is there to catch a HUNG refresh holding the advisory
        lock, not to police a merely slow one."""
        assert mdp.REFRESH_TIMEOUT_SECONDS >= 600

    @sync
    async def test_a_timeout_records_when_it_happened(self):
        mdp.REFRESH_TIMEOUT_SECONDS = 0.05
        mdp._last_refresh_failure_at = 0.0
        assert await mdp.refresh_snapshot(_Pool(_Conn(refresh_delay=1.0))) is False
        assert mdp._last_refresh_failure_at > 0.0
