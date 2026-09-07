from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.routers.v2 import market_transitions as mt


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
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_headline_is_a_real_state_transition_not_scan_copy():
    assert mt._headline("watch", "buy") == "WATCH → BUY"
    assert mt._headline("strong_buy", "avoid") == "STRONG BUY → AVOID"


def test_status_only_change_does_not_render_buy_to_buy():
    assert mt._headline("buy", "buy", "active", "expired") == "BUY · ACTIVE → EXPIRED"


def test_transition_endpoint_serialises_card_and_live_price():
    changed = datetime(2026, 9, 7, 20, 30, tzinfo=timezone.utc)
    conn = _Conn([
        {
            "source_card_id": 82092,
            "changed_at": changed,
            "previous_signal": "watch",
            "new_signal": "buy",
            "previous_status": "watch",
            "new_status": "active",
            "evaluated_bin": 77_000,
            "fair_value": 82_000,
            "expected_roi": 0.065,
            "confidence_score": 0.84,
            "risk_level": "low",
            "trend_state": "stable",
            "reason_codes": ["below_fair_value"],
            "blocking_codes": [],
            "name": "Mario Kempes",
            "rating": 88,
            "rarity": "Icon",
            "primary_position": "ST",
            "live_current_bin": 77_000,
            "player_image_url": "cutout.png",
            "card_image_stored_url": "stored.png",
            "card_image_url": "origin.png",
        }
    ])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=_Pool(conn))))

    result = run(mt.recent_market_changes(request, hours=24, limit=30, _ent={"tier": "elite"}))

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert conn.args == (24, 30)
    item = result["items"][0]
    assert item["headline"] == "WATCH → BUY"
    assert item["name"] == "Mario Kempes"
    assert item["evaluated_bin"] == 77_000
    assert item["live_current_bin"] == 77_000
    assert item["fair_value"] == 82_000
    assert item["expected_roi"] == 0.065
    assert item["composited_image_url"] == "stored.png"
    assert item["changed_at"] == changed.isoformat()


def test_missing_pool_degrades_cleanly():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    result = run(mt.recent_market_changes(request, hours=24, limit=30, _ent={}))
    assert result == {"items": [], "count": 0, "status": "unavailable"}
