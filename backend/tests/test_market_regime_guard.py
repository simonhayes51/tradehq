from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.futgg_intelligence import CardIntelligence
from app.services.market_regime_guard import (
    REGIME_NORMAL,
    REGIME_REPRICING,
    apply_regime_guard,
    evaluate_regime,
    fail_closed_regime_guard,
)

NOW = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)


def _sales(prices, step_minutes=2):
    start = NOW - timedelta(minutes=step_minutes * (len(prices) - 1))
    return [
        {"sold_price": price, "approximate_sold_at": start + timedelta(minutes=i * step_minutes)}
        for i, price in enumerate(prices)
    ]


def test_waterfall_sales_series_is_repricing():
    prices = [8_500_000] * 4 + [7_500_000] * 4 + [5_500_000] * 4 + [3_800_000] * 4 + [2_300_000] * 4
    assessment = evaluate_regime(
        {
            "current_bin": 2_124_000,
            "sales_median": 3_800_000,
            "sales_count": 20,
            "sales_window_span_minutes": 38,
            "sales_dispersion_ratio": 0.10,
        },
        _sales(prices),
    )
    assert assessment.state == REGIME_REPRICING
    assert assessment.drop_pct is not None
    assert assessment.drop_pct > 0.5


def test_ronaldo_shape_dense_sales_and_median_gap_is_repricing_without_raw_series():
    assessment = evaluate_regime(
        {
            "current_bin": 2_124_000,
            "sales_median": 2_350_000,
            "sales_count": 50,
            "sales_window_span_minutes": 18,
            "sales_dispersion_ratio": 0.10,
        }
    )
    assert assessment.state == REGIME_REPRICING
    assert assessment.sales_count == 50
    assert assessment.current_vs_median_pct < -0.09


def test_high_liquidity_stable_market_is_not_mistaken_for_crash():
    prices = [2_300_000, 2_310_000, 2_290_000, 2_305_000] * 5
    assessment = evaluate_regime(
        {
            "current_bin": 2_290_000,
            "sales_median": 2_300_000,
            "sales_count": 20,
            "sales_window_span_minutes": 38,
            "sales_dispersion_ratio": 0.01,
        },
        _sales(prices),
    )
    assert assessment.state == REGIME_NORMAL


def test_guard_turns_buy_into_non_actionable_avoid():
    ci = CardIntelligence(
        card_id=1,
        signal="buy",
        status="active",
        risk_level="low",
        confidence_score=0.92,
        fair_value=Decimal("2297565"),
        theoretical_max_buy=Decimal("2100000"),
        recommended_buy_max=Decimal("2030000"),
        current_executable_buy=Decimal("2000000"),
        break_even_price=Decimal("2105264"),
        recommended_sell_target=Decimal("2300000"),
        expected_profit_after_tax=Decimal("155000"),
        expected_roi=Decimal("0.0775"),
        expected_profit_basis=Decimal("2000000"),
    )
    guarded = apply_regime_guard(
        ci,
        {
            "current_bin": 2_124_000,
            "sales_median": 2_350_000,
            "sales_count": 50,
            "sales_window_span_minutes": 18,
            "sales_dispersion_ratio": 0.10,
        },
    )
    assert guarded.signal == "avoid"
    assert guarded.risk_level == "avoid"
    assert guarded.confidence_score <= 0.35
    assert guarded.fair_value is None
    assert guarded.recommended_buy_max is None
    assert guarded.current_executable_buy is None
    assert guarded.recommended_sell_target is None
    assert guarded.expected_profit_after_tax is None
    assert "market_repricing" in guarded.blocking_codes


def test_guard_never_upgrades_non_buy_signal():
    ci = CardIntelligence(card_id=2, signal="watch", status="watch", confidence_score=0.8)
    guarded = apply_regime_guard(
        ci,
        {
            "current_bin": 100_000,
            "sales_median": 200_000,
            "sales_count": 50,
            "sales_window_span_minutes": 20,
            "sales_dispersion_ratio": 0.5,
        },
    )
    assert guarded.signal == "watch"
    assert guarded.confidence_score == 0.8


