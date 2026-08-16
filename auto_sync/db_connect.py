# auto_sync/db_connect.py
"""
Connecting to Postgres, with the retry every worker needs and none had.

WHY THIS EXISTS
---------------
futgg_player_sync died on its first statement:

    File "/app/futgg_player_sync.py", line 591, in crawl_once
      lock_conn = await asyncpg.connect(DATABASE_URL)
    ...
      do_ssl_upgrade = await pr.on_data
    ConnectionResetError: [Errno 104] Connection reset by peer

The server accepted the TCP connection and then closed it during the TLS
handshake. That is a transient, connection-level condition - a Postgres
restart, a pooler recycling, max_connections briefly exhausted, a network
blip - and it says nothing about whether the worker can do its job. But
`asyncpg.connect` was the first line of the function, unguarded, so a
blip lasting one second killed a worker that then stayed dead.

This is the same failure shape as SalesSync's ensure_schema crash: a
momentary condition turned into a permanent outage because the code
treated "not right now" as "never". Both deserve a retry, not a
traceback.

WHAT IS NOT RETRIED
-------------------
Anything that will still be true in ten seconds. A wrong password, a
missing database, an unparseable DSN - retrying those just delays a clear
error behind a minute of noise, and hides the real problem in a log full
of attempts. Only genuinely transient transport failures are retried.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import asyncpg

log = logging.getLogger("db_connect")

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 2.0
DEFAULT_MAX_DELAY = 30.0

#: Failures that mean "the connection did not survive", not "you may not
#: connect". ConnectionResetError and friends are all OSError subclasses;
#: asyncpg raises its own for a server that closed mid-protocol.
_TRANSIENT = (
    OSError,                                   # includes ConnectionResetError (104)
    asyncio.TimeoutError,
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.ConnectionFailureError,
    asyncpg.exceptions.CannotConnectNowError,   # server starting up
    asyncpg.exceptions.TooManyConnectionsError,
)

#: Failures that will still be failures after any amount of waiting.
#: Listed explicitly so a config mistake surfaces immediately instead of
#: being buried under retries.
_PERMANENT = (
    asyncpg.exceptions.InvalidPasswordError,
    asyncpg.exceptions.InvalidCatalogNameError,
    asyncpg.exceptions.InvalidAuthorizationSpecificationError,
)


def _delay_for(attempt: int, base: float, ceiling: float) -> float:
    """Exponential backoff with jitter.

    Jittered because every worker restarts at once when the database
    blips, and un-jittered backoff would have them all retry in lockstep -
    recreating the thundering herd at each step just as the server is
    trying to recover.
    """
    raw = min(ceiling, base * (2 ** attempt))
    return raw * random.uniform(0.7, 1.3)


async def _with_retry(what: str, factory, attempts: int, base_delay: float, max_delay: float):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await factory()
        except _PERMANENT:
            raise
        except _TRANSIENT as exc:
            last = exc
            if attempt == attempts - 1:
                break
            delay = _delay_for(attempt, base_delay, max_delay)
            log.warning(
                "%s failed (%s: %s) - attempt %d/%d, retrying in %.1fs",
                what, type(exc).__name__, exc, attempt + 1, attempts, delay,
            )
            await asyncio.sleep(delay)
    log.error(
        "%s failed after %d attempts (%s: %s) - giving up",
        what, attempts, type(last).__name__, last,
    )
    raise last  # type: ignore[misc]


async def connect_with_retry(
    dsn: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    **kwargs: Any,
) -> asyncpg.Connection:
    return await _with_retry(
        "database connect",
        lambda: asyncpg.connect(dsn, **kwargs),
        attempts, base_delay, max_delay,
    )


async def create_pool_with_retry(
    dsn: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    **kwargs: Any,
) -> asyncpg.Pool:
    """Pool creation opens min_size connections eagerly, so it fails in
    exactly the same way a bare connect does - and at worker startup,
    where a failure is equally fatal."""
    return await _with_retry(
        "database pool creation",
        lambda: asyncpg.create_pool(dsn, **kwargs),
        attempts, base_delay, max_delay,
    )
