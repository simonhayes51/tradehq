"""Mutable current-state persistence for every FUT.GG scanner evaluation.

This is intentionally NOT the historical recommendation store. The historical
futgg_recommendation_snapshots table is an immutable audit/Track Record corpus
and should remain limited to calls worth grading. This module owns one mutable
row per card representing what the engine thinks *now*, including
hold/sell/avoid/insufficient_data.

It also records genuine state transitions. A transition exists only when a card
already had current state and a newer evaluation changes signal or status. The
first population of a card is not a transition, which prevents a deployment or
backfill from fabricating thousands of "market changes".
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Sequence

from app.services.futgg_intelligence import CardIntelligence

log = logging.getLogger("futgg_current_intelligence_store")


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

_TRANSITION_INSERT = """
INSERT INTO futgg_intelligence_transitions (
    source_card_id, changed_at,
    previous_signal, new_signal, previous_status, new_status,
    current_bin, fair_value, expected_roi, confidence_score,
    risk_level, trend_state, reason_codes, blocking_codes, engine_version
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15
)
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


def _transition_args(old: Any, snapshot: dict[str, Any], ci: CardIntelligence):
    return (
        int(ci.card_id),
        ci.evaluated_at,
        old["signal"],
        ci.signal,
        old["status"],
        ci.status,
        snapshot.get("current_bin"),
        _num(ci.fair_value),
        _num(ci.expected_roi),
        ci.confidence_score,
        ci.risk_level,
        ci.trend_state,
        list(ci.reason_codes or []),
        list(ci.blocking_codes or []),
        ci.engine_version,
    )


async def upsert_many(pool, pairs: Sequence[tuple[dict[str, Any], CardIntelligence]]) -> int:
    """Replace current state, then best-effort record genuine state changes.

    Current intelligence is the authoritative product state and must not depend
    on the optional transition/audit layer being healthy. Transition writes are
    therefore deliberately outside the current-state transaction: if migration
    081 is missing or that insert fails, current state still advances and the
    fault is logged for observability.
    """
    if not pairs:
        return 0

    card_ids = [int(ci.card_id) for _, ci in pairs]
    transitions = []
    accepted = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            old_rows = await conn.fetch(
                """
                SELECT source_card_id, evaluated_at, signal, status
                FROM futgg_current_intelligence
                WHERE source_card_id = ANY($1::bigint[])
                """,
                card_ids,
            )
            old_by_card = {int(row["source_card_id"]): row for row in old_rows}

            # Count only rows whose timestamp is eligible to become current.
            # Initial rows are accepted; older out-of-order results are not.
            accepted_pairs = []
            for snapshot, ci in pairs:
                old = old_by_card.get(int(ci.card_id))
                if old is not None and (ci.evaluated_at is None or old["evaluated_at"] > ci.evaluated_at):
                    continue
                accepted_pairs.append((snapshot, ci))
                if old is not None and (old["signal"] != ci.signal or old["status"] != ci.status):
                    transitions.append(_transition_args(old, snapshot, ci))

            if accepted_pairs:
                await conn.executemany(
                    _UPSERT,
                    [_args(snapshot, ci) for snapshot, ci in accepted_pairs],
                )
                accepted = len(accepted_pairs)

    if transitions:
        try:
            async with pool.acquire() as conn:
                await conn.executemany(_TRANSITION_INSERT, transitions)
        except Exception:
            log.warning(
                "current intelligence advanced but transition logging failed for %d rows",
                len(transitions),
                exc_info=True,
            )

    return accepted
