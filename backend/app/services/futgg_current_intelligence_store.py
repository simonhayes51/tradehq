"""Mutable current-state persistence for every FUT.GG scanner evaluation.

This is intentionally NOT the historical recommendation store. The historical
futgg_recommendation_snapshots table is an immutable audit/Track Record corpus
and should remain limited to calls worth grading. This module owns one mutable
row per card representing what the engine thinks *now*, including
hold/sell/avoid/insufficient_data.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Sequence

from app.services.futgg_intelligence import CardIntelligence


def _num(value: Any):
    if isinstance(value, Decimal):
        return value
    return value


_UPSERT = """
INSERT INTO futgg_current_intelligence (
    source_card_id, evaluated_at, current_bin, signal, status,
    fair_value, theoretical_max_buy, recommended_buy_max,
    current_executable_buy, break_even_price, recommended_sell_target,
    buy_below, expected_profit_after_tax, expected_roi,
    confidence_score, liquidity_score, risk_level, trend_state,
    valuation_source, evidence_quality, evidence_age_minutes,
    price_age_minutes, sales_sample_size, sales_window_span_minutes,
    reason_codes, blocking_codes, reasons, trend_features,
    engine_version, trend_version, expires_at, evaluated_bin,
    bin_sample_count, bin_history_span_hours, bin_reference_price,
    bin_trend_direction, bin_volatility, updated_at
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
    $20,$21,$22,$23,$24,$25,$26,$27::jsonb,$28::jsonb,$29,$30,$31,$32,$33,
    $34,$35,$36,$37,now()
)
ON CONFLICT (source_card_id) DO UPDATE SET
    evaluated_at = EXCLUDED.evaluated_at,
    current_bin = EXCLUDED.current_bin,
    signal = EXCLUDED.signal,
    status = EXCLUDED.status,
    fair_value = EXCLUDED.fair_value,
    theoretical_max_buy = EXCLUDED.theoretical_max_buy,
    recommended_buy_max = EXCLUDED.recommended_buy_max,
    current_executable_buy = EXCLUDED.current_executable_buy,
    break_even_price = EXCLUDED.break_even_price,
    recommended_sell_target = EXCLUDED.recommended_sell_target,
    buy_below = EXCLUDED.buy_below,
    expected_profit_after_tax = EXCLUDED.expected_profit_after_tax,
    expected_roi = EXCLUDED.expected_roi,
    confidence_score = EXCLUDED.confidence_score,
    liquidity_score = EXCLUDED.liquidity_score,
    risk_level = EXCLUDED.risk_level,
    trend_state = EXCLUDED.trend_state,
    valuation_source = EXCLUDED.valuation_source,
    evidence_quality = EXCLUDED.evidence_quality,
    evidence_age_minutes = EXCLUDED.evidence_age_minutes,
    price_age_minutes = EXCLUDED.price_age_minutes,
    sales_sample_size = EXCLUDED.sales_sample_size,
    sales_window_span_minutes = EXCLUDED.sales_window_span_minutes,
    reason_codes = EXCLUDED.reason_codes,
    blocking_codes = EXCLUDED.blocking_codes,
    reasons = EXCLUDED.reasons,
    trend_features = EXCLUDED.trend_features,
    engine_version = EXCLUDED.engine_version,
    trend_version = EXCLUDED.trend_version,
    expires_at = EXCLUDED.expires_at,
    evaluated_bin = EXCLUDED.evaluated_bin,
    bin_sample_count = EXCLUDED.bin_sample_count,
    bin_history_span_hours = EXCLUDED.bin_history_span_hours,
    bin_reference_price = EXCLUDED.bin_reference_price,
    bin_trend_direction = EXCLUDED.bin_trend_direction,
    bin_volatility = EXCLUDED.bin_volatility,
    updated_at = now()
WHERE futgg_current_intelligence.evaluated_at <= EXCLUDED.evaluated_at
"""


def _args(snapshot: dict[str, Any], ci: CardIntelligence):
    return (
        int(ci.card_id),
        ci.evaluated_at,
        snapshot.get("current_bin"),
        ci.signal,
        ci.status,
        _num(ci.fair_value),
        _num(ci.theoretical_max_buy),
        _num(ci.recommended_buy_max),
        _num(ci.current_executable_buy),
        _num(ci.break_even_price),
        _num(ci.recommended_sell_target),
        _num(ci.buy_below),
        _num(ci.expected_profit_after_tax),
        _num(ci.expected_roi),
        ci.confidence_score,
        ci.liquidity_score,
        ci.risk_level,
        ci.trend_state,
        ci.valuation_source,
        ci.evidence_quality,
        ci.evidence_age_minutes,
        ci.price_age_minutes,
        ci.sales_sample_size,
        ci.sales_window_span_minutes,
        list(ci.reason_codes or []),
        list(ci.blocking_codes or []),
        json.dumps(ci.reasons or []),
        json.dumps(ci.trend_features or {}),
        ci.engine_version,
        ci.trend_version,
        ci.expires_at,
        ci.evaluated_bin,
        ci.bin_sample_count,
        ci.bin_history_span_hours,
        _num(ci.bin_reference_price),
        ci.bin_trend_direction,
        ci.bin_volatility,
    )


async def upsert_many(pool, pairs: Sequence[tuple[dict[str, Any], CardIntelligence]]) -> int:
    """Replace current state for each evaluated card in one DB acquisition.

    Returns the number of input evaluations attempted. A scanner pass should
    treat this as current-state bookkeeping, not as recommendation-history
    creation; the immutable recommendation store remains a separate write.
    """
    if not pairs:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(_UPSERT, [_args(snapshot, ci) for snapshot, ci in pairs])
    return len(pairs)
