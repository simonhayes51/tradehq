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
        assert 'sales_interval_minutes(tier), "success", True, 0' in body

    def test_first_read_is_written_once_and_never_overwritten(self):
        """Tolerant of the table alias, because the write became a
        set-based UPDATE ... FROM unnest() and that form REQUIRES one.

        This assertion is on source text, so it can only ever check the
        spelling of the rule, not the rule. The behaviour itself - that a
        success never overwrites an existing sales_first_read_at, against
        a real Postgres, for both the seeded and unseeded branch - is
        pinned in test_futgg_sales_batch_update.py, which also asserts
        the set-based statement produces byte-identical state to the
        per-card loop it replaced."""
        body = self._process_batch_source()
        assert re.search(
            r"COALESCE\(\s*(?:p\.)?sales_first_read_at\s*,\s*NOW\(\)\s*\)", body
        ), "the first-success-wins COALESCE is missing from the status write"


class TestCircuitBreakerUnchanged:
    """Explicit scope guard. The throttle and circuit breaker constants
    came out of a real incident (144 HTTP 429s in one run) and were out of
    scope for this change."""

    def test_circuit_breaker_constants_are_untouched(self):
        assert sales.CIRCUIT_BREAKER_THRESHOLD == 0.8
        assert sales.CIRCUIT_BREAKER_MIN_SAMPLE == 20
        assert sales.CIRCUIT_BREAKER_BACKOFF_SECONDS == 300.0

    def test_throttle_error_markers_are_untouched(self):
        # "pow_" added later (futgg_price_access.py's structured
        # sha256-prefix-v1 proof-of-work challenge) - a deliberate,
        # separately-justified addition, not drift in this file's own
        # retry/backoff logic, which this test still pins otherwise.
        assert sales._THROTTLE_ERROR_MARKERS == (
            "429", "403", "sign_5", "data_5", "challenge", "pow_",
        )

    def test_the_adaptive_throttle_class_still_exists_unmodified(self):
        assert hasattr(sales, "AdaptiveThrottle")
        assert hasattr(sales.AdaptiveThrottle, "record")

    def test_priority_tier_intervals_are_untouched(self):
        # This change fixes retries; it does not suppress scheduled work.
        # Request suppression is a separate, flagged phase. Values updated
        # twice since - first for the rating-band -> adaptive-priority-
        # tier switch (see select_due()), then again for the tier
        # intervals themselves (see SALES_INTERVALS' own comment: the
        # original 15/60/240-minute CRITICAL/HIGH/NORMAL were never
        # achievable given FUT.GG's real rate limit). The property under
        # test - retries do not touch scheduled-interval config - is
        # unchanged either time.
        assert sales.SALES_INTERVALS["HIGH"] == 720
        assert sales.SALES_INTERVALS["NORMAL"] == 1440
        assert sales.SALES_INTERVALS["LOW"] == 2880
        assert sales.SALES_INTERVALS["DORMANT"] == 4000


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


class TestBoundedRun:
    """Cost control by wall clock, not by request count.

    Reducing how many cards this worker reads does not by itself reduce
    what it costs: the bill is for a container running a headed Chromium
    24 hours a day, and an idle container costs the same as a busy one.
    The compute bill only falls when the process exits and the container
    stops - which is what these bounds allow.
    """

    def test_continuous_running_is_still_the_default(self):
        # Absolutely must not change behaviour for anyone who has not
        # opted in - a surprise exit on a service with a restart policy is
        # a restart loop, which costs MORE than running continuously.
        assert sales.MAX_RUNTIME_MINUTES == 0
        assert sales.MAX_CYCLES == 0

    def test_the_bound_is_checked_before_a_cycle_not_during_one(self):
        """A cycle already in flight must finish. Tearing off mid-batch
        would leave cards marked due again and waste the next run's whole
        budget repeating them."""
        source = Path(sales.__file__).read_text()
        loop = source.split("while not shutdown_event.is_set():")[1].split("timers = StageTimers()")[0]
        assert "MAX_RUNTIME_MINUTES" in loop
        assert "MAX_CYCLES" in loop
        assert "break" in loop

    def test_a_bounded_run_exits_when_the_queue_empties(self):
        # Idling with a browser open costs exactly as much as working, so
        # "caught up" must mean "stop", not "wait".
        source = Path(sales.__file__).read_text()
        empty_branch = source.split("if not due:")[1].split("continue")[0]
        assert "if MAX_RUNTIME_MINUTES or MAX_CYCLES:" in empty_branch
        assert "exiting cleanly" in empty_branch

    def test_an_unbounded_run_still_idles_rather_than_exiting(self):
        source = Path(sales.__file__).read_text()
        empty_branch = source.split("if not due:")[1].split("continue")[0]
        assert "sleep_or_shutdown(IDLE_SLEEP, shutdown_event)" in empty_branch


