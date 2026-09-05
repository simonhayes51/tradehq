"""
Tests for app/services/futgg_bin_valuation.py's pure evaluate_bin_fallback()
- no database, no I/O. Constructed BIN-observation series below mimic rows
from futgg_bin_history: [(lowest_bin, captured_at), ...].
"""
from datetime import datetime, timedelta, timezone

from app.services import futgg_reasons as rc
from app.services.futgg_bin_valuation import (
    BinFallbackConfig,
    evaluate_bin_fallback,
)

AS_OF = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
CONFIG = BinFallbackConfig()


def _qualifying_observations(
    *, buckets=10, hours_apart=3, price=10000, as_of=AS_OF, price_fn=None,
):
    """A clean, qualifying series: `buckets` distinct hourly buckets,
    `hours_apart` hours between each (so span comfortably clears the
    24h minimum with the default 10x3=30h), one observation per bucket
    at a flat price unless `price_fn(i)` overrides it."""
    out = []
    for i in range(buckets):
        at = as_of - timedelta(hours=(buckets - i) * hours_apart)
        p = price_fn(i) if price_fn else price
        out.append((p, at))
    return out


class TestFreshCurrentBinRequired:
    def test_missing_current_bin_is_rejected(self):
        result = evaluate_bin_fallback(
            1, _qualifying_observations(),
            current_bin=None, price_age_minutes=None,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies
        assert rc.BIN_NO_LIVE_PRICE in result.reasons.blocking_codes

    def test_stale_current_bin_is_rejected(self):
        result = evaluate_bin_fallback(
            1, _qualifying_observations(),
            current_bin=10000, price_age_minutes=200,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies
        assert rc.BIN_STALE_CURRENT_PRICE in result.reasons.blocking_codes

    def test_fresh_current_bin_passes_this_gate(self):
        result = evaluate_bin_fallback(
            1, _qualifying_observations(),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert result.qualifies
        assert rc.BIN_NO_LIVE_PRICE not in result.reasons.blocking_codes
        assert rc.BIN_STALE_CURRENT_PRICE not in result.reasons.blocking_codes


class TestBucketingAndSpan:
    def test_sparse_bin_history_is_rejected(self):
        """Fewer than min_bucket_count distinct hourly buckets - even if
        each carries a real observation - must not qualify."""
        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=3, hours_apart=3),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies
        assert rc.BIN_INSUFFICIENT_BUCKETS in result.reasons.blocking_codes
        assert result.bin_sample_count == 3

    def test_insufficient_span_is_rejected(self):
        """Enough distinct buckets, but packed into far less than the
        minimum span - many samples from a short window, not a settled
        history."""
        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=1),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies
        assert rc.BIN_INSUFFICIENT_SPAN in result.reasons.blocking_codes

    def test_repeated_unchanged_samples_in_one_bucket_do_not_inflate_confidence(self):
        """Many identical observations crammed into the SAME hour (a
        heartbeat re-confirming an unchanged price) must count as ONE
        bucket, not dozens of independent samples."""
        as_of = AS_OF
        heartbeat_bucket = [
            (10000, as_of - timedelta(hours=30) + timedelta(minutes=m))
            for m in range(0, 50, 5)
        ]
        rest = _qualifying_observations(buckets=9, hours_apart=3, price=10000)
        result = evaluate_bin_fallback(
            1, heartbeat_bucket + rest,
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=as_of, config=CONFIG,
        )
        # 10 real hourly heartbeats worth of *ten* raw rows in one hour plus
        # 9 other hourly buckets = 10 buckets total, not 10 + 9 = 19.
        assert result.bin_sample_count == 10

    def test_qualifying_span_and_buckets_pass(self):
        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert result.qualifies
        assert result.bin_sample_count == 10
        assert result.bin_history_span_hours >= CONFIG.min_span_hours


class TestOutlierFiltering:
    def test_a_single_price_spike_is_filtered(self):
        """One wildly anomalous bucket among many flat ones must not move
        the reference price - the classic MAD-outlier requirement."""
        def price_fn(i):
            return 200000 if i == 5 else 10000

        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3, price_fn=price_fn),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert result.qualifies
        # Reference price stays near the flat 10,000 level, nowhere near
        # the 200,000 spike.
        assert abs(float(result.bin_reference_price) - 10000) < 500

    def test_zero_and_negative_prices_are_dropped_before_bucketing(self):
        obs = _qualifying_observations(buckets=10, hours_apart=3) + [
            (0, AS_OF - timedelta(hours=5)), (-100, AS_OF - timedelta(hours=6)),
        ]
        result = evaluate_bin_fallback(
            1, obs, current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert result.qualifies
        assert abs(float(result.bin_reference_price) - 10000) < 500


class TestStabilityOrTrend:
    def test_highly_volatile_history_is_rejected(self):
        def price_fn(i):
            return 5000 if i % 2 == 0 else 20000

        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=12, hours_apart=3, price_fn=price_fn),
            current_bin=9400, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies
        assert rc.BIN_EXCESSIVE_VOLATILITY in result.reasons.blocking_codes

    def test_stable_flat_history_qualifies(self):
        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3, price=10000),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert result.qualifies
        assert result.bin_trend_direction == "flat"

    def test_a_clear_upward_trend_qualifies_without_being_stable(self):
        def price_fn(i):
            # 10 buckets, roughly +15% from first third to last third.
            return 9000 + i * 200

        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3, price_fn=price_fn),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert result.qualifies
        assert result.bin_trend_direction == "up"

    def test_a_clear_downward_trend_is_never_a_buy_signal(self):
        """A real decline must not qualify - the reference price is a
        median over the whole window, so on a real downtrend it would
        anchor value on the stale, higher earlier level (the falling-knife
        failure mode)."""
        def price_fn(i):
            return 20000 - i * 1000

        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3, price_fn=price_fn),
            current_bin=7000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies


class TestStricterRecommendationRules:
    def test_insufficient_roi_is_rejected(self):
        """current_bin very close to the reference price - real ROI is
        far below the 8% BIN-only floor even though the sell target still
        clears ordinary EA tax."""
        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3, price=10000),
            current_bin=9900, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies
        assert rc.BIN_INSUFFICIENT_ROI in result.reasons.blocking_codes

    def test_insufficient_absolute_profit_is_rejected_on_a_cheap_card(self):
        """A cheap card can clear the 8% ROI floor on paper while the
        absolute coin profit is still trivial - the 500-coin floor exists
        specifically to catch this, so a bronze/silver card cannot
        dominate the feed on a percentage alone."""
        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3, price=1000),
            current_bin=700, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert not result.qualifies
        assert rc.BIN_INSUFFICIENT_PROFIT in result.reasons.blocking_codes

    def test_a_qualifying_high_value_card_clears_both_floors(self):
        result = evaluate_bin_fallback(
            1, _qualifying_observations(buckets=10, hours_apart=3, price=100000),
            current_bin=80000, price_age_minutes=10,
            max_acceptable_price_age_minutes=45, as_of=AS_OF, config=CONFIG,
        )
        assert result.qualifies
        assert result.recommended_buy_max is not None
        assert result.theoretical_max_buy is not None
        assert result.recommended_sell_target is not None
