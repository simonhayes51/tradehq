"""
Regression tests for the never-read retry loop in futgg_sales_sync.py.

THE BUG
-------
"Never read" and "due right now" were both encoded as
next_sales_due_at IS NULL. A never-read card that failed was deliberately
left NULL so it kept its place at the front of select_due()'s backfill
ordering - but that same NULL also made it due immediately, so it came
straight back on the next cycle. A card that could never succeed
(delisted, extinct, a bad source_url) was therefore re-attempted every
cycle indefinitely, and because production runs the page_capture
transport, each of those attempts is a full Chromium page navigation.

The two concepts are now separate columns, and these tests pin that
separation plus the bounded backoff that replaced the loop.

No DB and no network - the backoff ladder is a pure function and the SQL
is asserted as text, matching this repo's existing style.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Same shim the sibling suites use: the module fails fast on a missing
# DATABASE_URL at import, and nothing here touches a database.
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_sync as sales  # noqa: E402


def _code_only(text: str) -> str:
    """Strip Python and SQL comments.

    These tests assert on what the module DOES. The surrounding comments
    necessarily discuss the very identifiers being asserted on (that is
    what they are for), so matching raw text would pass or fail on prose.
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("--"):
            continue
        out.append(line.split("  # ")[0])
    return "\n".join(out)


class TestBackoffLadder:
    def test_the_first_failure_keeps_the_existing_short_retry(self):
        """A transient failure must still be retried quickly - that
        behaviour was deliberate and is not what was broken. Only REPEATED
        failure was pathological, so only repeated failure escalates."""
        assert sales.failure_backoff_minutes(1) == sales.FAILURE_RETRY_BACKOFF_MINUTES

    def test_consecutive_failures_increase_the_delay(self):
        delays = [sales.failure_backoff_minutes(n) for n in range(1, 7)]
        assert delays == sorted(delays)
        assert delays[-1] > delays[0]

    def test_the_delay_is_capped_and_never_grows_without_bound(self):
        """The whole point: a permanently-broken card settles at one cheap
        attempt per cap rather than thousands of page loads per day."""
        cap = min(sales.FAILURE_BACKOFF_LADDER[-1], sales.FAILURE_BACKOFF_MAX_MINUTES)
        for n in (6, 10, 100, 10_000):
            assert sales.failure_backoff_minutes(n) == cap

    def test_it_is_deterministic(self):
        assert [sales.failure_backoff_minutes(3)] * 5 == [
            sales.failure_backoff_minutes(3) for _ in range(5)
        ]

    def test_a_nonsense_failure_count_is_treated_as_the_first_failure(self):
        # Defensive: a NULL column read as 0 must not index off the end.
        assert sales.failure_backoff_minutes(0) == sales.failure_backoff_minutes(1)
        assert sales.failure_backoff_minutes(-5) == sales.failure_backoff_minutes(1)

    def test_the_ladder_is_env_configurable(self):
        assert sales._parse_backoff_ladder("5, 10,20") == [5, 10, 20]
        # Junk entries are dropped rather than crashing the worker on boot.
        assert sales._parse_backoff_ladder("5,,abc,-3,10") == [5, 10]
        assert sales._parse_backoff_ladder("") == []


class TestSelectDueOrdering:
    def test_backfill_preference_keys_on_read_history_not_due_time(self):
        """The fix. If this ever reverts to ordering on
        (next_sales_due_at IS NOT NULL) the hot loop returns, because that
        expression is simultaneously the never-read test and the due
        test."""
        source = Path(sales.__file__).read_text()
        due_sql = source.split("async def select_due")[1].split("async def")[0]
        assert "(sales_first_read_at IS NOT NULL)" in due_sql
        assert "(next_sales_due_at IS NOT NULL)" not in due_sql

    def test_never_read_cards_still_outrank_ordinary_maintenance(self):
        # Requirement 7: preference is retained, it is just no longer a
        # licence to retry without limit.
        source = Path(sales.__file__).read_text()
        due_sql = source.split("async def select_due")[1].split("async def")[0]
        order_by = _code_only(due_sql).split("ORDER BY")[1]
        first_key = [l for l in order_by.splitlines() if l.strip()][0]
        assert "sales_first_read_at" in first_key

    def test_the_due_filter_still_admits_never_scheduled_cards(self):
        # A brand-new card has no due-at at all and must still be picked
        # up immediately, otherwise nothing ever gets a first read.
        source = Path(sales.__file__).read_text()
        due_sql = source.split("async def select_due")[1].split("async def")[0]
        assert "next_sales_due_at IS NULL OR next_sales_due_at <= NOW()" in due_sql