class TestRateLimitDoesNotForceTheExpensiveTransport:
    """A 429 means "slow down". A 403/challenge means "not this way".

    Conflating them was expensive: every throttle marker authorised the
    auto-mode transport switch, so a burst of rate limiting flipped the
    worker from signed_fetch (~25 KB JSON per card) onto page_capture (a
    full ~275 KB page navigation with JS rendering and a 4s settle, per
    card). Roughly 10x the cost per card, chosen automatically, in
    response to a signal that specifically means requests are too
    frequent - and sticky, because a failed reprobe parks it back on
    page_capture.
    """

    def setup_method(self):
        sales._consecutive_signed_throttle_trips = 0

    def test_a_429_storm_does_not_trigger_a_transport_switch(self):
        stats = {"failed": 50, "ok": 0, "errors": {"data_429": 50}}
        assert sales._mostly_challenge_failures(stats) is False

    def test_a_403_wall_still_triggers_a_transport_switch(self):
        # The case the switch was actually designed for must still work.
        stats = {"failed": 50, "ok": 0, "errors": {"data_403": 50}}
        assert sales._mostly_challenge_failures(stats) is True

    def test_a_bot_challenge_still_triggers_a_transport_switch(self):
        stats = {"failed": 30, "ok": 0, "errors": {"challenge_required": 30}}
        assert sales._mostly_challenge_failures(stats) is True

    def test_upstream_5xx_does_not_trigger_a_transport_switch(self):
        # An upstream hiccup says nothing about which transport to use.
        stats = {"failed": 40, "ok": 0, "errors": {"sign_502": 20, "data_503": 20}}
        assert sales._mostly_challenge_failures(stats) is False

    def test_a_mixed_cycle_switches_only_on_a_blocking_majority(self):
        mostly_rate_limited = {"failed": 10, "ok": 0,
                               "errors": {"data_429": 8, "data_403": 2}}
        mostly_blocked = {"failed": 10, "ok": 0,
                          "errors": {"data_429": 2, "data_403": 8}}
        assert sales._mostly_challenge_failures(mostly_rate_limited) is False
        assert sales._mostly_challenge_failures(mostly_blocked) is True

    def test_a_429_still_drives_the_throttle_to_back_off(self):
        """The fix must not stop us reacting to rate limiting - it only
        stops us reacting with the wrong remedy."""
        assert sales.is_throttle_signal("data_429") is True
        assert sales.is_blocking_signal("data_429") is False

    def test_the_throttle_marker_list_is_unchanged(self):
        # AdaptiveThrottle's inputs are untouched; only what the circuit
        # breaker DOES afterwards changed. "pow_" added later for the same
        # reason as TestCircuitBreakerUnchanged's own pin above.
        assert sales._THROTTLE_ERROR_MARKERS == (
            "429", "403", "sign_5", "data_5", "challenge", "pow_",
        )


class TestThrottledTransportFallsBackEventually:
    """Backing off is the right first answer to a 429. Falling back
    afterwards is a SEPARATE decision, gated by
    PAGE_CAPTURE_FALLBACK_ON_THROTTLE (default off - see its own comment:
    production evidence showed page_capture at ~0 cards/min, which makes
    the fallback strictly worse than continuing to back off). These tests
    turn the gate on to pin the escape-hatch behaviour itself; the
    default-off behaviour has its own class below.
    """

    def setup_method(self):
        sales._consecutive_signed_throttle_trips = 0

    @pytest.fixture(autouse=True)
    def _fallback_enabled(self, monkeypatch):
        monkeypatch.setattr(sales, "PAGE_CAPTURE_FALLBACK_ON_THROTTLE", True)

    def _throttled(self):
        return {"failed": 50, "ok": 0, "errors": {"data_429": 50}}

    def _blocked(self):
        return {"failed": 50, "ok": 0, "errors": {"data_403": 50}}

    def test_a_wall_switches_immediately_without_waiting(self):
        # No amount of backing off gets past a refusal.
        switch, why = sales.should_fall_back_to_page_capture(self._blocked())
        assert switch is True
        assert "blocking-shaped" in why

    def test_a_single_throttled_cycle_does_not_switch(self):
        switch, why = sales.should_fall_back_to_page_capture(self._throttled())
        assert switch is False
        assert "backing off rather than switching" in why

    def test_persistent_throttling_eventually_switches(self):
        results = [
            sales.should_fall_back_to_page_capture(self._throttled())[0]
            for _ in range(sales.SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK)
        ]
        assert results[:-1] == [False] * (len(results) - 1)
        assert results[-1] is True

    def test_a_healthy_cycle_clears_the_streak(self):
        """Three unlucky cycles spread across hours of healthy running
        must not silently accumulate into a forced expensive fallback."""
        sales.should_fall_back_to_page_capture(self._throttled())
        sales.should_fall_back_to_page_capture(self._throttled())
        sales.note_signed_fetch_recovered()
        switch, _ = sales.should_fall_back_to_page_capture(self._throttled())
        assert switch is False

    def test_a_wall_after_throttling_still_switches_at_once(self):
        sales.should_fall_back_to_page_capture(self._throttled())
        switch, why = sales.should_fall_back_to_page_capture(self._blocked())
        assert switch is True
        assert "blocking-shaped" in why

    def test_the_fallback_threshold_is_configurable(self):
        assert sales.SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK >= 1

    def test_the_streak_is_reset_after_a_forced_fallback(self):
        for _ in range(sales.SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK):
            sales.should_fall_back_to_page_capture(self._throttled())
        assert sales._consecutive_signed_throttle_trips == 0


