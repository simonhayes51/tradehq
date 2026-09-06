"""Tests for the sales-sync payload mapping. Pure - no browser, no DB."""
from __future__ import annotations

import os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

from futgg_sales_sync import (  # noqa: E402
    _parse_sold_at, AdaptiveThrottle, build_sale_rows,
    SELECT_LIMIT, SELECT_LIMIT_HARD_CAP,
    circuit_breaker_tripped, classify_page_capture_error, sales_interval_minutes,
    should_fall_back_to_page_capture,
)

NOW = datetime(2026, 8, 3, 23, 0, tzinfo=timezone.utc)


class TestSelectionLimit:
    def test_default_selection_is_hard_capped_for_signed_endpoint(self):
        assert SELECT_LIMIT == SELECT_LIMIT_HARD_CAP == 90


class TestPageCaptureClassification:
    def test_normal_page_without_price_panel_is_no_market(self):
        assert classify_page_capture_error(
            nav_status=200,
            nav_cf_mitigated=None,
            data_status=None,
            data_cf_mitigated=None,
            parse_error=False,
            price_section_count=0,
        ) == "no_price_section"

    def test_tradeable_page_without_api_response_remains_timeout(self):
        assert classify_page_capture_error(
            nav_status=200,
            nav_cf_mitigated=None,
            data_status=None,
            data_cf_mitigated=None,
            parse_error=False,
            price_section_count=1,
        ) == "page_capture_timeout"

    def test_unknown_dom_state_is_never_classified_as_no_market(self):
        assert classify_page_capture_error(
            nav_status=200,
            nav_cf_mitigated=None,
            data_status=None,
            data_cf_mitigated=None,
            parse_error=False,
            price_section_count=None,
        ) == "page_capture_timeout"

    def test_cloudflare_challenge_takes_precedence(self):
        assert classify_page_capture_error(
            nav_status=200,
            nav_cf_mitigated="challenge",
            data_status=None,
            data_cf_mitigated=None,
            parse_error=False,
            price_section_count=0,
        ) == "page_capture_nav_challenge"


class TestParseSoldAt:
    def test_iso_with_z(self):
        assert _parse_sold_at("2026-08-03T21:55:13.434330Z", NOW).tzinfo is not None

    def test_iso_with_offset(self):
        assert _parse_sold_at("2026-08-03T21:55:13+00:00", NOW) is not None

    def test_naive_iso_is_utc(self):
        assert _parse_sold_at("2026-08-03T21:55:13", NOW).tzinfo == timezone.utc

    def test_epoch_seconds_and_millis(self):
        secs = _parse_sold_at(1785794113, NOW)
        millis = _parse_sold_at(1785794113000, NOW)
        assert secs is not None and millis is not None
        assert abs((secs - millis).total_seconds()) < 1

    def test_garbage_returns_none(self):
        for bad in (None, "", "not a date", {}, []):
            assert _parse_sold_at(bad, NOW) is None


