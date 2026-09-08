from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_information_shadow as shadow  # noqa: E402
import futgg_sales_triggers as triggers  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, fetch_results=None):
        self.fetch_results = list(fetch_results or [])
        self.fetch_calls = []
        self.execute_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self.fetch_results.pop(0) if self.fetch_results else []

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "OK"


def test_shadow_peeks_do_not_consume_price_events_or_user_requests():
    price_conn = FakeConn([[{"id": 1, "card_id": 10, "previous_price": 1000, "new_price": 1200}]])
    rows = run(shadow._peek_price_change_events(price_conn))
    assert len(rows) == 1
    assert price_conn.execute_calls == []
    assert "consumed_at IS NULL" in price_conn.fetch_calls[0][0]

    request_conn = FakeConn([[{"id": 2, "card_id": 20, "requested_by": "user", "requested_at": None}]])
    rows = run(shadow._peek_priority_requests(request_conn))
    assert len(rows) == 1
    assert request_conn.execute_calls == []
    assert "consumed_at IS NULL" in request_conn.fetch_calls[0][0]


def test_current_recommendation_triggers_use_current_intelligence_and_exact_bin():
    conn = FakeConn([[
        {"source_card_id": 10, "signal": "watch"},
        {"source_card_id": 20, "signal": "buy"},
    ]])
    hits = run(shadow.triggers_from_current_intelligence(conn))

    by_card = {}
    for hit in hits:
        by_card.setdefault(hit.card_id, set()).add(hit.reason)

    assert by_card[10] == {triggers.REASON_LIVE_RECOMMENDATION}
    assert by_card[20] == {
        triggers.REASON_LIVE_RECOMMENDATION,
        triggers.REASON_DISCORD_CANDIDATE,
    }
    sql, args = conn.fetch_calls[0]
    assert "FROM futgg_current_intelligence c" in sql
    assert "futgg_recommendation_snapshots" not in sql
    assert "m.current_bin = c.current_bin" in sql
    assert "c.expires_at IS NULL OR c.expires_at > now()" in sql
    assert "c.status NOT IN ('expired', 'invalidated')" in sql
    assert args == (shadow.CURRENT_INTELLIGENCE_MAX_AGE_MINUTES,)
    assert all(hit.detail["source"] == "current_intelligence" for hit in hits)


def test_held_positions_are_explicitly_futgg_and_user_driven():
    conn = FakeConn([[{"card_id": 82092, "position_count": 2}]])
    hits = run(shadow.triggers_from_held_positions(conn))
    assert len(hits) == 1
    assert hits[0].card_id == 82092
    assert hits[0].reason == shadow.REASON_HELD_POSITION
    assert hits[0].detail["position_count"] == 2
    sql = conn.fetch_calls[0][0]
    assert "status = 'open'" in sql
    assert "card_source = 'futgg'" in sql
    assert shadow.REASON_HELD_POSITION in shadow.USER_DRIVEN_REASONS


def test_user_exposure_beats_background_candidates_inside_same_cap():
    hits = [
        triggers.TriggerHit(100 + i, triggers.REASON_NEW_RELEASE, {})
        for i in range(20)
    ]
    hits += [
        triggers.TriggerHit(1, shadow.REASON_HELD_POSITION, {}),
        triggers.TriggerHit(2, triggers.REASON_WATCHLIST, {}),
    ]
    cards = shadow.combine_information_hits(hits, limit=3)
    assert [card.card_id for card in cards[:2]] == [1, 2]
    assert len(cards) == 3


def test_live_due_cards_are_excluded_from_shadow_candidates():
    hits = [
        triggers.TriggerHit(1, shadow.REASON_HELD_POSITION, {}),
        triggers.TriggerHit(2, triggers.REASON_WATCHLIST, {}),
    ]
    cards = shadow.combine_information_hits(hits, exclude_ids=[1], limit=10)
    assert [card.card_id for card in cards] == [2]


def test_recorded_shadow_explicitly_says_no_live_scheduler_change_or_extra_requests():
    conn = FakeConn()
    selection = shadow.ShadowSelection(
        cards=(
            triggers.TriggeredCard(
                card_id=82092,
                reasons=(shadow.REASON_HELD_POSITION,),
                details={shadow.REASON_HELD_POSITION: {"position_count": 1}},
            ),
        ),
        due_ids=(1, 2, 3),
    )
    run(shadow.record_shadow(conn, selection))
    assert len(conn.execute_calls) == 1
    sql, args = conn.execute_calls[0]
    assert "futgg_trigger_shadow_observations" in sql
    assert args[0] == 3
    assert args[1] == 1
    assert '"live_scheduler_changed": false' in args[-1]
    assert '"extra_sales_requests": 0' in args[-1]
