"""
Tests for futgg_sales_shadow.py - Part 6's shadow-mode measurement of
the triggered detailed-sales queue.

No live database and no production egress here (both are out of reach of
this sandbox - see the module's own docstring). build_shadow_cycle_
metrics() and build_interval_report() are pure functions tested
directly; record_shadow_cycle()/compute_interval_report() are tested
against a small FakeConn recording the SQL it was sent.
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

import futgg_sales_shadow as shadow  # noqa: E402
import futgg_sales_triggers as trig  # noqa: E402

T0 = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def run_async(coro):
    import asyncio
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, fetch_results=None, fetchval_results=None):
        self._fetch_results = list(fetch_results or [])
        self._fetchval_results = list(fetchval_results or [])
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self._fetch_results.pop(0) if self._fetch_results else []

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, args))
        return self._fetchval_results.pop(0) if self._fetchval_results else 0

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "OK"


def _due_row(card_id, tier="NORMAL"):
    return {"source_card_id": card_id, "effective_tier": tier}


def _triggered(card_id, *reasons):
    return trig.TriggeredCard(card_id=card_id, reasons=tuple(sorted(reasons)), details={})


# ---------------------------------------------------------------------
# build_shadow_cycle_metrics
# ---------------------------------------------------------------------


class TestBuildShadowCycleMetrics:
    def test_due_count_and_tier_breakdown(self):
        due = [_due_row(1, "CRITICAL"), _due_row(2, "CRITICAL"), _due_row(3, "NORMAL")]
        m = shadow.build_shadow_cycle_metrics(
            due_rows=due, triggered_cards=[], stats={}, cycle_started_at=T0, cycle_ended_at=T0,
        )
        assert m.due_count == 3
        assert m.due_count_by_tier == {"CRITICAL": 2, "NORMAL": 1}

    def test_triggered_new_count_excludes_cards_already_due(self):
        due = [_due_row(1)]
        triggered = [_triggered(1, trig.REASON_WATCHLIST), _triggered(2, trig.REASON_NEW_RELEASE)]
        m = shadow.build_shadow_cycle_metrics(
            due_rows=due, triggered_cards=triggered, stats={}, cycle_started_at=T0, cycle_ended_at=T0,
        )
        assert m.triggered_count == 2
        assert m.triggered_new_count == 1  # only card 2 is genuinely new work

    def test_reason_counts_tally_across_multi_reason_cards(self):
        triggered = [
            _triggered(1, trig.REASON_WATCHLIST, trig.REASON_NEW_RELEASE),
            _triggered(2, trig.REASON_WATCHLIST),
        ]
        m = shadow.build_shadow_cycle_metrics(
            due_rows=[], triggered_cards=triggered, stats={}, cycle_started_at=T0, cycle_ended_at=T0,
        )
        assert m.reason_counts == {trig.REASON_WATCHLIST: 2, trig.REASON_NEW_RELEASE: 1}

    def test_successful_and_failed_reads_come_from_stats(self):
        m = shadow.build_shadow_cycle_metrics(
            due_rows=[], triggered_cards=[], stats={"ok": 12, "failed": 3},
            cycle_started_at=T0, cycle_ended_at=T0,
        )
        assert m.successful_reads == 12
        assert m.failed_reads == 3

    def test_rate_limited_count_sums_429_marked_errors_only(self):
        stats = {"errors": {"data_429": 5, "sign_429": 2, "data_403": 1, "malformed_payload": 4}}
        m = shadow.build_shadow_cycle_metrics(
            due_rows=[], triggered_cards=[], stats=stats, cycle_started_at=T0, cycle_ended_at=T0,
        )
        assert m.rate_limited_count == 7  # 5 + 2, not the 403 or malformed_payload

    def test_successful_card_ids_are_carried_through(self):
        m = shadow.build_shadow_cycle_metrics(
            due_rows=[], triggered_cards=[], stats={}, cycle_started_at=T0, cycle_ended_at=T0,
            successful_card_ids=[10, 20, 30],
        )
        assert m.successful_card_ids == (10, 20, 30)


# ---------------------------------------------------------------------
# record_shadow_cycle
# ---------------------------------------------------------------------


class TestRecordShadowCycle:
    def test_it_issues_a_single_insert_with_all_fields(self):
        conn = FakeConn()
        m = shadow.build_shadow_cycle_metrics(
            due_rows=[_due_row(1, "HIGH")],
            triggered_cards=[_triggered(2, trig.REASON_NEW_RELEASE)],
            stats={"ok": 1, "failed": 0}, cycle_started_at=T0, cycle_ended_at=T0 + timedelta(minutes=1),
        )
        run_async(shadow.record_shadow_cycle(conn, m))
        assert len(conn.execute_calls) == 1
        sql, args = conn.execute_calls[0]
        assert "INSERT INTO futgg_sales_shadow_log" in sql
        assert args[2] == 1  # due_count


# ---------------------------------------------------------------------
# build_interval_report (pure aggregator)
# ---------------------------------------------------------------------


class TestBuildIntervalReport:
    def _rows(self):
        return [
            {
                "cycle_started_at": T0, "cycle_ended_at": T0 + timedelta(minutes=5),
                "due_count": 100, "due_count_by_tier": {"CRITICAL": 40, "HIGH": 60},
                "triggered_count": 5, "triggered_new_count": 3,
                "triggered_card_ids": [1, 2, 3, 4, 5],
                "reason_counts": {"watchlist": 2, "new_release": 3},
                "successful_reads": 90, "failed_reads": 10,
                "successful_card_ids": [10, 11, 12],
                "throttled_batches": 1, "rate_limited_count": 2,
            },
            {
                "cycle_started_at": T0 + timedelta(minutes=10), "cycle_ended_at": T0 + timedelta(minutes=15),
                "due_count": 80, "due_count_by_tier": {"CRITICAL": 30, "HIGH": 50},
                "triggered_count": 4, "triggered_new_count": 2,
                "triggered_card_ids": [4, 5, 6, 7],  # 4 and 5 repeat - should dedup
                "reason_counts": {"watchlist": 1, "market_activity": 3},
                "successful_reads": 75, "failed_reads": 5,
                "successful_card_ids": [12, 13],  # 12 repeats - should dedup
                "throttled_batches": 0, "rate_limited_count": 0,
            },
        ]

    def test_empty_rows_raises(self):
        with pytest.raises(ValueError):
            shadow.build_interval_report([], cards_price_changed=0, bulk_cycles=0)

    def test_interval_bounds_span_all_cycles(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=0, bulk_cycles=0)
        assert report.interval_start == T0
        assert report.interval_end == T0 + timedelta(minutes=15)
        assert report.cycles == 2

    def test_current_and_triggered_new_requests_are_summed(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=0, bulk_cycles=0)
        assert report.current_detailed_requests == 180  # 100 + 80
        assert report.triggered_new_requests == 5  # 3 + 2

    def test_reason_counts_are_summed_across_cycles(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=0, bulk_cycles=0)
        assert report.trigger_reason_counts == {"watchlist": 3, "new_release": 3, "market_activity": 3}

    def test_due_count_by_tier_is_summed_across_cycles(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=0, bulk_cycles=0)
        assert report.due_count_by_tier == {"CRITICAL": 70, "HIGH": 110}

    def test_unique_cards_triggered_dedups_across_cycles(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=0, bulk_cycles=0)
        assert report.unique_cards_triggered == 7  # {1,2,3,4,5,6,7}
        assert report.trigger_selection_events == 9  # 5 + 4, NOT deduped

    def test_unique_successful_card_count_dedups_across_cycles(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=0, bulk_cycles=0)
        assert report.unique_successful_card_count == 4  # {10,11,12,13}

    def test_rate_limited_and_throttled_batches_are_summed(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=0, bulk_cycles=0)
        assert report.rate_limited_total == 2
        assert report.throttled_batches_total == 1

    def test_supporting_signals_pass_through(self):
        report = shadow.build_interval_report(self._rows(), cards_price_changed=42, bulk_cycles=6)
        assert report.cards_price_changed == 42
        assert report.bulk_manifest_checks == 6
        assert report.bulk_cycles_dynamic_blobs_downloaded == 6


class TestShadowIntervalReportDerivedRates:
    def test_hours_is_the_true_wall_clock_span(self):
        rows = [{
            "cycle_started_at": T0, "cycle_ended_at": T0 + timedelta(hours=2),
            "due_count": 0, "due_count_by_tier": {}, "triggered_count": 0,
            "triggered_new_count": 0, "triggered_card_ids": [], "reason_counts": {},
            "successful_reads": 0, "failed_reads": 0, "successful_card_ids": [],
            "throttled_batches": 0, "rate_limited_count": 0,
        }]
        report = shadow.build_interval_report(rows, cards_price_changed=0, bulk_cycles=0)
        assert report.hours == pytest.approx(2.0)

    def test_current_requests_per_hour_and_per_day(self):
        rows = [{
            "cycle_started_at": T0, "cycle_ended_at": T0 + timedelta(hours=1),
            "due_count": 60, "due_count_by_tier": {}, "triggered_count": 0,
            "triggered_new_count": 0, "triggered_card_ids": [], "reason_counts": {},
            "successful_reads": 0, "failed_reads": 0, "successful_card_ids": [],
            "throttled_batches": 0, "rate_limited_count": 0,
        }]
        report = shadow.build_interval_report(rows, cards_price_changed=0, bulk_cycles=0)
        assert report.current_detailed_requests_per_hour == pytest.approx(60.0)
        assert report.current_detailed_requests_per_day == pytest.approx(1440.0)

    def test_projected_requests_add_triggered_new_on_top_of_current(self):
        rows = [{
            "cycle_started_at": T0, "cycle_ended_at": T0 + timedelta(hours=1),
            "due_count": 60, "due_count_by_tier": {}, "triggered_count": 10,
            "triggered_new_count": 10, "triggered_card_ids": [], "reason_counts": {},
            "successful_reads": 0, "failed_reads": 0, "successful_card_ids": [],
            "throttled_batches": 0, "rate_limited_count": 0,
        }]
        report = shadow.build_interval_report(rows, cards_price_changed=0, bulk_cycles=0)
        assert report.projected_detailed_requests_per_hour == pytest.approx(70.0)

    def test_unique_successful_refreshes_per_wall_clock_hour_uses_real_span_not_cycle_count(self):
        """The exact distinction the task called out: two cycles that
        together span 2 real wall-clock hours (e.g. a backoff gap
        between them) must report throughput against those 2 hours, not
        against however many cycles ran."""
        rows = [
            {
                "cycle_started_at": T0, "cycle_ended_at": T0 + timedelta(minutes=1),
                "due_count": 0, "due_count_by_tier": {}, "triggered_count": 0,
                "triggered_new_count": 0, "triggered_card_ids": [], "reason_counts": {},
                "successful_reads": 0, "failed_reads": 0, "successful_card_ids": [1, 2],
                "throttled_batches": 0, "rate_limited_count": 0,
            },
            {
                # A long gap here (rate-limit backoff) - the SECOND
                # cycle only starts 2 hours after the first.
                "cycle_started_at": T0 + timedelta(hours=2), "cycle_ended_at": T0 + timedelta(hours=2, minutes=1),
                "due_count": 0, "due_count_by_tier": {}, "triggered_count": 0,
                "triggered_new_count": 0, "triggered_card_ids": [], "reason_counts": {},
                "successful_reads": 0, "failed_reads": 0, "successful_card_ids": [3],
                "throttled_batches": 0, "rate_limited_count": 0,
            },
        ]
        report = shadow.build_interval_report(rows, cards_price_changed=0, bulk_cycles=0)
        assert report.unique_successful_card_count == 3
        # 3 cards over ~2.02 real hours, not 3 cards over 2 minutes of
        # active cycle time.
        assert report.unique_successful_refreshes_per_wall_clock_hour == pytest.approx(3 / report.hours)
        assert report.unique_successful_refreshes_per_wall_clock_hour < 3.0


# ---------------------------------------------------------------------
# compute_interval_report (DB wiring)
# ---------------------------------------------------------------------


class TestComputeIntervalReport:
    def test_it_queries_shadow_log_price_changes_and_bin_history(self):
        conn = FakeConn(
            fetch_results=[[{
                "cycle_started_at": T0, "cycle_ended_at": T0 + timedelta(minutes=1),
                "due_count": 5, "due_count_by_tier": {"NORMAL": 5}, "triggered_count": 0,
                "triggered_new_count": 0, "triggered_card_ids": [], "reason_counts": {},
                "successful_reads": 5, "failed_reads": 0, "successful_card_ids": [1],
                "throttled_batches": 0, "rate_limited_count": 0,
            }]],
            fetchval_results=[7, 3],  # cards_price_changed, bulk_cycles
        )
        report = run_async(shadow.compute_interval_report(conn, T0, T0 + timedelta(hours=1)))
        assert report.cards_price_changed == 7
        assert report.bulk_manifest_checks == 3
        assert len(conn.fetch_calls) == 1
        assert len(conn.fetchval_calls) == 2


# ---------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------


class TestRenderReport:
    def test_it_produces_readable_text_covering_every_requested_metric(self):
        rows = [{
            "cycle_started_at": T0, "cycle_ended_at": T0 + timedelta(hours=1),
            "due_count": 60, "due_count_by_tier": {"CRITICAL": 60}, "triggered_count": 5,
            "triggered_new_count": 3, "triggered_card_ids": [1, 2, 3], "reason_counts": {"watchlist": 2},
            "successful_reads": 55, "failed_reads": 5, "successful_card_ids": [1, 2],
            "throttled_batches": 1, "rate_limited_count": 2,
        }]
        report = shadow.build_interval_report(rows, cards_price_changed=12, bulk_cycles=4)
        text = shadow.render_report(report)
        for expected in (
            "bulk manifest checks", "dynamic blobs downloaded", "cards whose current price changed",
            "cards triggering detailed collection", "trigger reason counts", "backlog by priority",
            "current detailed requests", "projected w/ triggers added", "estimated reduction",
            "successful reads", "429s", "unique successful refreshes",
        ):
            assert expected in text, f"missing {expected!r} in report text"