class TestBuildSaleRows:
    def _auctions(self, n=3):
        return [
            {"soldPrice": 100000 + i, "soldDate": f"2026-08-03T22:{50+i:02d}:00Z"}
            for i in range(n)
        ]

    def test_maps_price_and_tax(self):
        rows = build_sale_rows(555, self._auctions(1), NOW)
        assert len(rows) == 1
        card_id, listed, sold, tax, net = rows[0][:5]
        assert card_id == 555 and sold == 100000
        assert tax == 5000 and net == 95000  # EA's 5%

    def test_fingerprints_unique_per_sale(self):
        rows = build_sale_rows(555, self._auctions(3), NOW)
        assert len({r[10] for r in rows}) == 3

    def test_identical_sales_get_distinct_occurrence_and_fingerprint(self):
        same = [{"soldPrice": 100000, "soldDate": "2026-08-03T22:50:00Z"}] * 3
        rows = build_sale_rows(555, same, NOW)
        assert [r[9] for r in rows] == [1, 2, 3]
        assert len({r[10] for r in rows}) == 3

    def test_fingerprint_is_stable_across_runs(self):
        # Re-reading the same card must dedupe, not duplicate.
        a = build_sale_rows(555, self._auctions(2), NOW)
        b = build_sale_rows(555, self._auctions(2), NOW + __import__("datetime").timedelta(hours=1))
        assert [r[10] for r in a] == [r[10] for r in b]

    def test_different_cards_never_collide(self):
        a = build_sale_rows(1, self._auctions(2), NOW)
        b = build_sale_rows(2, self._auctions(2), NOW)
        assert not ({r[10] for r in a} & {r[10] for r in b})

    def test_skips_unusable_entries(self):
        rows = build_sale_rows(555, [
            {"soldPrice": None, "soldDate": "2026-08-03T22:50:00Z"},
            {"soldPrice": 0, "soldDate": "2026-08-03T22:50:00Z"},
            {"soldPrice": -5, "soldDate": "2026-08-03T22:50:00Z"},
            {"soldPrice": 1000, "soldDate": None},
            {"soldPrice": "abc", "soldDate": "2026-08-03T22:50:00Z"},
            "not a dict",
            {"soldPrice": 1000, "soldDate": "2026-08-03T22:50:00Z"},
        ], NOW)
        assert len(rows) == 1

    def test_age_seconds_is_never_negative(self):
        future = [{"soldPrice": 1000, "soldDate": "2027-01-01T00:00:00Z"}]
        assert build_sale_rows(555, future, NOW)[0][7] >= 0

    def test_accepts_alternate_field_names(self):
        assert len(build_sale_rows(555, [{"price": 1000, "date": "2026-08-03T22:50:00Z"}], NOW)) == 1

    def test_empty_input(self):
        assert build_sale_rows(555, [], NOW) == []


class TestIntervals:
    """sales_interval_minutes() takes a card_priority.effective_tier
    value now, not a rating - see select_due()/apply_batch_results()."""

    def test_tiers_run_in_urgency_order(self):
        assert (
            sales_interval_minutes("CRITICAL")
            < sales_interval_minutes("HIGH")
            < sales_interval_minutes("NORMAL")
            < sales_interval_minutes("LOW")
            < sales_interval_minutes("DORMANT")
        )

    def test_an_unscored_card_defaults_to_normal(self):
        assert sales_interval_minutes(None) == sales_interval_minutes("NORMAL")

    def test_an_unrecognised_tier_also_defaults_to_normal(self):
        # A card_priority row from a future tier value this worker does
        # not know about yet must degrade to NORMAL, not crash or stall
        # forever on a KeyError-shaped bug.
        assert sales_interval_minutes("SOMETHING_NEW") == sales_interval_minutes("NORMAL")

    def test_critical_is_never_slower_than_high(self):
        assert sales_interval_minutes("CRITICAL") <= sales_interval_minutes("HIGH")


