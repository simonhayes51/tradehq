from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.services import intelligence_readiness as ir
from app.services import readiness as rd


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self, *, current_rows=10, newest=None, player_pool=100, fail=False):
        self.current_rows = current_rows
        self.newest = newest
        self.player_pool = player_pool
        self.fail = fail

    async def fetchrow(self, query, *args):
        if self.fail:
            raise RuntimeError("boom")
        return {
            "current_rows": self.current_rows,
            "newest_evaluated_at": self.newest,
        }

    async def fetchval(self, query, *args):
        if self.fail:
            raise RuntimeError("boom")
        return self.player_pool


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_current_intelligence_is_ok_when_recent():
    conn = _Conn(
        current_rows=75,
        player_pool=100,
        newest=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    result = run(ir._check_intelligence_state(_Pool(conn), rd))
    assert result["status"] == rd.OK
    assert result["detail"]["coverage_pct"] == 75.0
    assert result["detail"]["newest_age_minutes"] < ir.STALE_AFTER_MINUTES


def test_empty_current_state_is_degraded_not_unavailable():
    conn = _Conn(current_rows=0, player_pool=100, newest=None)
    result = run(ir._check_intelligence_state(_Pool(conn), rd))
    assert result["status"] == rd.DEGRADED
    assert result["reason"] == "no_current_state"


def test_stale_scanner_state_is_degraded():
    conn = _Conn(
        current_rows=50,
        player_pool=100,
        newest=datetime.now(timezone.utc) - timedelta(minutes=ir.STALE_AFTER_MINUTES + 5),
    )
    result = run(ir._check_intelligence_state(_Pool(conn), rd))
    assert result["status"] == rd.DEGRADED
    assert result["reason"] == "scanner_state_stale"


def test_unreadable_current_state_is_degraded_and_does_not_raise():
    result = run(ir._check_intelligence_state(_Pool(_Conn(fail=True)), rd))
    assert result["status"] == rd.DEGRADED
    assert result["reason"] == "unreadable"
