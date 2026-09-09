from types import SimpleNamespace

from app.services.snapshot_surface_routes import (
    _filter_meaningful_opportunities,
    _pair_filter,
    _quality_opportunity_sort_key,
)


def _ci(*, signal="buy", profit=1000, roi=0.10, confidence=0.80):
    return SimpleNamespace(
        signal=signal,
        expected_profit_after_tax=profit,
        expected_roi=roi,
        confidence_score=confidence,
        liquidity_score=0.8,
        risk_level="low",
        valuation_source="completed_sales",
        evidence_quality="confirmed",
        evidence_age_minutes=10,
    )


def _floor(current_bin):
    return max(300, (current_bin or 0) * 0.015)


def test_headline_opportunities_drop_tiny_coin_profit_despite_huge_roi():
    cheap = ({"source_card_id": 1, "current_bin": 350}, _ci(profit=268, roi=0.764, confidence=0.82))
    useful = ({"source_card_id": 2, "current_bin": 50_000}, _ci(profit=3_000, roi=0.06, confidence=0.78))

    result = _filter_meaningful_opportunities([cheap, useful], _floor)

    assert [row["source_card_id"] for row, _ in result] == [2]


def test_price_relative_floor_keeps_real_five_figure_trade():
    premium = ({"source_card_id": 3, "current_bin": 505_000}, _ci(profit=46_000, roi=0.091, confidence=0.92))

    assert _filter_meaningful_opportunities([premium], _floor) == [premium]


def test_quality_sort_values_absolute_profit_before_roi_percentage():
    tiny_high_roi = ({"source_card_id": 1, "current_bin": 1_000}, _ci(profit=500, roi=0.50, confidence=0.95))
    useful_lower_roi = ({"source_card_id": 2, "current_bin": 100_000}, _ci(profit=10_000, roi=0.10, confidence=0.80))

    ranked = sorted([tiny_high_roi, useful_lower_roi], key=_quality_opportunity_sort_key)

    assert ranked[0][0]["source_card_id"] == 2


def test_watch_still_does_not_become_an_opportunity_just_for_high_confidence():
    watch = ({"source_card_id": 4, "current_bin": 500_000}, _ci(signal="watch", profit=40_000, confidence=0.99))
    buy = ({"source_card_id": 5, "current_bin": 500_000}, _ci(signal="buy", profit=20_000, confidence=0.75))

    result = _pair_filter([watch, buy], signals={"buy", "strong_buy"})

    assert [row["source_card_id"] for row, _ in result] == [5]
