"""Read scanner-persisted current FUT.GG intelligence for bulk surfaces.

Unlike futgg_recommendation_snapshots, futgg_current_intelligence contains one
mutable row for every successfully scanned card, including avoid and
insufficient_data states. This is therefore the right source for current product
surfaces that need the engine's latest state rather than historical assertions.

Safety: persisted intelligence is reusable only while the market snapshot's
current BIN still matches the BIN the engine evaluated. A price move excludes
that card until the scanner evaluates it again. Callers serving actionable
recommendations can additionally require an unexpired evaluation, preserving the
same fail-closed lifecycle rule the older recommendation-snapshot reader used.
"""
from __future__ import annotations

from typing import Iterable, Optional

from app.services.snapshot_market_surfaces import _to_pair


_BASE_QUERY = """
SELECT
    m.*,
    c.evaluated_at                AS rec_evaluated_at,
    c.current_bin                 AS rec_current_bin,
    c.price_age_minutes           AS rec_price_age_minutes,
    c.sales_sample_size           AS rec_sales_count,
    c.sales_window_span_minutes   AS rec_sales_window_span_minutes,
    c.fair_value                  AS rec_fair_value,
    c.theoretical_max_buy         AS rec_theoretical_max_buy,
    c.recommended_buy_max         AS rec_recommended_buy_max,
    c.current_executable_buy      AS rec_current_executable_buy,
    c.break_even_price            AS rec_break_even_price,
    c.recommended_sell_target     AS rec_recommended_sell_target,
    c.buy_below                   AS rec_buy_below,
    c.expected_profit_after_tax   AS rec_expected_profit_after_tax,
    c.expected_profit_basis       AS rec_expected_profit_basis,
    c.expected_roi                AS rec_expected_roi,
    c.confidence_score            AS rec_confidence_score,
    c.liquidity_score             AS rec_liquidity_score,
    c.risk_level                  AS rec_risk_level,
    c.signal                      AS rec_signal,
    c.status                      AS rec_status,
    c.trend_state                 AS rec_trend_state,
    c.trend_features              AS rec_trend_features,
    c.reason_codes                AS rec_reason_codes,
    c.blocking_codes              AS rec_blocking_codes,
    c.reasons                     AS rec_reasons,
    c.engine_version              AS rec_engine_version,
    c.trend_version               AS rec_trend_version,
    c.expires_at                  AS rec_expires_at,
    NULL::integer                 AS rec_expiry_minutes,
    c.valuation_source            AS rec_valuation_source,
    c.evidence_quality            AS rec_evidence_quality,
    c.bin_sample_count            AS rec_bin_sample_count,
    c.bin_history_span_hours      AS rec_bin_history_span_hours,
    c.bin_reference_price         AS rec_bin_reference_price,
    c.bin_trend_direction         AS rec_bin_trend_direction,
    c.bin_volatility              AS rec_bin_volatility,
    c.evidence_age_minutes        AS rec_evidence_age_minutes
FROM futgg_current_intelligence c
JOIN futgg_market_snapshot m ON m.source_card_id = c.source_card_id AND m.game_year = c.game_year
WHERE m.is_tradeable IS DISTINCT FROM FALSE
  AND m.current_bin IS NOT NULL
  AND m.current_bin = c.current_bin
"""


async def load_current_intelligence_pairs(
    provider,
    *,
    card_ids: Optional[Iterable[int]] = None,
    max_age_minutes: Optional[int] = None,
    require_unexpired: bool = False,
):
    """Return current market-row + persisted intelligence pairs.

    `card_ids` lets callers preserve an existing candidate-universe contract
    without re-scoring it. `max_age_minutes=None` means use the latest known
    state regardless of age. `require_unexpired=True` is for actionable product
    surfaces: an evaluation whose own lifecycle deadline has passed is excluded
    even if the BIN has not moved yet.
    """
    clauses = []
    args = []
    next_arg = 1

    ids = None
    if card_ids is not None:
        ids = sorted({int(card_id) for card_id in card_ids})
        if not ids:
            return []
        clauses.append(f"c.source_card_id = ANY(${next_arg}::bigint[])")
        args.append(ids)
        next_arg += 1

    if max_age_minutes is not None:
        clauses.append(
            f"c.evaluated_at >= now() - (${next_arg}::int * interval '1 minute')"
        )
        args.append(max(1, int(max_age_minutes)))
        next_arg += 1

    if require_unexpired:
        clauses.append("(c.expires_at IS NULL OR c.expires_at > now())")
        clauses.append("c.status NOT IN ('expired', 'invalidated')")

    query = _BASE_QUERY
    if clauses:
        query += " AND " + " AND ".join(clauses)
    query += " ORDER BY c.evaluated_at DESC, c.source_card_id"

    async with provider._pool.acquire() as conn:
        records = await conn.fetch(query, *args)
    return [_to_pair(record) for record in records]
