# tests/test_alerts_router.py
#
# The endpoints behind "delete this alert and have it stay deleted".
# Driven directly with fake pools rather than a live database, matching
# tests/test_watchlist_router.py's approach (pytest-asyncio is not
# installed in this test env).
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.routers.v2 import alerts as alerts_router
from app.services import alerts_feed

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, rows, counts=None):
        self.rows = rows
        self.counts = counts or {}
        self.executed = []

    async def fetch(self, query, *args):
        return [dict(r) for r in self.rows]

    async def fetchval(self, query, *args):
        q = " ".join(query.split())
        if "FROM alerts_log WHERE id" in q:
            wanted = args[0]
            return 1 if any(r["id"] == wanted for r in self.rows) else None
        if "COUNT(*) FROM alerts_log" in q:
            return self.counts.get("unread", 0)
        if "COUNT(*) FROM watchlist" in q:
            return self.counts.get("watched", 0)
        return None

    async def execute(self, query, *args):
        self.executed.append((" ".join(query.split()), args))
        return "UPDATE 1"


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class FakeRequest:
    def __init__(self, user_id="u1"):
        self.session = {"user_id": user_id} if user_id else {}


def _row(**overrides):
    base = {
        "id": 1, "card_id": 42, "platform": "ps", "direction": "fall",
        "pct": -13.0, "price": 87_000, "ref_price": 100_000,
        "sent_at": NOW - timedelta(minutes=5), "read_at": None,
        "source": "futgg", "metric": "price",
    }
    base.update(overrides)
    return base


def _install(monkeypatch, conn):
    pool = FakePool(conn)

    async def _pool():
        return pool

    async def _none():
        return None

    monkeypatch.setattr(alerts_router, "get_watchlist_pool", _pool)
    monkeypatch.setattr(alerts_router, "get_core_pool", _none)
    monkeypatch.setattr(alerts_router, "get_player_pool", _none)
    monkeypatch.setattr(alerts_feed, "_resolve_cards", lambda *a, **k: _resolved())
    return pool


async def _resolved():
    return {42: {"name": "Mbappé", "rating": 91, "version": "icon", "image_url": None}}


def test_signed_out_request_is_401_not_an_empty_feed():
    # An empty list would render as "you have no alerts", which is a
    # different and wrong statement.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(alerts_router.list_alerts(FakeRequest(user_id=None)))
    assert exc.value.status_code == 401


def test_feed_resolves_the_player_and_reports_a_real_unread_count(monkeypatch):
    conn = FakeConn([_row()], counts={"unread": 4, "watched": 9})
    _install(monkeypatch, conn)
    result = asyncio.run(alerts_router.list_alerts(FakeRequest(), limit=20))
    assert result["items"][0]["title"].startswith("Mbappé (91 Icon) is down 13.0%")
    # 4, not len(items) == 1: the list is capped and repeat-collapsed, so
    # its length was never a truthful badge number.
    assert result["unreadCount"] == 4
    assert result["watchedCount"] == 9


def test_dismiss_marks_dismissed_and_read(monkeypatch):
    conn = FakeConn([_row()])
    _install(monkeypatch, conn)
    result = asyncio.run(alerts_router.dismiss_alert(1, FakeRequest()))
    assert result["ok"] is True
    statement = conn.executed[0][0]
    assert "dismissed_at = COALESCE(dismissed_at, now())" in statement
    assert "read_at = COALESCE(read_at, now())" in statement


def test_dismissing_twice_succeeds(monkeypatch):
    # Idempotent on purpose: a double-tap, or two tabs racing, must not
    # produce an error toast for something that did happen.
    conn = FakeConn([_row()])
    _install(monkeypatch, conn)
    assert asyncio.run(alerts_router.dismiss_alert(1, FakeRequest()))["ok"] is True
    assert asyncio.run(alerts_router.dismiss_alert(1, FakeRequest()))["ok"] is True


def test_cannot_dismiss_someone_elses_alert(monkeypatch):
    conn = FakeConn([])  # ownership check finds nothing
    _install(monkeypatch, conn)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(alerts_router.dismiss_alert(999, FakeRequest()))
    assert exc.value.status_code == 404
    assert conn.executed == []  # and nothing was written


def test_mark_read_does_not_dismiss(monkeypatch):
    # Seeing an alert is not the same action as removing it: the badge
    # clears, the feed does not.
    conn = FakeConn([_row()])
    _install(monkeypatch, conn)
    asyncio.run(alerts_router.mark_alerts_read(FakeRequest()))
    statement = conn.executed[0][0]
    assert "SET read_at = now()" in statement
    assert "dismissed_at =" not in statement


def test_dismiss_all_only_touches_live_alerts(monkeypatch):
    conn = FakeConn([_row()])
    _install(monkeypatch, conn)
    asyncio.run(alerts_router.dismiss_all_alerts(FakeRequest()))
    statement = conn.executed[0][0]
    assert "dismissed_at IS NULL" in statement


def test_feed_query_excludes_dismissed_rows():
    # The single most important line: a dismissed alert must not come
    # back on the next load, which is what "it didn't stay deleted" was.
    assert "dismissed_at IS NULL" in " ".join(alerts_feed._SELECT_LIVE_ALERTS.split())


def test_feed_query_does_not_join_the_rules_table():
    """A card can carry BOTH a price rule and a liquidity rule (migration
    055's unique index is on (user_id, card_id, platform, metric)), so
    joining alerts_log to watchlist_alerts to recover the metric returned
    every notification twice - once labelled with the wrong measurement.
    The metric is recorded on the alert instead (migration 072)."""
    query = " ".join(alerts_feed._SELECT_LIVE_ALERTS.split())
    assert "watchlist_alerts" not in query
    assert "JOIN" not in query.upper()
    assert "metric" in query


def test_feed_labels_a_liquidity_alert_as_sales_activity(monkeypatch):
    conn = FakeConn([_row(metric="liquidity", direction="fall", price=1.2, ref_price=None)])
    _install(monkeypatch, conn)
    item = asyncio.run(alerts_router.list_alerts(FakeRequest(), limit=20))["items"][0]
    assert "sales activity" in item["title"]
    assert "coins" not in item["movement"]
