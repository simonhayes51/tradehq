"""
Tests for app/services/futgg_intelligence.py's pure evaluate_card() layer
- no database, no I/O. Snapshot dicts below mimic rows of the
futgg_market_snapshot materialized view (migrations/038).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services import trading_math as tm
from app.services import futgg_reasons as reasons_pkg
from app.services import futgg_reasons as rc
from app.services.futgg_bin_valuation import BIN_FALLBACK_CONFIG
from app.services.futgg_config import ENGINE_CONFIG
from app.services.futgg_intelligence import (
    DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES,
    MIN_SALES_FOR_SIGNAL,
    evaluate_card,
)


def _bin_observations(*, buckets=10, hours_apart=3, price=10000, as_of=None, price_fn=None):
    """A clean, qualifying BIN-history series - see
    test_futgg_bin_valuation.py's own helper of the same shape."""
    as_of = as_of or AS_OF
    out = []
    for i in range(buckets):
        at = as_of - timedelta(hours=(buckets - i) * hours_apart)
        p = price_fn(i) if price_fn else price
        out.append((p, at))
    return out

AS_OF = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def _snapshot(**overrides):
    defaults = dict(
        source_card_id=1,
        is_tradeable=True,
        current_bin=10000,
        bin_captured_at=AS_OF - timedelta(minutes=5),
        sales_count=25,
        sales_median=11000,
        sales_trimmed_mean=11000,
        sales_low=10500,
        sales_high=11500,
        sales_stddev=200,
        sales_window_span_minutes=180.0,
        sales_dispersion_ratio=0.018,
    )
    defaults.update(overrides)
    return defaults


class TestUntradeable:
    def test_untradeable_card_is_always_avoid(self):
        ci = evaluate_card(_snapshot(is_tradeable=False), as_of=AS_OF)
        assert ci.signal == "avoid"
        assert ci.risk_level == "avoid"
        assert ci.confidence_score == 0.0
        assert ci.fair_value is None
        assert any("untradeable" in r.lower() for r in ci.signal_reasons)


class TestInsufficientData:
    def test_small_sales_sample_is_insufficient_data(self):
        ci = evaluate_card(_snapshot(sales_count=MIN_SALES_FOR_SIGNAL - 1), as_of=AS_OF)
        assert ci.signal == "insufficient_data"
        assert ci.fair_value is None
        assert any("recent sale" in r.lower() for r in ci.signal_reasons)

    def test_missing_bin_is_insufficient_data(self):
        ci = evaluate_card(_snapshot(current_bin=None, bin_captured_at=None), as_of=AS_OF)
        assert ci.signal == "insufficient_data"
        assert ci.price_age_minutes is None

    def test_stale_price_is_insufficient_data(self):
        stale_at = AS_OF - timedelta(minutes=DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES + 1)
        ci = evaluate_card(_snapshot(bin_captured_at=stale_at), as_of=AS_OF)
        assert ci.signal == "insufficient_data"
        assert any("minutes old" in r for r in ci.signal_reasons)

    def test_never_fabricates_price_from_missing_bin(self):
        """A snapshot with no BIN row must never return a fair_value or
        recommended prices - None, not 0 or a sales-only guess."""
        ci = evaluate_card(_snapshot(current_bin=None, bin_captured_at=None), as_of=AS_OF)
        assert ci.fair_value is None
        assert ci.recommended_buy_max is None
        assert ci.recommended_sell_target is None
        assert ci.expected_profit_after_tax is None
        assert ci.expected_roi is None

    def test_extreme_dispersion_is_insufficient_data(self):
        ci = evaluate_card(_snapshot(sales_dispersion_ratio=0.9), as_of=AS_OF)
        assert ci.signal == "insufficient_data"
        assert any("dispersion" in r.lower() for r in ci.signal_reasons)


class TestConfidenceGradient:
    def test_higher_dispersion_lowers_confidence(self):
        low_disp = evaluate_card(_snapshot(sales_dispersion_ratio=0.02), as_of=AS_OF)
        high_disp = evaluate_card(_snapshot(sales_dispersion_ratio=0.30), as_of=AS_OF)
        assert low_disp.confidence_score > high_disp.confidence_score

    def test_stale_but_still_acceptable_price_lowers_confidence(self):
        fresh = evaluate_card(_snapshot(bin_captured_at=AS_OF - timedelta(minutes=2)), as_of=AS_OF)
        older = evaluate_card(
            _snapshot(bin_captured_at=AS_OF - timedelta(minutes=DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES - 5)),
            as_of=AS_OF,
        )
        assert fresh.confidence_score > older.confidence_score

    def test_larger_sample_raises_confidence(self):
        thin = evaluate_card(_snapshot(sales_count=MIN_SALES_FOR_SIGNAL), as_of=AS_OF)
        thick = evaluate_card(_snapshot(sales_count=40), as_of=AS_OF)
        assert thick.confidence_score >= thin.confidence_score


class TestTaxDelegatesToTradingMath:
    # expected_profit_after_tax/expected_roi are computed against
    # recommended_buy_max (the entry price actually shown to the user as
    # "Buy below"), not the raw current_bin - see evaluate_card()'s own
    # comment on the live bug this fixed (UI showed a "Buy below" ceiling
    # a card's real listing price never supported, alongside a profit
    # figure secretly computed from a cheaper, never-displayed number).
    def test_expected_profit_matches_trading_math_net_profit(self):
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        assert ci.recommended_sell_target is not None
        assert ci.recommended_buy_max is not None
        expected = tm.net_profit(ci.recommended_sell_target, ci.recommended_buy_max)
        assert ci.expected_profit_after_tax == expected

    def test_expected_roi_matches_trading_math_net_roi(self):
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        expected = tm.net_roi(ci.recommended_sell_target, ci.recommended_buy_max)
        assert ci.expected_roi == expected

    def test_sell_target_is_a_valid_ea_increment(self):
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        assert ci.recommended_sell_target == tm.round_to_ea_increment(ci.recommended_sell_target)