class TestPageCaptureFallbackIsOffByDefault:
    """31 August production evidence: once the breaker switched
    signed_fetch's throttling wall to page_capture, the fallback managed
    cards_ok=2 failed=18 in 2311.5s (0 cards/min), with page fetches up
    to 21 minutes and repeated Playwright page crashes - while
    signed_fetch itself had been collecting 150-185 cards per cycle right
    up to the wall.

    So a throttle-shaped trip must never switch transport unless someone
    has explicitly turned page_capture's fallback back on having fixed
    it. Backing off and retrying signed_fetch, indefinitely, is strictly
    better than the fallback as last observed.
    """

    def setup_method(self):
        sales._consecutive_signed_throttle_trips = 0

    def _throttled(self):
        return {"failed": 50, "ok": 0, "errors": {"data_429": 50}}

    def _blocked(self):
        return {"failed": 50, "ok": 0, "errors": {"data_403": 50}}

    def test_the_flag_defaults_to_off(self):
        assert sales.PAGE_CAPTURE_FALLBACK_ON_THROTTLE is False

    def test_persistent_throttling_never_switches(self):
        results = [
            sales.should_fall_back_to_page_capture(self._throttled())[0]
            for _ in range(sales.SIGNED_THROTTLE_CYCLES_BEFORE_FALLBACK * 5)
        ]
        assert all(r is False for r in results)

    def test_a_saturated_throttle_still_does_not_switch(self):
        t = sales.AdaptiveThrottle(8, 0.5)
        t.batch_size = t.min_batch
        t.delay = t.max_delay
        switch, why = sales.should_fall_back_to_page_capture(self._throttled(), t)
        assert switch is False
        assert "PAGE_CAPTURE_FALLBACK_ON_THROTTLE is off" in why

    def test_a_wall_of_fully_throttled_batches_still_does_not_switch(self):
        t = sales.AdaptiveThrottle(8, 0.5)
        t.consecutive_fully_throttled_batches = sales.CONSECUTIVE_FULL_THROTTLE_BATCHES + 5
        switch, _ = sales.should_fall_back_to_page_capture(self._throttled(), t)
        assert switch is False

    def test_a_genuine_block_still_switches_immediately(self):
        # The gate is specific to throttling. A wall is a capability gap
        # page_capture may close, not a cost/reliability tradeoff, so it
        # is unaffected by this flag.
        switch, why = sales.should_fall_back_to_page_capture(self._blocked())
        assert switch is True
        assert "blocking-shaped" in why


class TestFallbackSurvivesBoundedRuns:
    """The consecutive-trip counter lives in process memory. Under
    FUTGG_SALES_MAX_RUNTIME_MINUTES the process exits every run, so a
    streak spanning runs can never accumulate - without a within-cycle
    escape hatch the fallback would never fire at all, and a persistently
    throttled worker would return no sales data indefinitely.

    Exercised with PAGE_CAPTURE_FALLBACK_ON_THROTTLE on - see the class
    above.
    """

    def setup_method(self):
        sales._consecutive_signed_throttle_trips = 0

    @pytest.fixture(autouse=True)
    def _fallback_enabled(self, monkeypatch):
        monkeypatch.setattr(sales, "PAGE_CAPTURE_FALLBACK_ON_THROTTLE", True)

    def _throttled(self):
        return {"failed": 50, "ok": 0, "errors": {"data_429": 50}}

    def _saturated_throttle(self):
        t = sales.AdaptiveThrottle(8, 0.5)
        t.batch_size = t.min_batch
        t.delay = t.max_delay
        return t

    def _fresh_throttle(self):
        return sales.AdaptiveThrottle(8, 0.5)

    def test_a_saturated_throttle_falls_back_on_the_very_first_trip(self):
        switch, why = sales.should_fall_back_to_page_capture(
            self._throttled(), self._saturated_throttle(),
        )
        assert switch is True
        assert "already at its floor" in why

    def test_a_throttle_with_headroom_still_backs_off_first(self):
        switch, _ = sales.should_fall_back_to_page_capture(
            self._throttled(), self._fresh_throttle(),
        )
        assert switch is False

    def test_the_decision_still_works_with_no_throttle_supplied(self):
        switch, _ = sales.should_fall_back_to_page_capture(self._throttled(), None)
        assert switch is False

    def test_saturation_is_measured_against_the_throttles_own_limits(self):
        assert sales._throttle_is_saturated(self._saturated_throttle()) is True
        assert sales._throttle_is_saturated(self._fresh_throttle()) is False
        assert sales._throttle_is_saturated(None) is False
