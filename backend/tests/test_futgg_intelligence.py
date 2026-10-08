"""Price-history-first recommendation contract; no DB or network I/O."""
from datetime import datetime, timedelta, timezone

from app.services import trading_math as tm
from app.services import futgg_reasons as rc
from app.services.futgg_bin_valuation import BIN_FALLBACK_CONFIG
from app.services.futgg_intelligence import (
    DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES,
    evaluate_card,
)

AS_OF = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def _observations(*, buckets=10, price_fn=None, price=10000):
    return [
        (price_fn(i) if price_fn else price, AS_OF - timedelta(hours=(buckets - i) * 3))
        for i in range(buckets)
    ]


def _snapshot(**overrides):
    row = {
        "source_card_id": 1,
        "is_tradeable": True,
        "current_bin": 7500,
        "bin_captured_at": AS_OF - timedelta(minutes=5),
        # Deliberately plentiful sales data: recommendations must ignore it.
        "sales_count": 50,
        "sales_median": 20000,
        "sales_trimmed_mean": 20000,
        "sales_window_span_minutes": 30,
    }
    row.update(overrides)
    return row


def test_sales_data_alone_never_creates_recommendation():
    result = evaluate_card(_snapshot(), as_of=AS_OF, sales=None, bin_observations=None)
    assert result.signal == "insufficient_data"
    assert result.valuation_source is None


def test_price_history_can_create_estimated_buy_without_sales():
    result = evaluate_card(
        _snapshot(sales_count=0, sales_median=None, sales_trimmed_mean=None),
        as_of=AS_OF,
        sales=None,
        bin_observations=_observations(),
    )
    assert result.signal == "buy"
    assert result.valuation_source == "bin_history"
    assert result.evidence_quality == "estimated"
    assert result.sales_sample_size == 0
    assert result.liquidity_score is None
    assert rc.PRICE_ONLY_EVIDENCE in result.reason_codes
    assert result.current_executable_buy == 7500
    assert result.expected_roi >= BIN_FALLBACK_CONFIG.min_net_roi_for_buy
    assert result.expected_profit_after_tax == tm.net_profit(
        result.recommended_sell_target, result.recommended_buy_max
    )


def test_above_buy_ceiling_is_watch_not_insufficient_or_buy():
    result = evaluate_card(
        _snapshot(current_bin=10000),
        as_of=AS_OF,
        bin_observations=_observations(),
    )
    assert result.signal == "watch"
    assert result.valuation_source == "bin_history"
    assert result.evidence_quality == "estimated"
    assert result.current_executable_buy is None
    assert result.buy_below is not None
    assert rc.PRICE_ABOVE_MAX_BUY in result.reason_codes


def test_downtrending_bin_history_cannot_be_a_buy():
    result = evaluate_card(
        _snapshot(current_bin=7000),
        as_of=AS_OF,
        bin_observations=_observations(price_fn=lambda i: 12000 - i * 500),
    )
    assert result.signal != "buy"
    assert result.valuation_source is None
    assert any(code.startswith("bin_") for code in result.reason_codes)


def test_stale_current_bin_is_rejected_even_with_history():
    stale = AS_OF - timedelta(minutes=DEFAULT_MAX_ACCEPTABLE_PRICE_AGE_MINUTES + 1)
    result = evaluate_card(
        _snapshot(bin_captured_at=stale),
        as_of=AS_OF,
        bin_observations=_observations(),
    )
    assert result.signal == "insufficient_data"
    assert result.valuation_source is None


def test_price_only_signal_is_independent_of_sales_values():
    history = _observations()
    a = evaluate_card(_snapshot(sales_count=0), as_of=AS_OF, bin_observations=history)
    b = evaluate_card(
        _snapshot(sales_count=50, sales_median=500000, sales_trimmed_mean=500000),
        as_of=AS_OF,
        sales=[{"price": 500000}],
        bin_observations=history,
    )
    assert (a.signal, a.recommended_buy_max, a.recommended_sell_target) == (
        b.signal, b.recommended_buy_max, b.recommended_sell_target
    )