class TestSeparatedBuyPrices:
    """Engine v2, item 10: the four prices are distinct, and none of them
    is produced by substituting the card's own asking price into the
    calculation.

    The previous behaviour clamped recommended_buy_max to current_bin,
    which made the advice circular - "the most you should pay" became
    "whatever it costs", so any card could be presented as a valid buy at
    its own ask. These tests pin the corrected contract."""

    def test_all_four_prices_are_present_and_ordered(self):
        ci = evaluate_card(
            _snapshot(
                current_bin=100000, sales_median=140000, sales_trimmed_mean=140000,
                sales_low=130000, sales_high=150000, sales_stddev=4000,
                sales_dispersion_ratio=0.03,
            ),
            as_of=AS_OF,
        )
        # The theoretical ceiling is derived from fair value alone and is
        # deliberately allowed to sit ABOVE the live ask - that is the
        # point: it describes how much headroom the card has, it is not a
        # price we advise paying.
        assert ci.theoretical_max_buy > 100000
        # The advised ceiling is strictly below the theoretical one.
        assert ci.recommended_buy_max < ci.theoretical_max_buy
        # The live ask is below the advised ceiling here, so it is buyable.
        assert ci.current_executable_buy == 100000
        assert ci.break_even_price is not None
        assert ci.recommended_sell_target > ci.break_even_price

    def test_profit_reconciles_with_the_price_a_user_would_actually_pay(self):
        ci = evaluate_card(
            _snapshot(
                current_bin=100000, sales_median=140000, sales_trimmed_mean=140000,
                sales_low=130000, sales_high=150000, sales_stddev=4000,
                sales_dispersion_ratio=0.03,
            ),
            as_of=AS_OF,
        )
        # Arithmetic a user can verify by hand from the displayed numbers.
        assert ci.expected_profit_after_tax == tm.net_profit(
            ci.recommended_sell_target, ci.current_executable_buy
        )

    def test_bin_above_recommended_max_is_a_watch_not_a_buy(self):
        # Default fixture: sales trimmed mean 11,000, live BIN 10,000.
        # Sell target 10,650 (the trimmed mean less sell_target_margin),
        # theoretical ceiling 9,600, advised ceiling 9,200. Previously the
        # ceiling was clamped down to 10,000 and this presented as a buy.
        # It is not one.
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        assert ci.theoretical_max_buy == 9600
        assert ci.recommended_buy_max == 9200
        assert ci.current_executable_buy is None
        assert ci.signal == "watch"
        assert ci.status == "watch"
        assert ci.buy_below == 9200
        assert reasons_pkg.PRICE_ABOVE_MAX_BUY in ci.reason_codes

    def test_the_buy_ceiling_does_not_move_with_the_live_ask(self):
        """The maximum you should pay for a card cannot depend on what it
        currently happens to be listed at - that is the circularity the
        four-price split exists to remove, and it survived in one
        direction until the ceiling stopped being derived from fair
        value. fair_value is 30% BIN, so an ask ABOVE recent sales used to
        inflate the ceiling: the engine told a user to pay more for a card
        precisely because it was listed dearer than it sells for. Below
        sales the same blend is conservative, which is why the asymmetry
        went unnoticed. Both ends now anchor to the same sales-derived
        exit."""
        ceilings = {
            bin_price: evaluate_card(
                _snapshot(current_bin=bin_price), as_of=AS_OF
            ).recommended_buy_max
            for bin_price in (9000, 10000, 13000)
        }
        assert len(set(ceilings.values())) == 1, ceilings

    def test_watch_states_an_explicit_trigger_price_in_english(self):
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        joined = " ".join(ci.signal_reasons)
        assert "9,200" in joined
        assert "falls to" in joined


class TestSignalDecision:
    def test_strong_discount_with_good_confidence_yields_buy_or_strong_buy(self):
        # BIN well below sales evidence, tight sample, fresh price.
        ci = evaluate_card(
            _snapshot(current_bin=9000, sales_median=11500, sales_trimmed_mean=11500, sales_count=40),
            as_of=AS_OF,
        )
        assert ci.signal in ("buy", "strong_buy")
        assert ci.expected_roi is not None and ci.expected_roi > 0
        assert ci.signal_reasons  # concrete, non-empty reasons

    def test_bin_above_fair_value_does_not_signal_buy(self):
        ci = evaluate_card(
            _snapshot(current_bin=15000, sales_median=11000, sales_trimmed_mean=11000),
            as_of=AS_OF,
        )
        assert ci.signal in ("avoid", "hold", "watch")

    def test_signal_reasons_are_concrete_and_value_grounded(self):
        ci = evaluate_card(_snapshot(current_bin=9500), as_of=AS_OF)
        joined = " ".join(ci.signal_reasons)
        # At least one reason should reference the actual sample size or
        # a concrete number, not just a generic label.
        assert any(char.isdigit() for char in joined)


