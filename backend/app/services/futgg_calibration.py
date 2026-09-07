"""Outcome-backed calibration evidence for the FUT.GG intelligence engine.

This module never changes a production threshold. It answers the prerequisite
question: is there enough clean, version-consistent evidence to justify even
reviewing one?

Three guardrails are load-bearing:
  * only one engine_version and grader_version are analysed at a time;
  * a single engine_version must map to exactly one frozen engine_config JSON;
  * apparent bucket differences need sample size, a 95% interval, a two-sided
    proportion test and a minimum practical effect before being flagged.

That keeps "0.85 confidence won more than 0.70" from becoming a configuration
change when the observation is just small-sample noise or mixed versions.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.services.futgg_config import ENGINE_VERSION, GRADER_VERSION
from app.services.futgg_recommendation_store import VALID_ENTRY_PRICE

MIN_COMPARISON_SAMPLE = 30
SIGNIFICANCE_ALPHA = 0.05
MIN_PRACTICAL_EFFECT_PP = 5.0

ORDERED_BUCKETS: Dict[str, Tuple[str, ...]] = {
    "confidence": ("<0.45", "0.45-0.60", "0.60-0.75", "0.75-0.85", "0.85+"),
    "liquidity": ("<0.30", "0.30-0.45", "0.45-0.60", "0.60-0.75", "0.75+"),
    "expected_roi": ("<3%", "3-5%", "5-8%", "8-12%", "12%+"),
}

_BASE = f"""
WITH base AS MATERIALIZED (
    SELECT
        s.confidence_score,
        s.liquidity_score,
        s.expected_roi,
        COALESCE(s.risk_level, 'unknown') AS risk_level,
        COALESCE(s.trend_state, 'unknown') AS trend_state,
        COALESCE(s.price_tier, 'unknown') AS price_tier,
        s.signal,
        o.entry_achieved,
        o.target_hit,
        (o.entry_achieved AND o.realised_roi > 0) AS profitable,
        o.realised_roi,
        o.minutes_to_target,
        o.max_adverse_excursion,
        o.max_favourable_excursion
    FROM futgg_recommendation_outcomes o
    JOIN futgg_recommendation_snapshots s ON s.id = o.snapshot_id
    WHERE o.horizon = $1
      AND s.evaluated_at >= now() - $2::interval
      AND s.engine_version = $3
      AND o.grader_version = $4
      AND s.signal IN ('buy', 'strong_buy')
      AND o.outcome_status <> 'insufficient_observations'
      AND {VALID_ENTRY_PRICE}
), bucketed AS (
    SELECT *,
        CASE
            WHEN confidence_score IS NULL THEN 'unknown'
            WHEN confidence_score < 0.45 THEN '<0.45'
            WHEN confidence_score < 0.60 THEN '0.45-0.60'
            WHEN confidence_score < 0.75 THEN '0.60-0.75'
            WHEN confidence_score < 0.85 THEN '0.75-0.85'
            ELSE '0.85+'
        END AS confidence_bucket,
        CASE
            WHEN liquidity_score IS NULL THEN 'unknown'
            WHEN liquidity_score < 0.30 THEN '<0.30'
            WHEN liquidity_score < 0.45 THEN '0.30-0.45'
            WHEN liquidity_score < 0.60 THEN '0.45-0.60'
            WHEN liquidity_score < 0.75 THEN '0.60-0.75'
            ELSE '0.75+'
        END AS liquidity_bucket,
        CASE
            WHEN expected_roi IS NULL THEN 'unknown'
            WHEN expected_roi < 0.03 THEN '<3%'
            WHEN expected_roi < 0.05 THEN '3-5%'
            WHEN expected_roi < 0.08 THEN '5-8%'
            WHEN expected_roi < 0.12 THEN '8-12%'
            ELSE '12%+'
        END AS expected_roi_bucket
    FROM base
), dimensions AS (
    SELECT 'overall'::text AS dimension, 'all'::text AS bucket, * FROM bucketed
    UNION ALL SELECT 'confidence', confidence_bucket, * FROM bucketed
    UNION ALL SELECT 'liquidity', liquidity_bucket, * FROM bucketed
    UNION ALL SELECT 'expected_roi', expected_roi_bucket, * FROM bucketed
    UNION ALL SELECT 'risk_level', risk_level, * FROM bucketed
    UNION ALL SELECT 'trend_state', trend_state, * FROM bucketed
    UNION ALL SELECT 'price_tier', price_tier, * FROM bucketed
    UNION ALL SELECT 'signal', signal, * FROM bucketed
)
SELECT
    dimension,
    bucket,
    count(*) AS total,
    count(*) FILTER (WHERE entry_achieved) AS entered,
    count(*) FILTER (WHERE target_hit) AS target_hits,
    count(*) FILTER (WHERE profitable) AS profitable,
    avg(realised_roi) FILTER (WHERE entry_achieved) AS avg_roi,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY realised_roi)
        FILTER (WHERE entry_achieved) AS median_roi,
    avg(minutes_to_target) FILTER (WHERE target_hit) AS avg_minutes_to_target,
    avg(max_adverse_excursion) FILTER (WHERE entry_achieved) AS avg_mae,
    avg(max_favourable_excursion) FILTER (WHERE entry_achieved) AS avg_mfe
