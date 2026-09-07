"""Fast reader for user-facing market lists.

The background FUT.GG scanner already runs the expensive recommendation engine
and persists buy/strong_buy/watch conclusions to futgg_recommendation_snapshots.
The HTTP list endpoints must not immediately repeat that same 1,300-card Python
CPU pass while a user waits.

This module reads the most recent still-usable persisted conclusion for each
card and joins it to the *current* futgg_market_snapshot row for identity,
images and price metadata.

Safety rule: a persisted recommendation is only reusable while the live
current_bin is exactly the same as the current_bin it was evaluated against.
If the price moved, the row is excluded until the scanner evaluates it again.
That deliberately fails closed rather than showing a stale executable trade.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from app.services.futgg_intelligence import CardIntelligence

DEFAULT_MAX_AGE_MINUTES = int(os.getenv("FUTGG_SURFACE_SNAPSHOT_MAX_AGE_MINUTES", "30"))

_QUERY = """
WITH recent AS (
    SELECT DISTINCT ON (s.source_card_id)
        s.*
    FROM futgg_recommendation_snapshots s
    WHERE s.evaluated_at >= now() - ($1::int * interval '1 minute')
      AND s.signal IN ('buy', 'strong_buy', 'watch')
      AND s.invalidated_at IS NULL
      AND (s.expires_at IS NULL OR s.expires_at > now())
    ORDER BY s.source_card_id, s.evaluated_at DESC
)
SELECT
    m.*,
    s.evaluated_at              AS rec_evaluated_at,
    s.current_bin               AS rec_current_bin,
    s.price_age_minutes         AS rec_price_age_minutes,
    s.sales_count               AS rec_sales_count,
    s.sales_window_span_minutes AS rec_sales_window_span_minutes,
    s.fair_value                AS rec_fair_value,
    s.theoretical_max_buy       AS rec_theoretical_max_buy,
    s.recommended_buy_max       AS rec_recommended_buy_max,
    s.current_executable_buy    AS rec_current_executable_buy,
    s.break_even_price          AS rec_break_even_price,
    s.recommended_sell_target   AS rec_recommended_sell_target,
    s.buy_below                 AS rec_buy_below,
    s.expected_profit_after_tax AS rec_expected_profit_after_tax,
    s.expected_roi              AS rec_expected_roi,
    s.confidence_score          AS rec_confidence_score,
    s.liquidity_score           AS rec_liquidity_score,
    s.risk_level                AS rec_risk_level,
    s.signal                    AS rec_signal,
    s.status                    AS rec_status,
    s.trend_state               AS rec_trend_state,
    s.trend_features            AS rec_trend_features,
    s.reason_codes              AS rec_reason_codes,
    s.blocking_codes            AS rec_blocking_codes,
    s.reasons                   AS rec_reasons,
    s.engine_version            AS rec_engine_version,
    s.trend_version             AS rec_trend_version,
    s.expires_at                AS rec_expires_at,
    s.expiry_minutes            AS rec_expiry_minutes,
    s.valuation_source          AS rec_valuation_source,
    s.evidence_quality          AS rec_evidence_quality,
    s.bin_sample_count          AS rec_bin_sample_count,
    s.bin_history_span_hours    AS rec_bin_history_span_hours,
    s.bin_reference_price       AS rec_bin_reference_price,
    s.bin_trend_direction       AS rec_bin_trend_direction,
    s.bin_volatility            AS rec_bin_volatility,
    s.evidence_age_minutes      AS rec_evidence_age_minutes
FROM recent s
JOIN futgg_market_snapshot m ON m.source_card_id = s.source_card_id
WHERE m.is_tradeable IS DISTINCT FROM FALSE
  AND m.current_bin IS NOT NULL
  AND m.current_bin = s.current_bin