class TestDecimalFromAsyncpg:
    """Regression test for a live 500: asyncpg returns Postgres numeric
    columns as Decimal, not float. futgg_market_snapshot's
    sales_window_span_minutes/sales_dispersion_ratio columns hit this in
    production (`TypeError: unsupported operand type(s) for /: 'decimal.Decimal'
    and 'float'` inside _compute_liquidity_score's `span_minutes / 60.0`) even
    though every test fixture here had only ever used plain floats/ints,
    which is exactly why it wasn't caught earlier."""

    def test_decimal_span_minutes_does_not_raise(self):
        ci = evaluate_card(
            _snapshot(sales_window_span_minutes=Decimal("853.0"), sales_count=40),
            as_of=AS_OF,
        )
        assert ci.liquidity_score is not None

    def test_decimal_span_minutes_over_an_hour_formats_in_reasons(self):
        # Exercises the second Decimal/float division site (the
        # "occurred over the last N hours" reason string), which a
        # short-span fixture never reaches.
        ci = evaluate_card(
            _snapshot(sales_window_span_minutes=Decimal("125.0"), sales_count=40),
            as_of=AS_OF,
        )
        assert any("hour" in reason for reason in ci.signal_reasons)

    def test_decimal_dispersion_ratio_does_not_raise(self):
        ci = evaluate_card(
            _snapshot(sales_dispersion_ratio=Decimal("0.0180")),
            as_of=AS_OF,
        )
        assert ci.confidence_score is not None


class TestTierAwareStaleness:
    """A flat 120-minute freshness cutoff applied to every card equally
    was itself a product bug: a "buy now" tip on a fast-moving special
    card could sit confidently presented as fresh right up to the edge
    of its own hour-long refresh interval, by which point the discount
    that made it a tip may already be gone. Each price_tier now has its
    own, much tighter, threshold."""

    def test_special_tier_flags_stale_well_before_default_threshold(self):
        # 45 minutes old: well within the old flat 120-minute cutoff, but
        # beyond special's own 40-minute threshold (raised from 20 - see
        # futgg_config.py's comment and test_futgg_price_freshness_clock.py -
        # to clear the bulk price sync's own worst-case heartbeat gap of up
        # to 29 minutes for a genuinely stable, correctly-tracked price).
        ci = evaluate_card(
            _snapshot(price_tier="special", bin_captured_at=AS_OF - timedelta(minutes=45)),
            as_of=AS_OF,
        )
        assert ci.signal == "insufficient_data"
        assert any("special-tier" in r for r in ci.signal_reasons)

    def test_bronze_tier_tolerates_an_age_that_would_flag_special(self):
        # Same 45-minute age a special-tier card was flagged stale at -
        # bronze's much longer market cadence tolerates this fine.
        ci = evaluate_card(
            _snapshot(price_tier="bronze", bin_captured_at=AS_OF - timedelta(minutes=45)),
            as_of=AS_OF,
        )
        assert ci.signal != "insufficient_data"

    def test_unknown_tier_falls_back_to_default_threshold(self):
        stale_at = AS_OF - timedelta(minutes=DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES - 5)
        ci = evaluate_card(
            _snapshot(price_tier=None, bin_captured_at=stale_at),
            as_of=AS_OF,
        )
        assert ci.signal != "insufficient_data"
        too_stale = AS_OF - timedelta(minutes=DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES + 5)
        ci2 = evaluate_card(
            _snapshot(price_tier=None, bin_captured_at=too_stale),
            as_of=AS_OF,
        )
        assert ci2.signal == "insufficient_data"

    def test_special_tier_confidence_degrades_faster_than_bronze_at_same_age(self):
        age = AS_OF - timedelta(minutes=15)
        special = evaluate_card(_snapshot(price_tier="special", bin_captured_at=age), as_of=AS_OF)
        bronze = evaluate_card(_snapshot(price_tier="bronze", bin_captured_at=age), as_of=AS_OF)
        assert special.confidence_score < bronze.confidence_score


class TestApproximateTimeNeverClaimedExact:
    def test_intelligence_output_has_no_exact_timestamp_claim(self):
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        # price_age_minutes is derived from bin_captured_at (a real DB
        # timestamp, not a sales approximation) - sanity check it's an int.
        assert isinstance(ci.price_age_minutes, int)


def _falling_series(start_price, end_price, *, n=12, hours=48.0):
    """Monotonic decline with a steepening tail - the shape that used to
    produce the engine's strongest buy signals."""
    step = hours / (n - 1)
    out = []
    for i in range(n):
        # Quadratic easing so the decline accelerates rather than being linear.
        frac = (i / (n - 1)) ** 1.6
        price = start_price + (end_price - start_price) * frac
        out.append((price, AS_OF - timedelta(hours=hours - step * i)))
    return out


def _flat_series(price, *, n=12, hours=48.0):
    step = hours / (n - 1)
    return [
        (price + (50 if i % 2 else -50), AS_OF - timedelta(hours=hours - step * i))
        for i in range(n)
    ]


