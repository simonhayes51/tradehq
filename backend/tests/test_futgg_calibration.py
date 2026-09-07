from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services import futgg_calibration as cal


def run(coro):
    return asyncio.run(coro)


def test_wilson_interval_is_bounded_and_contains_observed_rate():
    lo, hi = cal.wilson_interval(70, 100)
    assert lo is not None and hi is not None
    assert 0.0 <= lo < 0.70 < hi <= 1.0


def test_two_proportion_test_rejects_large_real_difference():
    p = cal.two_proportion_p_value(80, 100, 55, 100)
    assert p is not None
    assert p < 0.01


def test_small_sample_never_becomes_review_candidate_even_with_big_delta():
    buckets = [
        {"bucket": "0.60-0.75", "sample_size": 10, "profitable_calls": 3},
        {"bucket": "0.75-0.85", "sample_size": 10, "profitable_calls": 9},
    ]
    out = cal._ordered_comparisons(
        "confidence", buckets, min_sample=30, alpha=0.05, min_effect_pp=5.0
    )
    assert len(out) == 1
    assert out[0]["statistically_significant"] is False
    assert out[0]["candidate_for_review"] is False


def test_material_and_significant_adjacent_difference_is_flagged_for_review():
    buckets = [
        {"bucket": "0.60-0.75", "sample_size": 120, "profitable_calls": 54},
        {"bucket": "0.75-0.85", "sample_size": 120, "profitable_calls": 78},
    ]
    out = cal._ordered_comparisons(
        "confidence", buckets, min_sample=30, alpha=0.05, min_effect_pp=5.0
    )
    assert len(out) == 1
    assert out[0]["profitable_call_rate_delta_pp"] == 20.0
    assert out[0]["statistically_significant"] is True
    assert out[0]["practically_meaningful"] is True
    assert out[0]["candidate_for_review"] is True
    assert out[0]["direction"] == "higher_bucket_better"


class _Conn:
    async def fetchrow(self, query, *args):
        return {
            "snapshots": 100,
            "config_fingerprints": 2,
            "fingerprints": ["aaa", "bbb"],
            "first_seen_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "last_seen_at": datetime(2026, 9, 7, tzinfo=timezone.utc),
        }

    async def fetch(self, query, *args):
        if "GROUP BY o.grader_version" in query:
            return [{"grader_version": cal.GRADER_VERSION, "outcomes": 100}]
        return [
            {
                "dimension": "overall",
                "bucket": "all",
                "total": 100,
                "entered": 90,
                "target_hits": 60,
                "profitable": 70,
                "avg_roi": 0.05,
                "median_roi": 0.04,
                "avg_minutes_to_target": 320,
                "avg_mae": -0.03,
                "avg_mfe": 0.08,
            }
        ]


class _Acquire:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


def test_mixed_configs_under_same_engine_version_block_calibration():
    report = run(cal.calibration_report(_Pool()))
    assert report["version_integrity"]["config_clean"] is False
    assert "multiple_engine_configs_share_version" in report["blockers"]
    assert report["calibration_allowed"] is False
    assert report["review_candidates"] == []
