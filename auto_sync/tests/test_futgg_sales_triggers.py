"""
Tests for futgg_sales_triggers.py - Part 5's triggered detailed-sales
selection.

WHY THIS EXISTS
---------------
select_due() in futgg_sales_sync.py schedules by tier cadence alone. This
module adds a separate, additive selection of specific, falsifiable
reasons a card deserves a detailed sales read right now (a genuine bulk
price move, a crossed profit threshold, a watchlist/alert/user request,
a live recommendation or Discord candidate, a new release, real market
activity, or evidence that has gone stale past what the engine accepts).

No live database here. The DB-touching functions (triggers_from_*,
_fetch_and_consume_*) are tested against a small in-memory FakeConn that
records the SQL/args it was called with and returns canned rows - the
same lightweight-fake convention test_futgg_sales_sync_integrity.py
already uses (_FakePool). combine_trigger_hits() and
triggers_from_price_change_events()/triggers_from_priority_requests()
are pure functions and tested with no fake at all.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_triggers as trig  # noqa: E402

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def run_async(coro):
    import asyncio
    return asyncio.run(coro)


class FakeConn:
    """Records every .fetch()/.execute()/.executemany() call and answers
    .fetch() from a queue of canned row-lists, one per call, in order."""

    def __init__(self, fetch_results: list[list[dict[str, Any]]] | None = None):
        self._fetch_results = list(fetch_results or [])
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list]] = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if not self._fetch_results:
            return []
        return self._fetch_results.pop(0)

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "OK"

    async def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))


def _row(**kwargs):
    """A dict already behaves like an asyncpg.Record for our purposes
    (subscriptable, .get()) - dict(record) is what real code sees after
    that conversion anyway."""
    return kwargs


# ---------------------------------------------------------------------
# Reason/config sanity
# ---------------------------------------------------------------------


class TestReasonConstants:
    def test_user_driven_reasons_are_a_subset_of_all_reasons(self):
        assert trig.USER_DRIVEN_REASONS <= trig.ALL_REASONS

    def test_watchlist_alert_and_user_requested_are_user_driven(self):
        assert trig.USER_DRIVEN_REASONS == {
            trig.REASON_WATCHLIST, trig.REASON_ACTIVE_ALERT, trig.REASON_USER_REQUESTED,
        }

    def test_background_reasons_are_not_user_driven(self):
        background = trig.ALL_REASONS - trig.USER_DRIVEN_REASONS
        assert trig.REASON_BULK_PRICE_CHANGE in background
        assert trig.REASON_PROFIT_OPPORTUNITY in background
        assert trig.REASON_LIVE_RECOMMENDATION in background
        assert trig.REASON_DISCORD_CANDIDATE in background
        assert trig.REASON_NEW_RELEASE in background
        assert trig.REASON_MARKET_ACTIVITY in background
        assert trig.REASON_STALE_EVIDENCE in background


# ---------------------------------------------------------------------
# Trigger #1/#2: bulk price change + profit opportunity (pure classifier)
# ---------------------------------------------------------------------


class TestPriceChangeClassifier:
    def test_a_small_change_below_the_materiality_floor_triggers_nothing(self):
        rows = [_row(card_id=1, previous_price=10000, new_price=10050)]  # 0.5%
        hits = trig.triggers_from_price_change_events(rows)
        assert hits == []

    def test_a_material_change_below_the_profit_floor_triggers_only_price_change(self):
        # 5% move on a cheap card - material, but the delta is small coins.
        rows = [_row(card_id=1, previous_price=1000, new_price=1050)]
        hits = trig.triggers_from_price_change_events(rows)
        reasons = {h.reason for h in hits}
        assert reasons == {trig.REASON_BULK_PRICE_CHANGE}

    def test_a_change_crossing_the_profit_floor_triggers_both_reasons(self):
        rows = [_row(card_id=1, previous_price=10000, new_price=10500)]  # +500, 5%
        hits = trig.triggers_from_price_change_events(rows)
        reasons = {h.reason for h in hits}
        assert reasons == {trig.REASON_BULK_PRICE_CHANGE, trig.REASON_PROFIT_OPPORTUNITY}

    def test_a_first_observation_with_no_previous_price_triggers_nothing(self):
        rows = [_row(card_id=1, previous_price=None, new_price=10000)]
        hits = trig.triggers_from_price_change_events(rows)
        assert hits == []

    def test_multiple_rows_are_all_classified_independently(self):
        rows = [
            _row(card_id=1, previous_price=10000, new_price=10050),   # tiny, nothing
            _row(card_id=2, previous_price=10000, new_price=10500),   # material + profit
        ]
        hits = trig.triggers_from_price_change_events(rows)
        assert {h.card_id for h in hits} == {2}


class TestPriorityRequestClassifier:
    def test_each_row_becomes_a_user_requested_hit(self):
        rows = [_row(card_id=1, requested_by="user-a"), _row(card_id=2, requested_by=None)]
        hits = trig.triggers_from_priority_requests(rows)
        assert [h.reason for h in hits] == [trig.REASON_USER_REQUESTED] * 2
        assert {h.card_id for h in hits} == {1, 2}


# ---------------------------------------------------------------------
# combine_trigger_hits() - the reserved-capacity combinator
# ---------------------------------------------------------------------


class TestCombineTriggerHits:
    def test_a_card_with_multiple_reasons_appears_once_with_all_reasons(self):
        hits = [
            trig.TriggerHit(1, trig.REASON_WATCHLIST, {}),
            trig.TriggerHit(1, trig.REASON_NEW_RELEASE, {}),
        ]
        out = trig.combine_trigger_hits(hits, limit=10)
        assert len(out) == 1
        assert out[0].card_id == 1
        assert out[0].reasons == (trig.REASON_NEW_RELEASE, trig.REASON_WATCHLIST)

    def test_excluded_ids_are_dropped_entirely(self):
        hits = [trig.TriggerHit(1, trig.REASON_WATCHLIST, {}), trig.TriggerHit(2, trig.REASON_WATCHLIST, {})]
        out = trig.combine_trigger_hits(hits, exclude_ids=[1], limit=10)
        assert [c.card_id for c in out] == [2]

    def test_user_driven_candidates_are_never_dropped_for_background_ones(self):
        """The whole point of the reservation: even when background
        reasons vastly outnumber user-driven ones, every user-driven
        candidate within `limit` must survive."""
        background_hits = [
            trig.TriggerHit(i, trig.REASON_NEW_RELEASE, {}) for i in range(100, 200)
        ]
        user_hits = [trig.TriggerHit(i, trig.REASON_WATCHLIST, {}) for i in range(1, 6)]
        out = trig.combine_trigger_hits(background_hits + user_hits, limit=10)
        selected_ids = {c.card_id for c in out}
        assert {1, 2, 3, 4, 5} <= selected_ids

    def test_background_fills_whatever_user_driven_candidates_do_not_use(self):
        user_hits = [trig.TriggerHit(1, trig.REASON_WATCHLIST, {})]
        background_hits = [trig.TriggerHit(i, trig.REASON_NEW_RELEASE, {}) for i in range(2, 12)]
        out = trig.combine_trigger_hits(user_hits + background_hits, limit=5)
        assert len(out) == 5
        assert out[0].card_id == 1  # user-driven candidate always first

    def test_output_is_capped_at_the_limit(self):
        hits = [trig.TriggerHit(i, trig.REASON_NEW_RELEASE, {}) for i in range(50)]
        out = trig.combine_trigger_hits(hits, limit=10)
        assert len(out) == 10

    def test_a_limit_of_zero_selects_nothing(self):
        hits = [trig.TriggerHit(1, trig.REASON_WATCHLIST, {})]
        out = trig.combine_trigger_hits(hits, limit=0)
        assert out == []

    def test_is_user_driven_property_reflects_the_reasons_present(self):
        hits = [trig.TriggerHit(1, trig.REASON_NEW_RELEASE, {}), trig.TriggerHit(2, trig.REASON_ACTIVE_ALERT, {})]
        out = {c.card_id: c for c in trig.combine_trigger_hits(hits, limit=10)}
        assert out[1].is_user_driven is False
        assert out[2].is_user_driven is True


class TestReservedUserCapacity:
    def test_it_is_a_fraction_of_the_limit_rounded_up(self):
        assert trig.reserved_user_capacity(limit=40) == 16  # 40 * 0.4, ceil

    def test_zero_limit_reserves_nothing(self):
        assert trig.reserved_user_capacity(limit=0) == 0


# ---------------------------------------------------------------------
# DB-touching trigger sources (FakeConn)
# ---------------------------------------------------------------------


class TestBulkPriceChangeSource:
    def test_none_conn_returns_no_rows_and_touches_nothing(self):
        assert run_async(trig._fetch_and_consume_price_change_events(None)) == []

    def test_fetched_rows_are_marked_consumed(self):
        conn = FakeConn(fetch_results=[[
            {"id": 1, "card_id": 10, "previous_price": 1000, "new_price": 1200},
        ]])
        rows = run_async(trig._fetch_and_consume_price_change_events(conn))
        assert len(rows) == 1
        assert len(conn.execute_calls) == 1
        sql, args = conn.execute_calls[0]
        assert "consumed_at = now()" in sql
        assert args[0] == [1]

    def test_no_rows_means_no_consume_update_is_issued(self):
        conn = FakeConn(fetch_results=[[]])
        run_async(trig._fetch_and_consume_price_change_events(conn))
        assert conn.execute_calls == []


class TestWatchlistSource:
    def test_none_conn_returns_empty(self):
        assert run_async(trig.triggers_from_watchlist(None)) == []

    def test_rows_become_watchlist_hits(self):
        conn = FakeConn(fetch_results=[[{"card_id": 5, "watcher_count": 3}]])
        hits = run_async(trig.triggers_from_watchlist(conn))
        assert len(hits) == 1
        assert hits[0].reason == trig.REASON_WATCHLIST
        assert hits[0].card_id == 5
        assert hits[0].detail["watcher_count"] == 3


class TestActiveAlertSource:
    def test_none_conn_returns_empty_without_querying(self):
        assert run_async(trig.triggers_from_active_alerts(None)) == []

    def test_rows_become_active_alert_hits(self):
        conn = FakeConn(fetch_results=[[{"card_id": 7}]])
        hits = run_async(trig.triggers_from_active_alerts(conn))
        assert [h.reason for h in hits] == [trig.REASON_ACTIVE_ALERT]
        assert hits[0].card_id == 7

    def test_a_query_failure_degrades_to_no_hits_rather_than_raising(self):
        class ExplodingConn:
            async def fetch(self, *a, **kw):
                raise RuntimeError("connection to watchlist db lost")

        hits = run_async(trig.triggers_from_active_alerts(ExplodingConn()))
        assert hits == []


class TestRecommendationSource:
    def test_none_conn_returns_empty(self):
        assert run_async(trig.triggers_from_recommendations(None)) == []

    def test_a_watch_signal_is_a_recommendation_but_not_a_discord_candidate(self):
        conn = FakeConn(fetch_results=[[{"source_card_id": 1, "signal": "watch"}]])
        hits = run_async(trig.triggers_from_recommendations(conn))
        reasons = {h.reason for h in hits}
        assert reasons == {trig.REASON_LIVE_RECOMMENDATION}

    def test_a_buy_signal_is_both_a_recommendation_and_a_discord_candidate(self):
        conn = FakeConn(fetch_results=[[{"source_card_id": 1, "signal": "buy"}]])
        hits = run_async(trig.triggers_from_recommendations(conn))
        reasons = {h.reason for h in hits}
        assert reasons == {trig.REASON_LIVE_RECOMMENDATION, trig.REASON_DISCORD_CANDIDATE}

    def test_a_strong_buy_signal_is_also_a_discord_candidate(self):
        conn = FakeConn(fetch_results=[[{"source_card_id": 1, "signal": "strong_buy"}]])
        hits = run_async(trig.triggers_from_recommendations(conn))
        reasons = {h.reason for h in hits}
        assert trig.REASON_DISCORD_CANDIDATE in reasons


class TestNewReleaseSource:
    def test_none_conn_returns_empty(self):
        assert run_async(trig.triggers_from_new_releases(None)) == []

    def test_rows_become_new_release_hits(self):
        conn = FakeConn(fetch_results=[[{"source_card_id": 9, "released_at": NOW}]])
        hits = run_async(trig.triggers_from_new_releases(conn))
        assert [h.reason for h in hits] == [trig.REASON_NEW_RELEASE]


class TestMarketActivitySource:
    def test_none_conn_returns_empty(self):
        assert run_async(trig.triggers_from_market_activity(None)) == []

    def test_rows_become_market_activity_hits(self):
        conn = FakeConn(fetch_results=[[{"source_card_id": 3, "sales_count": 12}]])
        hits = run_async(trig.triggers_from_market_activity(conn))
        assert hits[0].detail["sales_count"] == 12


class TestStaleEvidenceSource:
    def test_none_conn_returns_empty(self):
        assert run_async(trig.triggers_from_stale_evidence(None)) == []

    def test_rows_become_stale_evidence_hits(self):
        conn = FakeConn(fetch_results=[[{"source_card_id": 4, "last_sales_status_at": NOW - timedelta(days=5)}]])
        hits = run_async(trig.triggers_from_stale_evidence(conn))
        assert [h.reason for h in hits] == [trig.REASON_STALE_EVIDENCE]

    def test_the_threshold_used_is_the_engine_reject_minutes_plus_margin(self):
        conn = FakeConn(fetch_results=[[]])
        run_async(trig.triggers_from_stale_evidence(conn))
        sql, args = conn.fetch_calls[0]
        expected = trig.ENGINE_REJECT_SALES_OLDER_THAN_MINUTES + trig.STALE_EVIDENCE_MARGIN_MINUTES
        assert args[0] == expected


class TestPriorityRequestSource:
    def test_none_conn_returns_empty(self):
        assert run_async(trig._fetch_and_consume_priority_requests(None)) == []

    def test_fetched_rows_are_marked_consumed(self):
        conn = FakeConn(fetch_results=[[{"id": 1, "card_id": 8, "requested_by": "u1", "requested_at": NOW}]])
        rows = run_async(trig._fetch_and_consume_priority_requests(conn))
        assert len(rows) == 1
        assert len(conn.execute_calls) == 1


# ---------------------------------------------------------------------
# select_triggered() end-to-end wiring
# ---------------------------------------------------------------------


class TestSelectTriggeredEndToEnd:
    def test_it_combines_every_source_and_respects_exclude_ids(self):
        # One FakeConn answers every core-pool .fetch() call in the
        # order select_triggered() issues them: price-change events,
        # priority requests, watchlist, recommendations, new releases,
        # market activity, stale evidence.
        conn = FakeConn(fetch_results=[
            [{"id": 1, "card_id": 100, "previous_price": 10000, "new_price": 10500}],  # price changes
            [{"id": 2, "card_id": 200, "requested_by": "u1", "requested_at": NOW}],     # priority requests
            [{"card_id": 300, "watcher_count": 2}],                                     # watchlist
            [{"source_card_id": 400, "signal": "buy"}],                                 # recommendations
            [{"source_card_id": 500, "released_at": NOW}],                              # new releases
            [{"source_card_id": 600, "sales_count": 9}],                                # market activity
            [{"source_card_id": 700, "last_sales_status_at": NOW - timedelta(days=5)}], # stale evidence
        ])
        out = run_async(trig.select_triggered(conn, exclude_ids=[600], limit=40))
        ids = {c.card_id for c in out}
        assert 100 in ids and 200 in ids and 300 in ids and 400 in ids and 500 in ids and 700 in ids
        assert 600 not in ids  # excluded

    def test_watchlist_pool_none_still_completes_the_selection(self):
        conn = FakeConn(fetch_results=[[], [], [], [], [], [], []])
        out = run_async(trig.select_triggered(conn, watchlist_conn=None, limit=40))
        assert out == []


class TestMirroredConstants:
    """MEANINGFUL_PROFIT_ABS_FLOOR/MEANINGFUL_PROFIT_PCT_OF_PRICE are
    duplicated from backend/app/routers/v2/futgg_market.py (this worker
    deploys separately and cannot import app.routers) - same convention
    ENGINE_REJECT_SALES_OLDER_THAN_MINUTES already uses, one module over,
    kept honest the same way: a drift check, not a docstring promise."""

    def test_the_profit_floor_constants_match_the_backend_ones(self):
        ROOT = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(ROOT / "backend"))
        from app.routers.v2.futgg_market import (
            MEANINGFUL_PROFIT_ABS_FLOOR,
            MEANINGFUL_PROFIT_PCT_OF_PRICE,
        )

        assert trig.MEANINGFUL_PROFIT_ABS_FLOOR == MEANINGFUL_PROFIT_ABS_FLOOR, (
            "futgg_sales_triggers's mirror of MEANINGFUL_PROFIT_ABS_FLOOR has "
            "drifted from futgg_market.py - update both."
        )
        assert trig.MEANINGFUL_PROFIT_PCT_OF_PRICE == MEANINGFUL_PROFIT_PCT_OF_PRICE, (
            "futgg_sales_triggers's mirror of MEANINGFUL_PROFIT_PCT_OF_PRICE has "
            "drifted from futgg_market.py - update both."
        )