class TestAdaptiveThrottle:
    def test_backs_off_on_throttling(self):
        t = AdaptiveThrottle(batch_size=8, delay=0.5)
        t.record(attempted=8, throttled=8)
        assert t.delay > 0.5
        assert t.batch_size < 8

    def test_backoff_scales_with_severity(self):
        light = AdaptiveThrottle(8, 0.5); light.record(8, 1)
        heavy = AdaptiveThrottle(8, 0.5); heavy.record(8, 8)
        assert heavy.delay > light.delay

    def test_light_throttling_does_not_shrink_batch(self):
        t = AdaptiveThrottle(8, 0.5)
        t.record(attempted=8, throttled=1)   # 12.5% - under the 25% bar
        assert t.batch_size == 8
        assert t.delay > 0.5                  # but still slows down

    def test_recovers_only_after_sustained_success(self):
        # Streak threshold is THROTTLE_RECOVERY_STREAK (3 by default,
        # lowered from the original hardcoded 5 - see that constant's
        # own comment for the production incident behind the change).
        import futgg_sales_sync as sales
        t = AdaptiveThrottle(8, 0.5)
        t.record(8, 8)
        slowed, shrunk = t.delay, t.batch_size
        for _ in range(sales.THROTTLE_RECOVERY_STREAK - 1):
            t.record(8, 0)
        assert t.delay == slowed and t.batch_size == shrunk  # not yet
        t.record(8, 0)                                        # last one
        assert t.delay < slowed and t.batch_size > shrunk

    def test_never_exceeds_starting_limits(self):
        t = AdaptiveThrottle(8, 0.5)
        for _ in range(200):
            t.record(8, 0)
        assert t.batch_size <= 8
        assert t.delay >= 0.5

    def test_delay_is_bounded_under_relentless_throttling(self):
        t = AdaptiveThrottle(8, 0.5)
        for _ in range(500):
            t.record(8, 8)
        assert t.delay <= t.max_delay
        assert t.batch_size >= 1        # never stalls completely

    def test_zero_delay_start_still_backs_off(self):
        # A configured delay of 0 must not multiply to stay at 0.
        t = AdaptiveThrottle(8, 0.0)
        t.record(8, 8)
        assert t.delay > 0

    def test_recent_429_wall_is_not_hidden_by_earlier_successes(self, monkeypatch):
        t = AdaptiveThrottle(8, 0.5)
        stats = {"ok": 100, "failed": 16}
        t.record(8, 8)
        assert not circuit_breaker_tripped(stats, t)
        t.record(8, 8)
        assert circuit_breaker_tripped(stats, t)
        # The circuit breaker tripping and the transport switching are
        # separate decisions - the fallback is gated off by default (see
        # PAGE_CAPTURE_FALLBACK_ON_THROTTLE), so this pins the trip
        # detection itself with the gate explicitly on.
        import futgg_sales_sync as sales
        monkeypatch.setattr(sales, "PAGE_CAPTURE_FALLBACK_ON_THROTTLE", True)
        switch, reason = should_fall_back_to_page_capture(stats, t)
        assert switch
        assert "fully-throttled batches" in reason

    def test_fully_throttled_batch_streak_resets_when_clean(self):
        t = AdaptiveThrottle(8, 0.5)
        t.record(8, 8)
        t.record(8, 0)
        t.record(8, 8)
        assert t.consecutive_fully_throttled_batches == 1


class TestAdaptiveThrottleRollingRecovery:
    """Recovery used to require THROTTLE_RECOVERY_STREAK (was hardcoded
    5) perfectly clean batches in a row - a single throttled request,
    however isolated, reset the count to zero. Production evidence
    (2026-09-03/04) showed a real throttle collapse took ~20 hours of
    wall-clock time to recover under that rule, because a persistent
    low-rate trickle of 429s from the low-priority tail kept resetting
    the streak before it ever reached 5. These tests exercise the
    rolling-window replacement: an isolated throttled batch inside an
    otherwise healthy window is forgiven (streak untouched, not reset),
    while a genuinely elevated ratio, or too little history to judge,
    still resets it exactly as before."""

    def test_isolated_429_does_not_reset_an_otherwise_healthy_streak(self):
        t = AdaptiveThrottle(8, 0.5)
        # Build up enough rolling history for the window to be judged
        # (THROTTLE_ROLLING_MIN_ATTEMPTED attempted) with a healthy
        # ratio, stopping one short of THROTTLE_RECOVERY_STREAK so a
        # recovery step hasn't fired yet.
        for _ in range(sales_module().THROTTLE_RECOVERY_STREAK - 1):
            t.record(8, 0)
        streak_before = t._clean_streak
        assert streak_before > 0
        # One isolated throttled batch - well within
        # THROTTLE_ROLLING_TOLERANCE against the window built above.
        t.record(8, 1)
        assert t._clean_streak == streak_before  # forgiven, not reset
        # This batch's OWN backoff still applied - a 429 is never
        # ignored, only forgiven for recovery bookkeeping.
        assert t.delay > 0.5

    def test_sustained_429s_still_reset_recovery_progress(self):
        t = AdaptiveThrottle(8, 0.5)
        for _ in range(3):
            t.record(8, 0)
        # Every batch throttled from here - ratio stays high, so
        # forgiveness must not apply.
        for _ in range(sales_module().THROTTLE_ROLLING_WINDOW_BATCHES):
            t.record(8, 8)
        assert t._clean_streak == 0

    def test_too_little_history_falls_back_to_the_strict_rule(self):
        # A small batch size means few requests accumulate per record()
        # call - two batches of 4 is only 8 attempted, below
        # THROTTLE_ROLLING_MIN_ATTEMPTED (16 by default), so there isn't
        # yet enough history to judge the window as healthy. The
        # throttled batch must fall back to the original zero-tolerance
        # behaviour (reset) rather than being forgiven.
        t = AdaptiveThrottle(4, 0.5)
        t.record(4, 0)
        t.record(4, 1)
        assert t._clean_streak == 0

    def test_recovery_is_materially_faster_under_mostly_clean_traffic(self):
        # A realistic noisy-but-mostly-healthy stream (occasional
        # isolated 429s, none of them sustained) must still converge to
        # full recovery in a bounded number of batches. Under the
        # ORIGINAL zero-tolerance streak-of-5, every one of these
        # isolated throttles would reset progress back to zero, and
        # production evidence (2026-09-03/04) showed that pattern took
        # ~20 hours of wall-clock time to recover; this asserts the
        # rolling-window replacement instead reaches full recovery
        # within a small, fixed number of batches.
        t = AdaptiveThrottle(8, 0.5, resume_batch_size=1, resume_delay=20.0)
        batches = 0
        while (t.batch_size < 8 or t.delay > 0.5) and batches < 200:
            batches += 1
            # Roughly 1 in 12 batches sees an isolated, low-severity
            # throttle - noisy, not a sustained wall.
            throttled = 1 if batches % 12 == 0 else 0
            t.record(8, throttled)
        assert t.batch_size == 8
        assert abs(t.delay - 0.5) < 1e-9
        assert batches < 200


