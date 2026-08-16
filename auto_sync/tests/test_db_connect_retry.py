"""
A transient connection failure must not kill a worker permanently.

WHY THIS EXISTS
---------------
futgg_player_sync died on its very first statement:

    File "/app/futgg_player_sync.py", line 591, in crawl_once
      lock_conn = await asyncpg.connect(DATABASE_URL)
    ...
      do_ssl_upgrade = await pr.on_data
    ConnectionResetError: [Errno 104] Connection reset by peer

The server accepted the TCP connection and closed it during the TLS
handshake - a Postgres restart, a pooler recycling, max_connections
briefly exhausted, a network blip. All transient, none of them a reason
the worker cannot do its job. But the connect was unguarded, so a blip
lasting one second killed a worker that then stayed dead.

Same shape as SalesSync's ensure_schema crash: a momentary condition made
permanent because the code read "not right now" as "never".

The other half matters just as much - a wrong password must NOT be
retried. Burying a config error under a minute of retries turns an
instant, obvious failure into a slow, confusing one.
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import db_connect  # noqa: E402


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.new_event_loop().run_until_complete(fn(*a, **k))
    return wrapper


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Real backoff would make this suite take a minute."""
    monkeypatch.setattr(db_connect, "DEFAULT_BASE_DELAY", 0.001)
    monkeypatch.setattr(db_connect, "DEFAULT_MAX_DELAY", 0.002)


class _Flaky:
    """Fails with `exc` the first `failures` times, then succeeds."""

    def __init__(self, failures, exc):
        self.calls = 0
        self._failures = failures
        self._exc = exc

    async def __call__(self, *a, **k):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._exc
        return "connection"


class TestTransientFailures:
    @sync
    async def test_the_exact_production_error_is_retried(self, monkeypatch=None):
        """ConnectionResetError(104) during the TLS handshake - what
        actually killed futgg_player_sync."""
        flaky = _Flaky(2, ConnectionResetError(104, "Connection reset by peer"))
        result = await db_connect._with_retry(
            "connect", flaky, attempts=5, base_delay=0.001, max_delay=0.002
        )
        assert result == "connection"
        assert flaky.calls == 3

    @sync
    async def test_a_server_still_starting_up_is_retried(self):
        flaky = _Flaky(1, asyncpg.exceptions.CannotConnectNowError("starting up"))
        assert await db_connect._with_retry(
            "connect", flaky, attempts=3, base_delay=0.001, max_delay=0.002
        ) == "connection"

    @sync
    async def test_connection_exhaustion_is_retried(self):
        """max_connections is transient by nature - something will
        disconnect."""
        flaky = _Flaky(1, asyncpg.exceptions.TooManyConnectionsError("too many"))
        assert await db_connect._with_retry(
            "connect", flaky, attempts=3, base_delay=0.001, max_delay=0.002
        ) == "connection"

    @sync
    async def test_it_gives_up_and_raises_the_real_error(self):
        """Retrying forever would hide a genuinely down database. The
        error that surfaces must be the connection error, not a wrapper."""
        flaky = _Flaky(99, ConnectionResetError(104, "Connection reset by peer"))
        with pytest.raises(ConnectionResetError):
            await db_connect._with_retry(
                "connect", flaky, attempts=3, base_delay=0.001, max_delay=0.002
            )
        assert flaky.calls == 3


class TestPermanentFailures:
    @sync
    async def test_a_bad_password_fails_immediately(self):
        """A config error must surface at once. Retrying it delays a
        clear message behind a minute of noise."""
        flaky = _Flaky(99, asyncpg.exceptions.InvalidPasswordError("nope"))
        with pytest.raises(asyncpg.exceptions.InvalidPasswordError):
            await db_connect._with_retry(
                "connect", flaky, attempts=5, base_delay=0.001, max_delay=0.002
            )
        assert flaky.calls == 1, "a permanent error must not be retried"

    @sync
    async def test_a_missing_database_fails_immediately(self):
        flaky = _Flaky(99, asyncpg.exceptions.InvalidCatalogNameError("no db"))
        with pytest.raises(asyncpg.exceptions.InvalidCatalogNameError):
            await db_connect._with_retry(
                "connect", flaky, attempts=5, base_delay=0.001, max_delay=0.002
            )
        assert flaky.calls == 1


class TestBackoff:
    def test_it_is_jittered(self):
        """Every worker restarts together when the database blips.
        Un-jittered backoff has them retry in lockstep, recreating the
        herd at each step exactly as the server tries to recover."""
        delays = {db_connect._delay_for(2, 2.0, 30.0) for _ in range(50)}
        assert len(delays) > 1

    def test_it_grows_and_is_capped(self):
        assert db_connect._delay_for(0, 2.0, 30.0) < db_connect._delay_for(4, 2.0, 30.0)
        assert all(db_connect._delay_for(20, 2.0, 30.0) <= 30.0 * 1.3 for _ in range(20))
