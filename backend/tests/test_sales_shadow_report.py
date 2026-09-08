from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.routers.v2.sales_shadow import _card_note_map, sales_information_shadow


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self, observations, metadata=None):
        self.observations = observations
        self.metadata = metadata or []
        self.fetch_calls = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if "FROM futgg_trigger_shadow_observations" in query:
            return self.observations
        if "FROM futgg_market_snapshot" in query:
            return self.metadata
        raise AssertionError(f"unexpected query: {query}")


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


def _row(
    at,
    *,
    due=(),
    triggered=(),
    cards=(),
    selector="information-shadow-1.2",
    live=False,
):
    return {
        "observed_at": at,
        "due_count": len(due),
        "triggered_count": len(triggered),
        "triggered_new_count": len(triggered),
        "user_driven_count": sum(1 for card in cards if card.get("user_driven")),
        "held_position_count": sum(1 for card in cards if "held_position" in card.get("reasons", [])),
        "due_card_ids": list(due),
        "triggered_card_ids": list(triggered),
        "reason_counts": {},
        "selector_version": selector,
        "notes": {"cards": list(cards), "live_scheduler_changed": live},
    }


def test_card_note_map_handles_json_and_old_rows():
    assert _card_note_map({"notes": {}}) == {}
    mapped = _card_note_map({"notes": json.dumps({"cards": [{"card_id": "42", "reasons": ["watchlist"]}]})})
    assert mapped[42]["reasons"] == ["watchlist"]


def test_report_measures_natural_pickup_repeated_misses_and_pending_events():
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(hours=2)
    held = {"card_id": 2, "reasons": ["held_position"], "user_driven": True}
    watch = {"card_id": 3, "reasons": ["watchlist"], "user_driven": True}
    fresh = {"card_id": 4, "reasons": ["watchlist"], "user_driven": True}

    observations = [
        _row(t0, due=(1,), triggered=(2, 3), cards=(held, watch)),
        _row(t0 + timedelta(minutes=5), due=(2,), triggered=(3,), cards=(watch,)),
        _row(t0 + timedelta(minutes=40), due=(), triggered=(3,), cards=(watch,)),
        _row(now - timedelta(minutes=5), due=(), triggered=(4,), cards=(fresh,)),
    ]
    metadata = [{
        "source_card_id": 3,
        "name": "Repeated Miss",
        "rating": 88,
        "rarity": "Icon",
        "current_bin": 100000,
        "signal": "buy",
        "evaluated_at": now - timedelta(minutes=10),
    }]
    conn = _Conn(observations, metadata)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=_Pool(conn))))

    result = run(sales_information_shadow(
        request,
        hours=24,
        pickup_window_minutes=30,
        _admin={"is_admin": True},
    ))

    pickup = result["pickup_analysis"]
    assert pickup["measured_shadow_card_events"] == 4
    assert pickup["pending_shadow_card_events"] == 1
    assert pickup["picked_up_by_live_scheduler"] == 1
    assert pickup["same_cycle_reallocated"] == 0
    assert pickup["still_missed_after_window"] == 3
    assert pickup["pickup_rate_pct"] == 25.0
    assert pickup["user_driven_events_with_card_detail"] == 4
    assert pickup["user_driven_pending_events"] == 1
    assert pickup["user_driven_picked_up"] == 1

    repeated = pickup["repeated_missed_cards"]
    assert len(repeated) == 1
    assert repeated[0]["card_id"] == 3
    assert repeated[0]["name"] == "Repeated Miss"
    assert repeated[0]["missed_events"] == 3
    assert repeated[0]["pending_events"] == 0
    assert repeated[0]["reasons"] == {"watchlist": 3}


def test_same_cycle_rebalanced_card_counts_as_immediate_pickup():
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(hours=1)
    card = {
        "card_id": 100815667,
        "reasons": ["market_activity"],
        "user_driven": False,
        "rebalanced_live": True,
    }
    observations = [
        _row(
            t0,
            due=(1, 100815667),
            triggered=(100815667,),
            cards=(card,),
            selector="information-live-rebalance-1.0",
            live=True,
        )
    ]
    conn = _Conn(observations)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=_Pool(conn))))

    result = run(sales_information_shadow(
        request,
        hours=24,
        pickup_window_minutes=30,
        _admin={"is_admin": True},
    ))

    pickup = result["pickup_analysis"]
    assert pickup["measured_shadow_card_events"] == 1
    assert pickup["picked_up_by_live_scheduler"] == 1
    assert pickup["same_cycle_reallocated"] == 1
    assert pickup["still_missed_after_window"] == 0
    assert result["live_scheduler_changed"] is True
    assert result["live_rebalance_cycles"] == 1
    assert result["extra_futgg_sales_requests"] == 0


def test_old_observations_still_count_pickup_without_fabricating_reason_detail():
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    old = _row(t0, triggered=(9,), cards=(), selector="information-shadow-1.1")
    old["notes"] = {"live_scheduler_changed": False}
    later = _row(t0 + timedelta(minutes=5), due=(9,))
    conn = _Conn([old, later])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=_Pool(conn))))

    result = run(sales_information_shadow(
        request,
        hours=24,
        pickup_window_minutes=30,
        _admin={"is_admin": True},
    ))

    pickup = result["pickup_analysis"]
    assert pickup["measured_shadow_card_events"] == 1
    assert pickup["pending_shadow_card_events"] == 0
    assert pickup["picked_up_by_live_scheduler"] == 1
    assert pickup["same_cycle_reallocated"] == 0
    assert pickup["user_driven_events_with_card_detail"] == 0
    assert pickup["user_driven_pickup_rate_pct"] is None