class TestFailureAndSuccessWrites:
    def _process_batch_source(self) -> str:
        source = Path(sales.__file__).read_text()
        return source.split("async def process_batch")[1].split(
            "async def process_batch_page_capture"
        )[0]

    def test_every_failure_now_sets_a_retry_time(self):
        """Previously the never-read branch appended no update at all,
        which is what left the card permanently due."""
        body = self._process_batch_source()
        # The failure branch must append unconditionally - no
        # `if not was_never_read` guard around the update.
        assert "if not was_never_read:" not in _code_only(body)
        assert "failure_backoff_minutes(failures)" in body

    def test_a_failure_never_stamps_first_read(self):
        """sales_first_read_at is the backfill-priority witness. If a
        failure could set it, a card that has never returned data would
        silently lose its place in the queue - the exact data-integrity
        bug an earlier fix was written for."""
        body = self._process_batch_source()
        failure_branch = _code_only(body).split("if error:")[1].split("sale_rows =")[0]
        assert "sales_first_read_at" not in failure_branch

    def test_success_resets_the_consecutive_failure_count(self):
        body = self._process_batch_source()
        assert 'sales_interval_minutes(rating), "success", True, 0' in body

    def test_first_read_is_written_once_and_never_overwritten(self):
        body = self._process_batch_source()
        assert "COALESCE(sales_first_read_at, NOW())" in body


class TestCircuitBreakerUnchanged:
    """Explicit scope guard. The throttle and circuit breaker constants
    came out of a real incident (144 HTTP 429s in one run) and were out of
    scope for this change."""

    def test_circuit_breaker_constants_are_untouched(self):
        assert sales.CIRCUIT_BREAKER_THRESHOLD == 0.8
        assert sales.CIRCUIT_BREAKER_MIN_SAMPLE == 20
        assert sales.CIRCUIT_BREAKER_BACKOFF_SECONDS == 300.0

    def test_throttle_error_markers_are_untouched(self):
        assert sales._THROTTLE_ERROR_MARKERS == (
            "429", "403", "sign_5", "data_5", "challenge",
        )

    def test_the_adaptive_throttle_class_still_exists_unmodified(self):
        assert hasattr(sales, "AdaptiveThrottle")
        assert hasattr(sales.AdaptiveThrottle, "record")

    def test_rating_band_intervals_are_untouched(self):
        # This change fixes retries; it does not suppress scheduled work.
        # Request suppression is a separate, flagged phase.
        assert sales.SALES_INTERVALS["85_plus"] == 60
        assert sales.SALES_INTERVALS["80_84"] == 240
        assert sales.SALES_INTERVALS["75_79"] == 720
        assert sales.SALES_INTERVALS["under_75"] == 2880


class TestSchemaAndAudit:
    def test_the_new_columns_are_created_and_backfilled(self):
        source = Path(sales.__file__).read_text()
        schema = source.split("async def ensure_schema")[1].split("#: Throttle-worthy")[0]
        assert "sales_first_read_at" in schema
        assert "sales_consecutive_failures" in schema
        # Existing databases must not all look never-read after deploy.
        assert "UPDATE futgg_players" in schema
        assert "WHERE sales_first_read_at IS NULL AND next_sales_due_at IS NOT NULL" in schema

    def test_coverage_reporting_counts_the_failing_never_read_population(self):
        source = Path(sales.__file__).read_text()
        coverage = source.split("async def report_coverage")[1].split(
            "async def ensure_schema"
        )[0]
        assert "never_read_and_failing" in coverage
        assert "chronic_failures" in coverage
        # never_read must now be measured from read history, not due-at.
        assert "FILTER (WHERE p.sales_first_read_at IS NULL) AS never_read" in coverage

    def test_absent_columns_degrade_instead_of_crashing(self):
        """A worker can be mid-deploy against a database ensure_schema has
        not reached. Turning that into a KeyError would crash-loop the
        very worker this change exists to calm."""
        assert sales._row_value({}, "sales_first_read_at") is None
        assert sales._row_value({"sales_consecutive_failures": None}, "sales_consecutive_failures", 0) == 0
        assert sales._row_value({"sales_consecutive_failures": 3}, "sales_consecutive_failures", 0) == 3
