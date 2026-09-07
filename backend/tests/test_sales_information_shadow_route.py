from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.routers.v2 import sales_shadow


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.args = None

    async def fetch(self, query, *args):
        self.args = args
        return self.rows


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, rows):
        self.conn = _Conn(rows)

    def acquire(self):
        return _Acquire(self.conn)


def _request(rows):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=_Pool(rows))))


def test_shadow_report_aggregates_reason_counts_and_user_share():
    t1 = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 7, 20, 5, tzinfo=timezone.utc)
    rows = [
        {
            "observed_at": t1,
            "due_count": 60,
            "triggered_count": 8,
            "triggered_new_count": 8,
            "user_driven_count": 3,
            "held_position_count": 1,
            "reason_counts": '{"watchlist":2,"held_position":1,"new_release":5}',
            "selector_version": "information-shadow-1.0",
        },
        {
            "observed_at": t2,
            "due_count": 60,
            "triggered_count": 4,
            "triggered_new_count": 4,
            "user_driven_count": 2,
            "held_position_count": 1,
            "reason_counts": {"watchlist": 1, "held_position": 1, "market_activity": 2},
            "selector_version": "information-shadow-1.0",
        },
    ]
    request = _request(rows)
    result = run(sales_shadow.sales_information_shadow(request, hours=24, _admin=True))

    assert result["status"] == "ok"
    assert result["cycles"] == 2
    assert result["live_due_requests_observed"] == 120
    assert result["shadow_priority_events"] == 12
    assert result["user_driven_shadow_events"] == 5
    assert result["held_position_shadow_events"] == 2
    assert result["reason_counts"]["watchlist"] == 3
    assert result["reason_counts"]["held_position"] == 2
    assert result["live_scheduler_changed"] is False
    assert result["extra_futgg_sales_requests"] == 0
    assert request.app.state.db_pool.conn.args == (24,)


def test_empty_shadow_report_is_warming_not_a_false_healthy_zero():
    result = run(sales_shadow.sales_information_shadow(_request([]), hours=24, _admin=True))
    assert result == {
        "status": "warming",
        "hours": 24,
        "cycles": 0,
        "reason": "no_shadow_observations_yet",
    }


def test_json_object_tolerates_asyncpg_string_or_decoded_json():
    assert sales_shadow._json_object('{"watchlist":2}') == {"watchlist": 2}
    assert sales_shadow._json_object({"watchlist": 2}) == {"watchlist": 2}
    assert sales_shadow._json_object("not json") == {}
