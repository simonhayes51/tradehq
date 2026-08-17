"""Tests for the sales-sync payload mapping. Pure - no browser, no DB."""
from __future__ import annotations

import os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

from futgg_sales_sync import (  # noqa: E402
    _parse_sold_at, AdaptiveThrottle, build_sale_rows,
    circuit_breaker_tripped, classify_page_capture_error, sales_interval_minutes,
    should_fall_back_to_page_capture,
)

NOW = datetime(2026, 8, 3, 23, 0, tzinfo=timezone.utc)


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
    def test_rating_bands(self):
        assert sales_interval_minutes(91) < sales_interval_minutes(82)
        assert sales_interval_minutes(82) < sales_interval_minutes(77)
        assert sales_interval_minutes(77) < sales_interval_minutes(60)

    def test_none_rating_is_slowest(self):
        assert sales_interval_minutes(None) == sales_interval_minutes(50)

    def test_95_plus_is_never_slower_than_85_plus(self):
        assert sales_interval_minutes(98) <= sales_interval_minutes(91)


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
        t = AdaptiveThrottle(8, 0.5)
        t.record(8, 8)
        slowed, shrunk = t.delay, t.batch_size
        for _ in range(4):
            t.record(8, 0)
        assert t.delay == slowed and t.batch_size == shrunk  # not yet
        t.record(8, 0)                                        # fifth
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

    def test_recent_429_wall_is_not_hidden_by_earlier_successes(self):
        t = AdaptiveThrottle(8, 0.5)
        stats = {"ok": 100, "failed": 16}
        t.record(8, 8)
        assert not circuit_breaker_tripped(stats, t)
        t.record(8, 8)
        assert circuit_breaker_tripped(stats, t)
        switch, reason = should_fall_back_to_page_capture(stats, t)
        assert switch
        assert "fully-throttled batches" in reason

    def test_fully_throttled_batch_streak_resets_when_clean(self):
        t = AdaptiveThrottle(8, 0.5)
        t.record(8, 8)
        t.record(8, 0)
        t.record(8, 8)
        assert t.consecutive_fully_throttled_batches == 1


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
