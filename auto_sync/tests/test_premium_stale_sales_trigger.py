from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_information_shadow as shadow  # noqa: E402
import futgg_sales_shadow_worker as worker  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows


def test_premium_stale_sales_trigger_targets_old_high_value_card():
    latest = datetime.now(timezone.utc)
    conn = FakeConn([
        {
            "source_card_id": 100815667,
            "rating": 98,
            "current_bin": 2_200_000,
            "latest_sale_at": latest,
            "last_sales_status_at": latest,
        }
    ])

    hits = run(shadow.triggers_from_premium_stale_sales(conn))

    assert len(hits) == 1
    assert hits[0].card_id == 100815667
    assert hits[0].reason == shadow.REASON_PREMIUM_STALE_SALES
    assert hits[0].detail["rating"] == 98
    assert hits[0].detail["current_bin"] == 2_200_000
    assert conn.calls[0][1] == (
        shadow.PREMIUM_STALE_MIN_BIN,
        shadow.PREMIUM_STALE_MIN_RATING,
        shadow.PREMIUM_STALE_AFTER_MINUTES,
        shadow.PREMIUM_STALE_CHECK_AFTER_MINUTES,
        shadow.PREMIUM_STALE_QUERY_LIMIT,
    )
    sql = conn.calls[0][0]
    assert "last_sales_status_at" in sql
    assert "LIMIT $5" in sql


def test_premium_stale_sales_reason_is_high_priority_background_work():
    card = type("Card", (), {"reasons": (shadow.REASON_PREMIUM_STALE_SALES,)})()
    assert worker._reason_rank(card) == 1


def test_defaults_catch_bellingham_shape_after_twelve_hours_without_request_loop():
    assert shadow.PREMIUM_STALE_MIN_RATING <= 98
    assert shadow.PREMIUM_STALE_MIN_BIN <= 2_200_000
    assert shadow.PREMIUM_STALE_AFTER_MINUTES == 720
    assert shadow.PREMIUM_STALE_CHECK_AFTER_MINUTES >= worker.LIVE_REBALANCE_MIN_CHECK_AGE_MINUTES