class TestFallingKnifeGate:
    """The single most important behavioural change in engine v2.

    A card that has fallen hard has a stale-high sales median, so the
    naive "BIN vs median" comparison scores it as the *biggest* discount
    available. With no trend term, the engine ranked exactly the cards
    that were still collapsing as its top opportunities. These tests pin
    the gate that stops that."""

    def test_steep_decline_below_stale_median_is_not_a_buy(self):
        # BIN 60,000 against a 100,000 median: a 40% "discount" on the
        # old logic, and a large positive expected ROI. It is a card in
        # free fall.
        snapshot = _snapshot(
            current_bin=60000, sales_median=100000, sales_trimmed_mean=100000,
            sales_low=58000, sales_high=140000, sales_stddev=12000,
            sales_dispersion_ratio=0.12, sales_count=30,
        )
        ci = evaluate_card(
            snapshot, as_of=AS_OF, sales=_falling_series(140000, 62000),
        )
        assert ci.trend_state in ("falling_knife", "downtrend")
        assert ci.signal == "avoid"
        assert ci.risk_level == "high"
        assert (
            reasons_pkg.FALLING_KNIFE in ci.reason_codes
            or reasons_pkg.UNRESOLVED_DOWNTREND in ci.reason_codes
        )

    def test_same_card_without_the_trend_gate_would_have_looked_great(self):
        # Identical snapshot, no sales series supplied. This documents
        # exactly how large the mispricing looked before the gate existed
        # - and that omitting the series now degrades to a capped signal
        # rather than silently reverting to the old behaviour.
        snapshot = _snapshot(
            current_bin=60000, sales_median=100000, sales_trimmed_mean=100000,
            sales_low=58000, sales_high=140000, sales_stddev=12000,
            sales_dispersion_ratio=0.12, sales_count=30,
        )
        ci = evaluate_card(snapshot, as_of=AS_OF)
        assert ci.expected_roi > 0.30  # the discount the old engine chased
        assert ci.trend_state == "insufficient_trend_data"
        # Capped, never promoted to strong_buy, because the trend is unknown.
        assert ci.signal != "strong_buy"

    def test_stabilised_card_below_median_is_allowed_but_capped(self):
        # Fell, then genuinely flattened. This IS a legitimate buy - the
        # gate must not be so blunt that it blocks every discounted card.
        sales = _falling_series(120000, 92000, n=7, hours=48.0) + _flat_series(
            91500, n=7, hours=16.0
        )
        snapshot = _snapshot(
            current_bin=88000, sales_median=95000, sales_trimmed_mean=95000,
            sales_low=88000, sales_high=120000, sales_stddev=5000,
            sales_dispersion_ratio=0.05, sales_count=30,
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=sales)
        assert ci.trend_state in ("stabilising", "sideways", "recovering")
        assert ci.signal in ("buy", "strong_buy", "watch")
        assert ci.signal != "avoid"

    def test_sideways_liquid_market_still_produces_a_buy(self):
        snapshot = _snapshot(
            current_bin=9000, sales_median=11500, sales_trimmed_mean=11500,
            sales_count=40,
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20))
        assert ci.trend_state == "sideways"
        assert ci.signal in ("buy", "strong_buy")
        assert ci.status == "active"


class TestSbcRewardGate:
    """Production incident: Lamine Yamal 99 RW was live-flagged as the
    homepage's Top Card - "23% below median, broadly flat" - while a
    live SBC ("Summer Stars Tournament Winners", ~1.7m to complete) was
    producing fresh copies of the exact same card. The trend layer is
    price-only and has no way to see that; sbc_reward_active is a
    separate, independent guard fed from app/services/sbc_reward_match.py
    (see market_data_provider._attach_sbc_reward_watch).

    Base case is TestFallingKnifeGate.test_sideways_liquid_market_
    still_produces_a_buy's own snapshot - a sideways market that clears
    every other bar - with only sbc_reward_active added, so these tests
    isolate exactly what the new guard changes.
    """

    def _sideways_buy_snapshot(self, **overrides):
        defaults = dict(
            current_bin=9000, sales_median=11500, sales_trimmed_mean=11500,
            sales_count=40,
        )
        defaults.update(overrides)
        return _snapshot(**defaults)

    def test_an_active_reward_downgrades_a_buy_to_a_watch(self):
        snapshot = self._sideways_buy_snapshot(
            sbc_reward_active=True, sbc_reward_title="Lamine Yamal",
            sbc_reward_ends_at=AS_OF + timedelta(hours=30),
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20))
        assert ci.signal == "watch"
        assert ci.status == "watch"
        assert rc.SBC_REWARD_ACTIVE in ci.reason_codes

    def test_the_reward_title_and_countdown_are_in_the_explanation(self):
        snapshot = self._sideways_buy_snapshot(
            sbc_reward_active=True, sbc_reward_title="Lamine Yamal",
            sbc_reward_ends_at=AS_OF + timedelta(hours=30),
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20))
        joined = " ".join(ci.signal_reasons)
        assert "Lamine Yamal" in joined
        assert "live SBC" in joined
        assert "30h" in joined

    def test_no_flag_is_the_same_as_before_this_feature(self):
        # sbc_reward_active absent entirely (not just False) - the shape
        # a database on an older migration or an un-migrated read path
        # would produce. Must degrade to normal buy-gating, not crash.
        snapshot = self._sideways_buy_snapshot()
        assert "sbc_reward_active" not in snapshot
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20))
        assert ci.signal in ("buy", "strong_buy")

    def test_an_explicit_false_flag_does_not_gate(self):
        snapshot = self._sideways_buy_snapshot(sbc_reward_active=False)
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20))
        assert ci.signal in ("buy", "strong_buy")

    def test_missing_ends_at_still_gates_just_without_a_countdown(self):
        snapshot = self._sideways_buy_snapshot(
            sbc_reward_active=True, sbc_reward_title="Some Player", sbc_reward_ends_at=None,
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20))
        assert ci.signal == "watch"
        assert "Reconsider once the SBC ends" not in " ".join(ci.signal_reasons)

    def test_a_naive_datetime_degrades_rather_than_raising(self):
        # evaluate_card() is documented to never raise on a malformed
        # optional field - a hand-built snapshot with a naive datetime
        # (missing tzinfo) must not crash the whole evaluation.
        snapshot = self._sideways_buy_snapshot(
            sbc_reward_active=True, sbc_reward_title="Some Player",
            sbc_reward_ends_at=datetime(2026, 1, 12, 0, 0),  # naive, no tzinfo
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20))
        assert ci.signal == "watch"

    def test_the_kill_switch_disables_the_gate(self):
        from dataclasses import replace

        cfg = replace(ENGINE_CONFIG, sbc_reward_guard_enabled=False)
        snapshot = self._sideways_buy_snapshot(
            sbc_reward_active=True, sbc_reward_title="Lamine Yamal",
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_flat_series(11500, n=20), config=cfg)
        assert ci.signal in ("buy", "strong_buy")
        assert rc.SBC_REWARD_ACTIVE not in ci.reason_codes

    def test_a_falling_knife_that_is_also_an_sbc_reward_is_still_avoid(self):
        # The falling-knife guard is the single most important gate in
        # the engine (see TestFallingKnifeGate's own docstring) and must
        # not be softened to a mere watch just because this new guard
        # also applies - avoid outranks watch.
        snapshot = _snapshot(
            current_bin=60000, sales_median=100000, sales_trimmed_mean=100000,
            sales_low=58000, sales_high=140000, sales_stddev=12000,
            sales_dispersion_ratio=0.12, sales_count=30,
            sbc_reward_active=True, sbc_reward_title="Some Player",
        )
        ci = evaluate_card(snapshot, as_of=AS_OF, sales=_falling_series(140000, 62000))
        assert ci.signal == "avoid"
        assert rc.FALLING_KNIFE in ci.reason_codes or rc.UNRESOLVED_DOWNTREND in ci.reason_codes

    def test_it_is_one_of_the_blocking_codes(self):
        assert reasons_pkg.SBC_REWARD_ACTIVE in reasons_pkg.BLOCKING_CODES


