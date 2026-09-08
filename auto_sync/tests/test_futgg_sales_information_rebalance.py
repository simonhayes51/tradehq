from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_shadow_worker as worker  # noqa: E402


def _row(card_id, *, never_read=False, failures=0, tier="NORMAL"):
    return {
        "source_card_id": card_id,
        "rating": 90,
        "next_sales_due_at": datetime.now(timezone.utc),
        "source_url": f"https://www.fut.gg/players/{card_id}/",
        "sales_first_read_at": None if never_read else datetime.now(timezone.utc),
        "sales_consecutive_failures": failures,
        "effective_tier": tier,
    }


def test_default_production_cycle_reserves_nine_of_sixty_slots():
    old_enabled = worker.LIVE_REBALANCE_ENABLED
    old_share = worker.LIVE_REBALANCE_SHARE
    old_max = worker.LIVE_REBALANCE_MAX_SLOTS
    try:
        worker.LIVE_REBALANCE_ENABLED = True
        worker.LIVE_REBALANCE_SHARE = 0.15
        worker.LIVE_REBALANCE_MAX_SLOTS = 12
        assert worker.rebalance_slot_count(60) == 9
    finally:
        worker.LIVE_REBALANCE_ENABLED = old_enabled
        worker.LIVE_REBALANCE_SHARE = old_share
        worker.LIVE_REBALANCE_MAX_SLOTS = old_max


def test_rebalance_never_increases_cycle_request_count():
    baseline = [_row(i) for i in range(1, 61)]
    candidates = [_row(1000 + i) for i in range(20)]

    final_due, chosen = worker.merge_rebalanced_rows(
        baseline,
        candidates,
        slots=9,
    )

    assert len(final_due) == 60
    assert len(chosen) == 9
    assert chosen == list(range(1000, 1009))


def test_never_read_and_failure_retry_rows_are_never_displaced():
    baseline = [
        _row(1, never_read=True),
        _row(2, failures=2),
        _row(3),
        _row(4),
        _row(5),
    ]
    candidates = [_row(100), _row(101), _row(102)]

    final_due, chosen = worker.merge_rebalanced_rows(
        baseline,
        candidates,
        slots=3,
    )
    final_ids = [row["source_card_id"] for row in final_due]

    assert len(final_due) == len(baseline)
    assert 1 in final_ids
    assert 2 in final_ids
    assert chosen == [100, 101, 102]


def test_user_and_live_signal_reasons_rank_ahead_of_background_activity():
    watch = SimpleNamespace(reasons=("watchlist",))
    live = SimpleNamespace(reasons=("live_recommendation",))
    market = SimpleNamespace(reasons=("market_activity",))
    stale = SimpleNamespace(reasons=("stale_evidence_critical",))

    assert worker._reason_rank(watch) < worker._reason_rank(live)
    assert worker._reason_rank(live) < worker._reason_rank(market)
    assert worker._reason_rank(market) < worker._reason_rank(stale)
