"""Empirical exit-quality evidence for one FUT.GG recommendation.

Fair value answers "what is this worth from current evidence?". Exit quality
answers a different question: "how often did sufficiently similar historical
calls actually reach their recommended sell target, and how long did it take?"

No synthetic 0-100 score is produced. The output is an observed target-hit rate,
a Wilson 95% interval, sample size, time-to-target and MAE/MFE, with explicit
cohort specificity. If history is too thin, the honest result is insufficient.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.services.futgg_calibration import wilson_interval
from app.services.futgg_config import ENGINE_VERSION, GRADER_VERSION
from app.services.futgg_recommendation_store import VALID_ENTRY_PRICE

MIN_COHORT_SAMPLE = 30
DEFAULT_HORIZON = "24h"
DEFAULT_WINDOW_DAYS = 90


@dataclass(frozen=True)
class ExitCohort:
    liquidity_bucket: str
    target_distance_bucket: str
    trend_state: str
    price_tier: str


def liquidity_bucket(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 0.30:
        return "<0.30"
    if value < 0.45:
        return "0.30-0.45"
    if value < 0.60:
        return "0.45-0.60"
    if value < 0.75:
        return "0.60-0.75"
    return "0.75+"


def target_distance_bucket(entry: Optional[float], target: Optional[float]) -> str:
    if entry is None or target is None or entry <= 0 or target <= 0:
        return "unknown"
    distance = (target - entry) / entry
    if distance < 0.03:
        return "<3%"
    if distance < 0.05:
        return "3-5%"
    if distance < 0.08:
        return "5-8%"
    if distance < 0.12:
        return "8-12%"
    return "12%+"


def current_cohort(row: Dict[str, Any], ci: Any) -> ExitCohort:
    entry = ci.current_executable_buy
    if entry is None:
        entry = ci.expected_profit_basis or ci.recommended_buy_max
    return ExitCohort(
        liquidity_bucket=liquidity_bucket(ci.liquidity_score),
        target_distance_bucket=target_distance_bucket(
            float(entry) if entry is not None else None,
            float(ci.recommended_sell_target) if ci.recommended_sell_target is not None else None,
        ),
        trend_state=str(ci.trend_state or "unknown"),
        price_tier=str(row.get("price_tier") or "unknown"),
    )


_QUERY = f"""
WITH historical AS MATERIALIZED (
    SELECT
        CASE
            WHEN s.liquidity_score IS NULL THEN 'unknown'
            WHEN s.liquidity_score < 0.30 THEN '<0.30'
            WHEN s.liquidity_score < 0.45 THEN '0.30-0.45'
            WHEN s.liquidity_score < 0.60 THEN '0.45-0.60'
            WHEN s.liquidity_score < 0.75 THEN '0.60-0.75'
            ELSE '0.75+'
        END AS liquidity_bucket,
        CASE
            WHEN s.current_executable_buy IS NULL OR s.current_executable_buy <= 0
              OR s.recommended_sell_target IS NULL OR s.recommended_sell_target <= 0
                THEN 'unknown'
            WHEN (s.recommended_sell_target - s.current_executable_buy) / s.current_executable_buy < 0.03 THEN '<3%'
            WHEN (s.recommended_sell_target - s.current_executable_buy) / s.current_executable_buy < 0.05 THEN '3-5%'
            WHEN (s.recommended_sell_target - s.current_executable_buy) / s.current_executable_buy < 0.08 THEN '5-8%'
            WHEN (s.recommended_sell_target - s.current_executable_buy) / s.current_executable_buy < 0.12 THEN '8-12%'
            ELSE '12%+'
        END AS target_distance_bucket,
        COALESCE(s.trend_state, 'unknown') AS trend_state,
        COALESCE(s.price_tier, 'unknown') AS price_tier,
        o.entry_achieved,
        o.target_hit,
        o.minutes_to_target,
        o.realised_roi,
        o.max_adverse_excursion,
        o.max_favourable_excursion
    FROM futgg_recommendation_outcomes o
    JOIN futgg_recommendation_snapshots s ON s.id = o.snapshot_id
    WHERE o.horizon = $1
      AND s.evaluated_at >= now() - ($2::int * interval '1 day')
      AND s.engine_version = $3
      AND o.grader_version = $4
      AND s.signal IN ('buy', 'strong_buy')
      AND o.outcome_status <> 'insufficient_observations'
      AND {VALID_ENTRY_PRICE}
), ranked_cohorts AS (
    SELECT 1 AS specificity, 'liquidity+target+trend+tier'::text AS cohort,
           * FROM historical
    WHERE liquidity_bucket = $5 AND target_distance_bucket = $6
      AND trend_state = $7 AND price_tier = $8
    UNION ALL
    SELECT 2, 'liquidity+target+trend', * FROM historical
    WHERE liquidity_bucket = $5 AND target_distance_bucket = $6 AND trend_state = $7
    UNION ALL
    SELECT 3, 'liquidity+target', * FROM historical
    WHERE liquidity_bucket = $5 AND target_distance_bucket = $6
    UNION ALL
    SELECT 4, 'liquidity', * FROM historical
    WHERE liquidity_bucket = $5
    UNION ALL
    SELECT 5, 'overall', * FROM historical
), aggregated AS (
    SELECT
        specificity,
        cohort,
        count(*) AS total,
        count(*) FILTER (WHERE entry_achieved) AS entered,
        count(*) FILTER (WHERE target_hit) AS target_hits,
        avg(realised_roi) FILTER (WHERE entry_achieved) AS avg_roi,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY minutes_to_target)
            FILTER (WHERE target_hit) AS median_minutes_to_target,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY max_adverse_excursion)
            FILTER (WHERE entry_achieved) AS median_mae,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY max_favourable_excursion)
            FILTER (WHERE entry_achieved) AS median_mfe
    FROM ranked_cohorts
    GROUP BY specificity, cohort
)
SELECT *
FROM aggregated
WHERE total >= $9
ORDER BY specificity
LIMIT 1
"""


async def exit_quality_for_card(
    pool,
    row: Dict[str, Any],
    ci: Any,
    *,
    horizon: str = DEFAULT_HORIZON,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_sample: int = MIN_COHORT_SAMPLE,
    engine_version: str = ENGINE_VERSION,
    grader_version: str = GRADER_VERSION,
) -> Dict[str, Any]:
    if horizon not in {"24h", "48h", "7d"}:
        raise ValueError("horizon must be one of 24h, 48h, 7d")

    cohort = current_cohort(row, ci)
    async with pool.acquire() as conn:
        result = await conn.fetchrow(
            _QUERY,
            horizon,
            max(1, int(window_days)),
            engine_version,
            grader_version,
            cohort.liquidity_bucket,
            cohort.target_distance_bucket,
            cohort.trend_state,
            cohort.price_tier,
            max(10, int(min_sample)),
        )

    current = {
        "liquidity_bucket": cohort.liquidity_bucket,
        "target_distance_bucket": cohort.target_distance_bucket,
        "trend_state": cohort.trend_state,
        "price_tier": cohort.price_tier,
    }
    if result is None:
        return {
            "status": "insufficient_history",
            "horizon": horizon,
            "window_days": int(window_days),
            "minimum_sample": max(10, int(min_sample)),
            "current_cohort": current,
            "message": "Not enough comparable graded recommendations yet to estimate exit performance reliably.",
        }

    total = int(result["total"] or 0)
    entered = int(result["entered"] or 0)
    hits = int(result["target_hits"] or 0)
    lo, hi = wilson_interval(hits, total)

    if total >= 100:
        evidence_strength = "strong"
    elif total >= 50:
        evidence_strength = "moderate"
    else:
        evidence_strength = "limited"

    return {
        "status": "available",
        "horizon": horizon,
        "window_days": int(window_days),
        "cohort": str(result["cohort"]),
        "current_cohort": current,
        "sample_size": total,
        "evidence_strength": evidence_strength,
        # All-call denominator is intentional: a recommendation whose entry was
        # rarely executable should not get an inflated exit statistic merely by
        # excluding the calls a user could not realistically enter.
        "entry_rate_pct": round(100.0 * entered / total, 2) if total else None,
        "target_hit_call_rate_pct": round(100.0 * hits / total, 2) if total else None,
        "target_hit_call_rate_ci95_pct": (
            [round(lo * 100.0, 2), round(hi * 100.0, 2)] if lo is not None and hi is not None else None
        ),
        "target_hit_after_entry_pct": round(100.0 * hits / entered, 2) if entered else None,
        "median_minutes_to_target": (
            round(float(result["median_minutes_to_target"]), 1)
            if result["median_minutes_to_target"] is not None else None
        ),
        "avg_realised_roi_pct": (
            round(float(result["avg_roi"]) * 100.0, 2) if result["avg_roi"] is not None else None
        ),
        "median_mae_pct": (
            round(float(result["median_mae"]) * 100.0, 2) if result["median_mae"] is not None else None
        ),
        "median_mfe_pct": (
            round(float(result["median_mfe"]) * 100.0, 2) if result["median_mfe"] is not None else None
        ),
        "engine_version": engine_version,
        "grader_version": grader_version,
        "interpretation": "Historical behaviour of comparable calls, not a guarantee or a forecast of this card.",
    }