class TestExpiryAndProvenance:
    def test_every_result_is_versioned(self):
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        assert ci.engine_version.startswith("futgg-")
        assert ci.trend_version.startswith("trend-")
        assert ci.evaluated_at == AS_OF
        assert ci.evaluated_bin == 10000

    def test_active_recommendation_carries_an_expiry(self):
        ci = evaluate_card(
            _snapshot(current_bin=9000, sales_median=11500, sales_trimmed_mean=11500,
                      sales_count=40),
            as_of=AS_OF, sales=_flat_series(11500, n=20),
        )
        assert ci.expiry_minutes is not None and ci.expiry_minutes > 0
        assert ci.expires_at == AS_OF + timedelta(minutes=ci.expiry_minutes)

    def test_expiry_never_outlives_the_price_freshness_budget(self):
        # A price already 100 minutes old on a 120-minute default tier has
        # ~20 minutes of credibility left, not a fresh full window.
        ci = evaluate_card(
            _snapshot(
                current_bin=9000, sales_median=11500, sales_trimmed_mean=11500,
                sales_count=40, bin_captured_at=AS_OF - timedelta(minutes=100),
            ),
            as_of=AS_OF, sales=_flat_series(11500, n=20),
        )
        assert ci.expiry_minutes <= 20

    def test_stale_price_yields_insufficient_data_with_a_code(self):
        ci = evaluate_card(
            _snapshot(bin_captured_at=AS_OF - timedelta(minutes=600)), as_of=AS_OF,
        )
        assert ci.status == "insufficient_data"
        assert reasons_pkg.STALE_MARKET in ci.reason_codes


class TestStructuredReasons:
    def test_reasons_expose_codes_and_english(self):
        ci = evaluate_card(_snapshot(), as_of=AS_OF)
        assert ci.reasons, "structured reasons must be populated"
        for reason in ci.reasons:
            assert set(reason) == {"code", "message"}
            assert reason["message"]
        # The legacy free-text shape stays available for the existing API.
        assert ci.signal_reasons == [r["message"] for r in ci.reasons]

    def test_blocking_codes_are_separated_from_informational_ones(self):
        ci = evaluate_card(_snapshot(sales_count=1), as_of=AS_OF)
        assert reasons_pkg.INSUFFICIENT_SALES in ci.blocking_codes
        # Informational codes must never appear as blocking.
        assert reasons_pkg.INFO_PRICE_AGE not in ci.blocking_codes


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))


class TestSalesStaleness:
    """A fresh BIN beside a days-old sales sample produced a confident
    verdict about a market that had already moved. Observed live: a card
    at 337,000 marked AVOID against a ~286,000 target derived from sales
    whose newest print was four days earlier, while the chart beside it
    showed a 396,000 sale."""

    def _snap(self, sale_age_hours, **kw):
        base = dict(
            current_bin=337000, sales_median=290000, sales_trimmed_mean=286000,
            sales_low=250000, sales_high=450000, sales_stddev=20000,
            sales_count=30, sales_dispersion_ratio=0.07,
            latest_sale_at=AS_OF - timedelta(hours=sale_age_hours),
        )
        base.update(kw)
        return _snapshot(**base)

    def test_four_day_old_sales_block_a_verdict(self):
        ci = evaluate_card(self._snap(96), as_of=AS_OF)
        assert ci.status == "insufficient_data"
        assert reasons_pkg.STALE_SALES in ci.reason_codes
        # No profit figure is offered from a sample this old.
        assert ci.expected_profit_after_tax is None

    def test_fresh_sales_still_produce_a_verdict(self):
        ci = evaluate_card(self._snap(1), as_of=AS_OF)
        assert reasons_pkg.STALE_SALES not in ci.reason_codes
        assert ci.fair_value is not None

    def test_confidence_decays_with_sales_age(self):
        fresh = evaluate_card(self._snap(1), as_of=AS_OF)
        older = evaluate_card(self._snap(36), as_of=AS_OF)
        assert older.confidence_score < fresh.confidence_score

    def test_sales_age_is_surfaced_to_the_user(self):
        ci = evaluate_card(self._snap(30), as_of=AS_OF)
        assert any("most recent completed sale" in m.lower() for m in ci.signal_reasons)

    def test_missing_latest_sale_at_is_neutral_not_punished(self):
        # Plenty of thin cards legitimately have no recent print; that is
        # not the same as a known-stale sample.
        ci = evaluate_card(self._snap(1, latest_sale_at=None), as_of=AS_OF)
        assert reasons_pkg.STALE_SALES not in ci.reason_codes
        assert ci.confidence_score > 0