"""


def _jsonish(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def _current_price_age_minutes(row: Dict[str, Any]) -> int | None:
    observed = row.get("bin_checked_at") or row.get("bin_captured_at")
    if observed is None:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - observed).total_seconds() // 60))


def _current_evidence_age_minutes(row: Dict[str, Any], valuation_source: str | None) -> int | None:
    if valuation_source == "completed_sales":
        observed = row.get("latest_sale_at")
    else:
        observed = row.get("bin_checked_at") or row.get("bin_captured_at")
    if observed is None:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - observed).total_seconds() // 60))


def _to_pair(record: Any) -> Tuple[Dict[str, Any], CardIntelligence]:
    row = dict(record)
    reasons = _jsonish(row.get("rec_reasons"), [])
    trend_features = _jsonish(row.get("rec_trend_features"), {})
    signal_reasons: List[str] = []
    for reason in reasons if isinstance(reasons, list) else []:
        if isinstance(reason, dict):
            text = reason.get("message") or reason.get("text") or reason.get("reason")
            if text:
                signal_reasons.append(str(text))
        elif reason:
            signal_reasons.append(str(reason))

    valuation_source = row.get("rec_valuation_source")
    ci = CardIntelligence(
        card_id=int(row["source_card_id"]),
        fair_value=row.get("rec_fair_value"),
        theoretical_max_buy=row.get("rec_theoretical_max_buy"),
        recommended_buy_max=row.get("rec_recommended_buy_max"),
        current_executable_buy=row.get("rec_current_executable_buy"),
        break_even_price=row.get("rec_break_even_price"),
        recommended_sell_target=row.get("rec_recommended_sell_target"),
        buy_below=row.get("rec_buy_below"),
        expected_profit_after_tax=row.get("rec_expected_profit_after_tax"),
        expected_roi=row.get("rec_expected_roi"),
        liquidity_score=float(row["rec_liquidity_score"]) if row.get("rec_liquidity_score") is not None else None,
        confidence_score=float(row["rec_confidence_score"]) if row.get("rec_confidence_score") is not None else None,
        risk_level=row.get("rec_risk_level") or "high",
        signal=row.get("rec_signal") or "insufficient_data",
        status=row.get("rec_status") or "insufficient_data",
        signal_reasons=signal_reasons,
        reason_codes=list(row.get("rec_reason_codes") or []),
        blocking_codes=list(row.get("rec_blocking_codes") or []),
        reasons=reasons if isinstance(reasons, list) else [],
        price_age_minutes=_current_price_age_minutes(row),
        sales_sample_size=int(row.get("rec_sales_count") or 0),
        sales_window_span_minutes=float(row["rec_sales_window_span_minutes"]) if row.get("rec_sales_window_span_minutes") is not None else None,
        trend_state=row.get("rec_trend_state") or "insufficient_trend_data",
        trend_description="",
        trend_features=trend_features if isinstance(trend_features, dict) else {},
        engine_version=row.get("rec_engine_version") or "unknown",
        trend_version=row.get("rec_trend_version") or "unknown",
        evaluated_at=row.get("rec_evaluated_at"),
        expires_at=row.get("rec_expires_at"),
        expiry_minutes=row.get("rec_expiry_minutes"),
        evaluated_bin=row.get("rec_current_bin"),
        valuation_source=valuation_source,
        evidence_quality=row.get("rec_evidence_quality"),
        bin_sample_count=row.get("rec_bin_sample_count"),
        bin_history_span_hours=float(row["rec_bin_history_span_hours"]) if row.get("rec_bin_history_span_hours") is not None else None,
        bin_reference_price=row.get("rec_bin_reference_price"),
        bin_trend_direction=row.get("rec_bin_trend_direction"),
        bin_volatility=float(row["rec_bin_volatility"]) if row.get("rec_bin_volatility") is not None else None,
        evidence_age_minutes=_current_evidence_age_minutes(row, valuation_source),
    )
    return row, ci


async def load_market_surface_pairs(provider, *, max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES):
    """Return current-row + persisted-intelligence pairs for fast list surfaces."""
    async with provider._pool.acquire() as conn:
        records = await conn.fetch(_QUERY, max(1, int(max_age_minutes)))
    return [_to_pair(record) for record in records]
