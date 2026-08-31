# tests/test_attach_sbc_reward_watch.py
#
# market_data_provider._attach_sbc_reward_watch() is the merge point
# every FutggMarketDataProvider read path (get_player, get_players_by_ids,
# select_candidates_by_segment, search_players) shares, so the sbc_reward
# guard in futgg_intelligence.py sees real data instead of always
# defaulting to "no active reward". Exercised directly against a fake
# connection - no database, no view.
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.services.market_data_provider import _attach_sbc_reward_watch


def run(coro):
    return asyncio.run(coro)


AS_OF = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, watch_rows, *, explode=False):
        self.watch_rows = watch_rows
        self.explode = explode
        self.fetch_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if self.explode:
            raise RuntimeError("relation \"futgg_sbc_reward_watch\" does not exist")
        (ids,) = args
        return [w for w in self.watch_rows if w["futgg_source_card_id"] in ids]


def _watch(card_id, title="Lamine Yamal", ends_at=None):
    return {
        "futgg_source_card_id": card_id,
        "reward_title": title,
        "sbc_ends_at": ends_at or (AS_OF + timedelta(hours=10)),
    }


class TestAttachSbcRewardWatch:
    def test_matched_rows_are_flagged(self):
        conn = FakeConn([_watch(1)])
        rows = [{"source_card_id": 1}, {"source_card_id": 2}]
        run(_attach_sbc_reward_watch(conn, rows))
        assert rows[0]["sbc_reward_active"] is True
        assert rows[0]["sbc_reward_title"] == "Lamine Yamal"
        assert rows[0]["sbc_reward_ends_at"] is not None
        assert rows[1]["sbc_reward_active"] is False
        assert rows[1]["sbc_reward_title"] is None
        assert rows[1]["sbc_reward_ends_at"] is None

    def test_empty_rows_is_a_no_op(self):
        conn = FakeConn([_watch(1)])
        run(_attach_sbc_reward_watch(conn, []))
        assert conn.fetch_calls == []

    def test_rows_with_no_source_card_id_skip_the_query(self):
        conn = FakeConn([_watch(1)])
        run(_attach_sbc_reward_watch(conn, [{"name": "no id here"}]))
        assert conn.fetch_calls == []

    def test_a_query_failure_degrades_to_unflagged_rather_than_raising(self):
        # A database that has not yet run migration 079 - or any other
        # failure - must not break every read path that shares this
        # helper (players list, search, opportunities, trade finder).
        conn = FakeConn([], explode=True)
        rows = [{"source_card_id": 1}]
        run(_attach_sbc_reward_watch(conn, rows))  # must not raise
        assert "sbc_reward_active" not in rows[0]

    def test_only_the_relevant_ids_are_queried(self):
        conn = FakeConn([_watch(1), _watch(2)])
        rows = [{"source_card_id": 1}]
        run(_attach_sbc_reward_watch(conn, rows))
        _, args = conn.fetch_calls[0]
        assert list(args[0]) == [1]
