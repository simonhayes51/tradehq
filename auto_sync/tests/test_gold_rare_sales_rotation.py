from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_information_shadow as shadow  # noqa: E402
import futgg_sales_shadow_worker as worker  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetch_calls = []
        self.execute_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self.rows

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "UPDATE 1"


def test_gold_rare_rotation_is_based_on_collector_check_age():
    checked = datetime.now(timezone.utc) - timedelta(hours=9)
    conn = FakeConn([
        {
            "source_card_id": 27,
            "rating": 89,
            "current_bin": 750_000,
            "rarity": "Gold Rare",
            "last_sales_status_at": checked,
        }
    ])

    hits = run(shadow.triggers_from_gold_rare_rotation(conn))

    assert len(hits) == 1
    assert hits[0].reason == shadow.REASON_GOLD_RARE_ROTATION
    assert hits[0].card_id == 27
    assert conn.fetch_calls[0][1] == (shadow.GOLD_RARE_ROTATION_AFTER_MINUTES,)
    sql = conn.fetch_calls[0][0].lower()
    assert "gold rare" in sql
    assert "last_sales_status_at" in sql
    assert "latest_sale_at" not in sql


def test_gold_rare_rotation_sits_ahead_of_generic_stale_maintenance():
    gold = type("Card", (), {"reasons": (shadow.REASON_GOLD_RARE_ROTATION,)})()
    stale = type("Card", (), {"reasons": ("stale_evidence_critical",)})()
    assert worker._reason_rank(gold) < worker._reason_rank(stale)


def test_oldest_gold_rare_check_sorts_first_even_when_cheaper():
    now = datetime.now(timezone.utc)
    old = {
        "source_card_id": 1,
        "rating": 84,
        "current_bin": 20_000,
        "last_sales_status_at": now - timedelta(hours=20),
    }
    newer = {
        "source_card_id": 2,
        "rating": 91,
        "current_bin": 1_000_000,
        "last_sales_status_at": now - timedelta(hours=10),
    }
    assert worker._checked_at_sort_value(old) < worker._checked_at_sort_value(newer)


def test_live_queue_consumption_marks_requests_and_price_events_once_selected():
    conn = FakeConn()
    run(worker._consume_live_trigger_queues(conn, [100815667]))

    assert len(conn.execute_calls) == 2
    assert "futgg_sales_priority_requests" in conn.execute_calls[0][0]
    assert "futgg_price_change_events" in conn.execute_calls[1][0]
    assert conn.execute_calls[0][1] == ([100815667],)
    assert conn.execute_calls[1][1] == ([100815667],)