def sales_module():
    import futgg_sales_sync as sales
    return sales


class TestAdaptiveThrottleResume:
    """resume_batch_size/resume_delay let a fresh bounded run start from
    a previous run's persisted state (see load_throttle_state/
    save_throttle_state) instead of the static BATCH_SIZE/BATCH_DELAY
    every time. The bug this guards against: naively passing the
    persisted (possibly throttled-down) values as the constructor's
    ordinary batch_size/delay would also become the new max_batch/
    min_delay, permanently capping the throttle at whatever it had
    backed off to in the previous run - never able to ease back up to
    the real BATCH_SIZE ceiling again even after sustained clean cycles."""

    def test_resumes_at_the_persisted_values(self):
        t = AdaptiveThrottle(8, 0.5, resume_batch_size=3, resume_delay=2.0)
        assert t.batch_size == 3
        assert t.delay == 2.0

    def test_bounds_stay_at_the_configured_ceiling_not_the_resumed_value(self):
        # Resuming from a throttled-down batch=2 must not make 2 the new
        # ceiling - BATCH_SIZE=8 remains recoverable.
        t = AdaptiveThrottle(8, 0.5, resume_batch_size=2, resume_delay=3.0)
        assert t.max_batch == 8
        assert t.min_delay == 0.5

    def test_throttle_can_recover_back_toward_the_real_ceiling_after_resuming_low(self):
        t = AdaptiveThrottle(8, 0.5, resume_batch_size=2, resume_delay=3.0)
        for _ in range(200):
            t.record(8, 0)  # sustained clean traffic
        assert t.batch_size == 8
        assert abs(t.delay - 0.5) < 1e-9

    def test_no_resume_values_behaves_exactly_like_before(self):
        t = AdaptiveThrottle(8, 0.5)
        assert t.batch_size == 8
        assert t.delay == 0.5
        assert t.max_batch == 8
        assert t.min_delay == 0.5

    def test_resume_values_are_clamped_into_this_runs_bounds(self):
        # Defensive: persisted state from a run under a DIFFERENT
        # BATCH_SIZE/BATCH_DELAY config (an env var changed between
        # runs) must not start outside what this run's own bounds allow.
        t = AdaptiveThrottle(8, 0.5, resume_batch_size=99, resume_delay=0.01)
        assert t.batch_size == 8   # clamped down to max_batch
        assert t.delay == 0.5      # clamped up to min_delay