class TestImplausibleEdge:
    """The Time Warp Ronaldo incident, reproduced.

    A live BIN of 138,000 - correct, four minutes old, confirmed against
    the EA web app - sat beside a ~755,000 recent-sales estimate because
    the card had crashed and the sales window straddled the crash. The
    engine valued it at 570,000, advised buying at up to 500,000, and
    quoted a 403,500 profit on a card worth 138,000.

    Every existing gate passed it: the price was fresh, the sales were
    plentiful and recent, and the trend layer could not establish a slope
    from a sparse, unevenly spaced sample - falling back to a state that
    only caps the signal rather than blocking it.
    """

    def _crashed_card(self, **overrides):
        snapshot = dict(
            current_bin=138000,
            bin_captured_at=AS_OF - timedelta(minutes=4),
            sales_count=40,
            sales_median=755000,
            sales_trimmed_mean=755000,
            sales_low=138000,
            sales_high=2250000,
            sales_stddev=600000,
            sales_dispersion_ratio=0.8,
        )
        snapshot.update(overrides)
        return _snapshot(**snapshot)

    def test_live_price_far_below_sales_is_never_a_buy(self):
        ci = evaluate_card(self._crashed_card(), as_of=AS_OF)
        assert ci.signal not in ("buy", "strong_buy")

    def test_the_divergence_is_reported_as_the_reason(self):
        ci = evaluate_card(self._crashed_card(), as_of=AS_OF)
        assert rc.SALES_BIN_DIVERGENCE in ci.reason_codes
        assert rc.SALES_BIN_DIVERGENCE in ci.blocking_codes

    def test_no_buy_advice_is_published_for_a_broken_anchor(self):
        # The specific harm: a "buy below 500,000" on a 138,000 card.
        # Refusing to value it must mean no entry price at all, not a
        # smaller wrong one.
        ci = evaluate_card(self._crashed_card(), as_of=AS_OF)
        assert ci.recommended_buy_max is None
        assert ci.expected_profit_after_tax is None

    def test_an_ordinary_discount_is_still_allowed_through(self):
        # 20% below the sales estimate is a normal, tradeable edge and
        # must not be caught by the plausibility gate.
        ci = evaluate_card(
            _snapshot(current_bin=8800, sales_median=11000, sales_trimmed_mean=11000),
            as_of=AS_OF,
        )
        assert rc.SALES_BIN_DIVERGENCE not in ci.reason_codes

    def test_the_boundary_is_the_configured_ratio(self):
        cfg_ratio = ENGINE_CONFIG.min_bin_to_sales_ratio
        sales = 100000
        just_above = evaluate_card(
            _snapshot(current_bin=int(sales * cfg_ratio) + 1000,
                      sales_median=sales, sales_trimmed_mean=sales),
            as_of=AS_OF,
        )
        just_below = evaluate_card(
            _snapshot(current_bin=int(sales * cfg_ratio) - 1000,
                      sales_median=sales, sales_trimmed_mean=sales),
            as_of=AS_OF,
        )
        assert rc.SALES_BIN_DIVERGENCE not in just_above.reason_codes
        assert rc.SALES_BIN_DIVERGENCE in just_below.reason_codes