FROM dimensions
GROUP BY dimension, bucket
ORDER BY dimension, bucket
"""

_VERSION_CLEANLINESS = """
SELECT
    count(*) AS snapshots,
    count(DISTINCT md5(engine_config::text)) AS config_fingerprints,
    array_agg(DISTINCT md5(engine_config::text)) AS fingerprints,
    min(evaluated_at) AS first_seen_at,
    max(evaluated_at) AS last_seen_at
FROM futgg_recommendation_snapshots
WHERE engine_version = $1
  AND evaluated_at >= now() - $2::interval
  AND signal IN ('buy', 'strong_buy')
"""

_GRADER_VERSIONS = """
SELECT o.grader_version, count(*) AS outcomes
FROM futgg_recommendation_outcomes o
JOIN futgg_recommendation_snapshots s ON s.id = o.snapshot_id
WHERE s.engine_version = $1
  AND s.evaluated_at >= now() - $2::interval
  AND o.horizon = $3
  AND s.signal IN ('buy', 'strong_buy')
  AND o.outcome_status <> 'insufficient_observations'
GROUP BY o.grader_version
ORDER BY o.grader_version
"""


def _pct(n: int, d: int) -> Optional[float]:
    if not d:
        return None
    return round(100.0 * n / d, 2)


def wilson_interval(successes: int, total: int, *, z: float = 1.959963984540054) -> Tuple[Optional[float], Optional[float]]:
    """95% Wilson score interval as proportions, robust for modest samples."""
    if total <= 0:
        return None, None
    p = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    centre = (p + z2 / (2.0 * total)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def two_proportion_p_value(success_a: int, n_a: int, success_b: int, n_b: int) -> Optional[float]:
    """Two-sided pooled two-proportion z-test p-value, no scipy dependency."""
    if n_a <= 0 or n_b <= 0:
        return None
    pooled = (success_a + success_b) / (n_a + n_b)
    variance = pooled * (1.0 - pooled) * ((1.0 / n_a) + (1.0 / n_b))
    if variance <= 0:
        return 1.0
    z = ((success_a / n_a) - (success_b / n_b)) / math.sqrt(variance)
    return math.erfc(abs(z) / math.sqrt(2.0))


def _bucket(row: Mapping[str, Any], *, min_sample: int) -> Dict[str, Any]:
    total = int(row["total"] or 0)
    entered = int(row["entered"] or 0)
    target_hits = int(row["target_hits"] or 0)
    profitable = int(row["profitable"] or 0)
    lo, hi = wilson_interval(profitable, total)
    return {
        "bucket": str(row["bucket"]),
        "sample_size": total,
        "entered": entered,
        "entry_rate_pct": _pct(entered, total),
        "profitable_calls": profitable,
        # Primary calibration metric. This intentionally uses ALL calls as the
        # denominator, so an impressive profit rate among the few calls that
        # ever became executable cannot hide a poor entry/executability rate.
        "profitable_call_rate_pct": _pct(profitable, total),
        "profitable_call_rate_ci95_pct": (
            [round(lo * 100.0, 2), round(hi * 100.0, 2)] if lo is not None and hi is not None else None
        ),
        "target_hits": target_hits,
        "target_hit_call_rate_pct": _pct(target_hits, total),
        "profitable_rate_after_entry_pct": _pct(profitable, entered),
        "target_hit_rate_after_entry_pct": _pct(target_hits, entered),
        "avg_roi_pct": round(float(row["avg_roi"]) * 100.0, 2) if row["avg_roi"] is not None else None,
        "median_roi_pct": round(float(row["median_roi"]) * 100.0, 2) if row["median_roi"] is not None else None,
        "avg_minutes_to_target": round(float(row["avg_minutes_to_target"]), 1) if row["avg_minutes_to_target"] is not None else None,
        "avg_mae_pct": round(float(row["avg_mae"]) * 100.0, 2) if row["avg_mae"] is not None else None,
        "avg_mfe_pct": round(float(row["avg_mfe"]) * 100.0, 2) if row["avg_mfe"] is not None else None,
        "has_minimum_sample": total >= min_sample,
    }


def _ordered_comparisons(
    dimension: str,
    buckets: Sequence[Dict[str, Any]],
    *,
    min_sample: int,
    alpha: float,
    min_effect_pp: float,
) -> List[Dict[str, Any]]:
    order = ORDERED_BUCKETS.get(dimension)
    if not order:
        return []
    by_name = {b["bucket"]: b for b in buckets}
    comparisons: List[Dict[str, Any]] = []
    for lower_name, higher_name in zip(order, order[1:]):
        lower = by_name.get(lower_name)
        higher = by_name.get(higher_name)
        if lower is None or higher is None:
            continue
        n_low = int(lower["sample_size"])
        n_high = int(higher["sample_size"])
        s_low = int(lower["profitable_calls"])
        s_high = int(higher["profitable_calls"])
        p_value = two_proportion_p_value(s_high, n_high, s_low, n_low)
        low_rate = (100.0 * s_low / n_low) if n_low else 0.0
        high_rate = (100.0 * s_high / n_high) if n_high else 0.0
        delta = high_rate - low_rate
        enough = n_low >= min_sample and n_high >= min_sample
        significant = bool(enough and p_value is not None and p_value <= alpha)
        practical = abs(delta) >= min_effect_pp
        comparisons.append({
            "lower_bucket": lower_name,
            "higher_bucket": higher_name,
            "lower_sample": n_low,
            "higher_sample": n_high,
            "profitable_call_rate_delta_pp": round(delta, 2),
            "p_value": round(p_value, 6) if p_value is not None else None,
            "statistically_significant": significant,
            "practically_meaningful": practical,
            "candidate_for_review": bool(significant and practical),
            "direction": (
                "higher_bucket_better" if delta >= min_effect_pp
                else "lower_bucket_better" if delta <= -min_effect_pp
                else "no_material_difference"
            ),
        })
    return comparisons


async def calibration_report(
    pool,
    *,
    horizon: str = "24h",
    window_days: int = 90,
    engine_version: str = ENGINE_VERSION,
    grader_version: str = GRADER_VERSION,
    min_sample: int = MIN_COMPARISON_SAMPLE,
    alpha: float = SIGNIFICANCE_ALPHA,
    min_effect_pp: float = MIN_PRACTICAL_EFFECT_PP,
) -> Dict[str, Any]:
    if horizon not in {"24h", "48h", "7d"}:
        raise ValueError("horizon must be one of 24h, 48h, 7d")
    interval = timedelta(days=max(1, int(window_days)))
    min_sample = max(20, int(min_sample))

    async with pool.acquire() as conn:
        cleanliness = await conn.fetchrow(_VERSION_CLEANLINESS, engine_version, interval)
        graders = await conn.fetch(_GRADER_VERSIONS, engine_version, interval, horizon)
        rows = await conn.fetch(_BASE, horizon, interval, engine_version, grader_version)

    config_fingerprints = int(cleanliness["config_fingerprints"] or 0)
    config_clean = config_fingerprints <= 1
    available_graders = {
        str(row["grader_version"]): int(row["outcomes"] or 0)
        for row in graders
    }

    dimensions: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        dimensions.setdefault(str(row["dimension"]), []).append(_bucket(row, min_sample=min_sample))

    comparisons = {
        dimension: _ordered_comparisons(
            dimension,
            buckets,
            min_sample=min_sample,
            alpha=alpha,
            min_effect_pp=min_effect_pp,
        )
        for dimension, buckets in dimensions.items()
        if dimension in ORDERED_BUCKETS
    }

    review_candidates = [
        {"dimension": dimension, **comparison}
        for dimension, comps in comparisons.items()
        for comparison in comps
        if comparison["candidate_for_review"]
    ]

    blockers: List[str] = []
    if not config_clean:
        blockers.append("multiple_engine_configs_share_version")
    if grader_version not in available_graders:
        blockers.append("requested_grader_has_no_outcomes")
    overall = (dimensions.get("overall") or [{}])[0]
    if int(overall.get("sample_size") or 0) < min_sample:
        blockers.append("overall_sample_too_small")

    return {
        "engine_version": engine_version,
        "grader_version": grader_version,
        "horizon": horizon,
        "window_days": int(window_days),
        "statistical_policy": {
            "minimum_sample_per_bucket": min_sample,
            "alpha": alpha,
            "confidence_level_pct": round((1.0 - alpha) * 100.0, 1),
            "minimum_practical_effect_pp": min_effect_pp,
            "primary_metric": "profitable_call_rate_pct",
        },
        "version_integrity": {
            "config_clean": config_clean,
            "config_fingerprints": config_fingerprints,
            "fingerprints": list(cleanliness["fingerprints"] or []),
            "snapshots": int(cleanliness["snapshots"] or 0),
            "first_seen_at": cleanliness["first_seen_at"].isoformat() if cleanliness["first_seen_at"] else None,
            "last_seen_at": cleanliness["last_seen_at"].isoformat() if cleanliness["last_seen_at"] else None,
            "available_grader_versions": available_graders,
        },
        "calibration_allowed": not blockers,
        "blockers": blockers,
        "dimensions": dimensions,
        "ordered_comparisons": comparisons,
        # These are evidence prompts, never automatic config changes.
        "review_candidates": review_candidates if not blockers else [],
    }