def test_guard_failure_suppresses_actionable_buy():
    ci = CardIntelligence(
        card_id=3,
        signal="buy",
        status="active",
        risk_level="low",
        confidence_score=0.9,
        fair_value=Decimal("100000"),
        recommended_buy_max=Decimal("90000"),
        current_executable_buy=Decimal("88000"),
        recommended_sell_target=Decimal("110000"),
        expected_profit_after_tax=Decimal("12000"),
    )
    guarded = fail_closed_regime_guard(ci, {"source_card_id": 3}, RuntimeError("boom"))
    assert guarded.signal == "avoid"
    assert guarded.status == "insufficient_data"
    assert guarded.recommended_buy_max is None
    assert guarded.current_executable_buy is None
    assert guarded.recommended_sell_target is None
    assert guarded.expected_profit_after_tax is None
    assert "market_repricing" in guarded.blocking_codes


def _actionable_buy(card_id=10):
    return CardIntelligence(
        card_id=card_id,
        signal="buy",
        status="active",
        risk_level="low",
        confidence_score=0.90,
        fair_value=Decimal("70000"),
        theoretical_max_buy=Decimal("62000"),
        recommended_buy_max=Decimal("60000"),
        current_executable_buy=Decimal("42000"),
        break_even_price=Decimal("44211"),
        recommended_sell_target=Decimal("68000"),
        expected_profit_after_tax=Decimal("22600"),
        expected_roi=Decimal("0.538"),
        expected_profit_basis=Decimal("42000"),
    )


def test_persistent_live_bin_level_blocks_stale_sales_median_buy():
    # Regression for the FC27 Undav/Harder failure: the live market has
    # already settled around 42k, but old completed sales still centre on 70k.
    # That is stale evidence, not 28k of upside.
    observations = [
        (42_000, NOW - timedelta(minutes=5 * i))
        for i in range(24)
    ]
    guarded = apply_regime_guard(
        _actionable_buy(10),
        {
            "source_card_id": 10,
            "current_bin": 42_000,
            "sales_median": 70_000,
            "sales_count": 50,
            "sales_window_span_minutes": 240,
            "sales_dispersion_ratio": 0.02,
        },
        [],
        observations,
        as_of=NOW,
    )
    assert guarded.signal == "avoid"
    assert guarded.risk_level == "avoid"
    assert guarded.recommended_buy_max is None
    assert guarded.expected_profit_after_tax is None
    assert guarded.trend_features["regime_bin_market_state"] == "STABLE"
    assert "market_repricing" in guarded.blocking_codes


def test_established_bin_downmove_blocks_buy_before_sales_median_catches_up():
    observations = []
    # Older level around 100k, then a persistent new 80k level in the last hour.
    for i in range(12):
        observations.append((100_000, NOW - timedelta(minutes=180 - i * 10)))
    for i in range(12):
        observations.append((80_000, NOW - timedelta(minutes=55 - i * 5)))

    guarded = apply_regime_guard(
        _actionable_buy(11),
        {
            "source_card_id": 11,
            "current_bin": 80_000,
            "sales_median": 100_000,
            "sales_count": 50,
            "sales_window_span_minutes": 240,
            "sales_dispersion_ratio": 0.02,
        },
        [],
        observations,
        as_of=NOW,
    )
    assert guarded.signal == "avoid"
    assert guarded.trend_features["regime_bin_market_state"] == "MOVING_DOWN"
    assert guarded.recommended_sell_target is None


def test_single_cheap_bin_undercut_does_not_kill_a_real_snipe():
    observations = [
        (70_000, NOW - timedelta(minutes=5 * i))
        for i in range(1, 20)
    ]
    observations.append((42_000, NOW))

    ci = _actionable_buy(12)
    guarded = apply_regime_guard(
        ci,
        {
            "source_card_id": 12,
            "current_bin": 42_000,
            "sales_median": 70_000,
            "sales_count": 50,
            "sales_window_span_minutes": 240,
            "sales_dispersion_ratio": 0.02,
        },
        [],
        observations,
        as_of=NOW,
    )
    assert guarded.signal == "buy"
    assert guarded.recommended_buy_max == Decimal("60000")