class TestSalesSpreadGate:
    """Regression cover for the reported "the site prices just don't match,
    and it's giving poor advice" case, reproduced from the real production
    row for Willian Pacho UCL RTTF 96 (source_card_id 134473924): 40 sales
    inside six hours ranging 33,500 to 94,500, with 33,500 and 87,000
    printing in the SAME hour.

    stddev/median for that sample is 0.199 and passed the 0.45
    extreme_dispersion_ratio gate comfortably. The interdecile spread is
    0.484. Nothing looked at it, and the consequence was not a slightly-off
    number: with no real trend in the series to find, futgg_trend.py's
    verdict became a function of how many rows it happened to be handed -
    the same card, same minute, same data gave BUY at n=8, STRONG BUY at
    n=12, AVOID at n=15, BUY at n=20-30 and AVOID at n=40, while reported
    confidence climbed from 0.63 to 0.85."""

    #: Real sold prices, newest first, straight off futgg_sales_history.
    PACHO_SALES = [
        64000, 61000, 58000, 94500, 65000, 88000, 62500, 62000, 89500, 61000,
        89000, 59500, 70500, 63000, 75000, 72000, 55000, 68000, 90000, 89500,
        49500, 87000, 50000, 75000, 82500, 60000, 83500, 85000, 82500, 74000,
        82500, 55500, 85500, 33500, 82500, 72000, 79000, 60000, 55000, 55000,
    ]

    def _pacho(self, *, current_bin, sales):
        srt = sorted(sales)
        n = len(srt)
        median = srt[n // 2]
        p10, p90 = srt[max(0, n // 10)], srt[min(n - 1, (n * 9) // 10)]
        trimmed = sum(p for p in srt if p10 <= p <= p90) / len(
            [p for p in srt if p10 <= p <= p90]
        )
        return _snapshot(
            current_bin=current_bin, sales_count=n,
            sales_median=median, sales_trimmed_mean=trimmed,
            sales_low=srt[0], sales_high=srt[-1],
            sales_dispersion_ratio=0.199,
            sales_interdecile_ratio=round((p90 - p10) / median, 4),
        )

    def test_a_series_with_no_settled_price_is_declined_at_every_sample_size(self):
        # The specific failure: the verdict used to oscillate between
        # STRONG BUY and AVOID purely on how many rows the trend layer saw.
        # It must now be declined regardless, so there is nothing left to
        # oscillate.
        for n in (8, 12, 15, 20, 30, 40):
            snap = self._pacho(current_bin=58000, sales=self.PACHO_SALES[:n])
            ci = evaluate_card(snap, as_of=AS_OF)
            assert ci.signal == "insufficient_data", (n, ci.signal)
            assert ci.recommended_sell_target is None, n
            assert rc.EXCESSIVE_DISPERSION in ci.reason_codes, n

    def test_the_stddev_ratio_alone_would_not_have_caught_it(self):
        # Guards the reason this gate exists at all: if stddev/median ever
        # starts catching this shape on its own, the interdecile gate has
        # stopped earning its place. 0.199 is the real measured value.
        assert 0.199 < ENGINE_CONFIG.extreme_dispersion_ratio
        assert 0.484 >= ENGINE_CONFIG.extreme_interdecile_ratio

    def test_a_tightly_traded_card_is_not_caught_by_the_gate(self):
        # The gate must decline broken cards, not ordinary ones.
        ci = evaluate_card(
            _snapshot(current_bin=9000, sales_count=40, sales_interdecile_ratio=0.04),
            as_of=AS_OF,
        )
        assert ci.signal != "insufficient_data"
        assert rc.EXCESSIVE_DISPERSION not in ci.reason_codes

    def test_a_snapshot_predating_migration_050_skips_the_gate(self):
        # sales_interdecile_ratio is absent from stored rows written before
        # migration 050 (and from hand-built dicts). Absent must mean "not
        # tested", never a crash.
        snap = _snapshot(current_bin=9000)
        snap.pop("sales_interdecile_ratio", None)
        assert evaluate_card(snap, as_of=AS_OF).signal != "insufficient_data"


class TestBinDetachedAboveSales:
    def test_an_ask_far_above_recent_sales_is_declined(self):
        # Pacho again, from the other side: every listing parked at the
        # card's 100,000 price cap while real sales cleared around 70,600.
        # This direction was previously ungated entirely, and it is not the
        # harmless one - fair value is 30% BIN-weighted, so the detached ask
        # dragged the sell target up and the user listed at a price nothing
        # was paying.
        ci = evaluate_card(
            _snapshot(current_bin=30000, sales_median=11000, sales_trimmed_mean=11000),
            as_of=AS_OF,
        )
        assert ci.signal == "insufficient_data"
        assert rc.SALES_BIN_DIVERGENCE in ci.reason_codes

    def test_a_modestly_higher_ask_is_still_valued(self):
        # A card genuinely on the way up prints a high BIN before the sales
        # window catches up. That is a real signal, not an error.
        ci = evaluate_card(_snapshot(current_bin=13000), as_of=AS_OF)
        assert rc.SALES_BIN_DIVERGENCE not in ci.reason_codes


class TestSellTargetIsSalesDerived:
    def test_the_sell_target_does_not_move_with_the_live_ask(self):
        """Reported as "it said sell for 90, it actually sold at 72". The
        sell target used to BE fair value, which is 30% live BIN, so the
        price we told a user to LIST AT moved whenever the cheapest ASK
        moved - even though what buyers were paying had not changed."""
        targets = {
            bin_price: evaluate_card(
                _snapshot(current_bin=bin_price), as_of=AS_OF
            ).recommended_sell_target
            for bin_price in (9000, 10000, 13000)
        }
        assert len(set(targets.values())) == 1, targets

    def test_the_sell_target_sits_just_under_the_anomaly_trimmed_average(self):
        # sales_trimmed_mean is the mean of sales between p10 and p90 - the
        # average sale price with the silly-low and silly-high anomalies
        # excluded. Listing AT that average clears only about half the
        # time, so the advised target sits a margin below it.
        ci = evaluate_card(_snapshot(sales_trimmed_mean=11000), as_of=AS_OF)
        expected = Decimal("11000") * (Decimal("1") - Decimal(str(ENGINE_CONFIG.sell_target_margin)))
        assert ci.recommended_sell_target <= expected
        assert ci.recommended_sell_target > expected * Decimal("0.98")


class TestBinHistoryFallbackWiring:
    """evaluate_card()'s integration with app/services/futgg_bin_valuation.py
    - the fallback is attempted ONLY when completed-sales evidence is
    missing or stale, never when it merely looks unreliable (dispersion/
    divergence), and every field it stamps is checked here so a caller can
    always tell a confirmed sale from an estimated BIN-history valuation.
    """

    def test_fresh_completed_sales_take_precedence_over_bin_observations(self):
        # A normal, healthy snapshot - plenty of BIN observations are
        # available too, but must never be consulted while real sales
        # evidence is fresh and sufficient.
        ci = evaluate_card(
            _snapshot(), as_of=AS_OF, bin_observations=_bin_observations(),
        )
        assert ci.valuation_source == "completed_sales"
        assert ci.evidence_quality == "confirmed"
        assert ci.bin_sample_count is None
        assert ci.bin_reference_price is None

    def test_no_bin_observations_passed_behaves_exactly_as_before(self):
        """Omitting bin_observations entirely (every caller that has not
        been updated yet) must degrade to the pre-fallback behaviour
        byte-for-byte - insufficient_data, no BIN fields populated, no
        BIN reason codes anywhere."""
        ci = evaluate_card(_snapshot(sales_count=0), as_of=AS_OF)
        assert ci.signal == "insufficient_data"
        assert ci.valuation_source is None
        assert ci.evidence_quality is None
        assert not any(code.startswith("bin_") for code in ci.reason_codes)

    def test_absent_sales_can_use_qualifying_bin_history(self):
        ci = evaluate_card(
            _snapshot(sales_count=0, sales_median=None, sales_trimmed_mean=None,
                      current_bin=7000, bin_captured_at=AS_OF - timedelta(minutes=10)),
            as_of=AS_OF,
            bin_observations=_bin_observations(price=10000),
        )
        assert ci.valuation_source == "bin_history"
        assert ci.evidence_quality == "estimated"
        assert ci.signal == "watch"
        assert ci.current_executable_buy is None
        assert ci.bin_sample_count == 10
        assert ci.bin_history_span_hours is not None
        assert ci.bin_reference_price is not None

    def test_stale_sales_can_use_qualifying_bin_history(self):
        stale_sale_at = AS_OF - timedelta(minutes=ENGINE_CONFIG.reject_sales_older_than_minutes + 60)
        ci = evaluate_card(
            _snapshot(
                current_bin=7000, bin_captured_at=AS_OF - timedelta(minutes=10),
                latest_sale_at=stale_sale_at,
            ),
            as_of=AS_OF,
            bin_observations=_bin_observations(price=10000),
        )
        assert ci.valuation_source == "bin_history"
        assert ci.evidence_quality == "estimated"

    def test_sparse_bin_history_is_rejected_and_stays_insufficient_data(self):
        ci = evaluate_card(
            _snapshot(sales_count=0, current_bin=7000,
                      bin_captured_at=AS_OF - timedelta(minutes=10)),
            as_of=AS_OF,
            bin_observations=_bin_observations(buckets=2, hours_apart=3),
        )
        assert ci.signal == "insufficient_data"
        assert ci.valuation_source is None
        assert rc.BIN_INSUFFICIENT_BUCKETS in ci.reason_codes

    def test_excessive_dispersion_never_triggers_bin_fallback(self):
        """Sales exist and are fresh but too scattered to trust - this is
        NOT the "unavailable or stale" case the fallback covers, so it
        must keep blocking outright even with perfectly good BIN history
        sitting right there."""
        ci = evaluate_card(
            _snapshot(sales_dispersion_ratio=0.9, current_bin=7000,
                      bin_captured_at=AS_OF - timedelta(minutes=10)),
            as_of=AS_OF,
            bin_observations=_bin_observations(price=10000),
        )
        assert ci.signal == "insufficient_data"
        assert ci.valuation_source is None
        assert not any(code.startswith("bin_") for code in ci.reason_codes)

    def test_bin_derived_confidence_is_capped_below_the_configured_ceiling(self):
        ci = evaluate_card(
            _snapshot(sales_count=0, current_bin=7000,
                      bin_captured_at=AS_OF - timedelta(minutes=1)),
            as_of=AS_OF,
            # A maximally clean series - long span, many buckets, dead
            # flat - to try to push confidence as high as possible.
            bin_observations=_bin_observations(buckets=40, hours_apart=6, price=10000),
        )
        assert ci.valuation_source == "bin_history"
        assert ci.confidence_score <= BIN_FALLBACK_CONFIG.confidence_cap

    def test_bin_derived_signal_is_never_strong_buy(self):
        ci = evaluate_card(
            _snapshot(sales_count=0, sales_median=None, sales_trimmed_mean=None,
                      current_bin=5000, bin_captured_at=AS_OF - timedelta(minutes=1)),
            as_of=AS_OF,
            # A huge apparent discount - if anything could push the
            # sales-path logic to strong_buy, an edge this large would.
            bin_observations=_bin_observations(buckets=40, hours_apart=6, price=20000),
        )
        assert ci.valuation_source == "bin_history"
        assert ci.signal in ("buy", "insufficient_data")
        assert ci.signal != "strong_buy"

    def test_bin_derived_risk_is_never_low(self):
        ci = evaluate_card(
            _snapshot(sales_count=0, current_bin=7000,
                      bin_captured_at=AS_OF - timedelta(minutes=1)),
            as_of=AS_OF,
            bin_observations=_bin_observations(buckets=40, hours_apart=6, price=10000),
        )
        assert ci.valuation_source == "bin_history"
        assert ci.risk_level != "low"

    def test_bin_derived_profit_floor_is_the_stricter_500_coins(self):
        """A cheap card clearing the 8% ROI floor but under the 500-coin
        absolute profit floor must still be rejected - see
        test_futgg_bin_valuation.py's own dedicated coverage of the
        formula; this just confirms evaluate_card() actually surfaces
        that rejection rather than falling through to some other path."""
        ci = evaluate_card(
            _snapshot(sales_count=0, sales_median=None, sales_trimmed_mean=None,
                      current_bin=700, bin_captured_at=AS_OF - timedelta(minutes=1)),
            as_of=AS_OF,
            bin_observations=_bin_observations(price=1000),
        )
        assert ci.signal == "insufficient_data"
        assert rc.BIN_INSUFFICIENT_PROFIT in ci.reason_codes

    def test_a_crash_inside_the_bin_fallback_never_propagates(self, monkeypatch):
        """Regression test for a live incident (2026-09-05): an unhandled
        exception in the BIN-fallback path - which runs for every card
        with insufficient/stale sales, an everyday case, not a rare one -
        propagated out of evaluate_card() and, with no per-card isolation
        in the router's scoring loop, emptied the entire Opportunities/
        dashboard feed for every user. evaluate_card()'s own documented
        contract is "never raises on missing/None fields"; this proves the
        fallback attempt itself can never violate that, regardless of
        what breaks inside it."""
        import app.services.futgg_intelligence as intelligence_module

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated unanticipated production data shape")

        monkeypatch.setattr(intelligence_module, "evaluate_bin_fallback", _boom)
        ci = evaluate_card(
            _snapshot(sales_count=0, sales_median=None, sales_trimmed_mean=None,
                      current_bin=7000, bin_captured_at=AS_OF - timedelta(minutes=1)),
            as_of=AS_OF,
            bin_observations=_bin_observations(price=10000),
        )
        assert ci.signal == "insufficient_data"
        assert ci.valuation_source is None
