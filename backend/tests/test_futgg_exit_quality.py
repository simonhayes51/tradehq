from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

from app.services import futgg_exit_quality as eq


def run(coro):
    return asyncio.run(coro)


def test_bucket_boundaries_are_explicit_and_stable():
    assert eq.liquidity_bucket(None) == "unknown"
    assert eq.liquidity_bucket(0.29) == "<0.30"
    assert eq.liquidity_bucket(0.30) == "0.30-0.45"
    assert eq.liquidity_bucket(0.75) == "0.75+"

    assert eq.target_distance_bucket(100, 102) == "<3%"
    assert eq.target_distance_bucket(100, 104) == "3-5%"
    assert eq.target_distance_bucket(100, 106) == "5-8%"
    assert eq.target_distance_bucket(100, 110) == "8-12%"
    assert eq.target_distance_bucket(100, 115) == "12%+"


def test_current_cohort_uses_executable_entry_before_fallback_prices():
    row = {"price_tier": "special"}
    ci = SimpleNamespace(
        liquidity_score=0.64,
        current_executable_buy=Decimal("100000"),
        expected_profit_basis=Decimal("90000"),
        recommended_buy_max=Decimal("95000"),
        recommended_sell_target=Decimal("108000"),
        trend_state="stable",
    )
    cohort = eq.current_cohort(row, ci)
    assert cohort.liquidity_bucket == "0.60-0.75"
    assert cohort.target_distance_bucket == "8-12%"
    assert cohort.trend_state == "stable"
    assert cohort.price_tier == "special"


class _Conn:
    def __init__(self, result):
        self.result = result
        self.args = None
        self.query = None

    async def fetchrow(self, query, *args):
        self.query = query
        self.args = args
        return self.result


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, result):
        self.conn = _Conn(result)

    def acquire(self):
        return _Acquire(self.conn)


def _ci():
    return SimpleNamespace(
        liquidity_score=0.70,
        current_executable_buy=Decimal("100000"),
        expected_profit_basis=Decimal("100000"),
        recommended_buy_max=Decimal("100000"),
        recommended_sell_target=Decimal("108000"),
        trend_state="stable",
    )


def test_thin_history_returns_insufficient_instead_of_fake_confidence():
    pool = _Pool(None)
    result = run(eq.exit_quality_for_card(pool, {"price_tier": "special"}, _ci()))
    assert result["status"] == "insufficient_history"
    assert result["exact_engine_config"] is True
    assert "exact engine configuration" in result["message"]
    assert "target_hit_call_rate_pct" not in result


def test_exit_quality_reports_observed_rate_interval_and_time_not_synthetic_score():
    pool = _Pool({
        "specificity": 2,
        "cohort": "liquidity+target+trend",
        "total": 100,
        "entered": 90,
        "target_hits": 68,
        "avg_roi": Decimal("0.071"),
        "median_minutes_to_target": 312.0,
        "median_mae": Decimal("-0.028"),
        "median_mfe": Decimal("0.104"),
    })
    result = run(eq.exit_quality_for_card(pool, {"price_tier": "special"}, _ci()))

    assert result["status"] == "available"
    assert result["sample_size"] == 100
    assert result["evidence_strength"] == "strong"
    assert result["entry_rate_pct"] == 90.0
    assert result["target_hit_call_rate_pct"] == 68.0
    assert result["target_hit_after_entry_pct"] == 75.56
    assert result["median_minutes_to_target"] == 312.0
    assert result["avg_realised_roi_pct"] == 7.1
    assert result["median_mae_pct"] == -2.8
    assert result["median_mfe_pct"] == 10.4
    assert result["exact_engine_config"] is True
    lo, hi = result["target_hit_call_rate_ci95_pct"]
    assert lo < 68.0 < hi
    assert "score" not in result
    assert "not a guarantee" in result["interpretation"]

    # Query is pinned to current engine/grader, exact frozen config and cohort.
    assert "s.engine_config = $5::jsonb" in pool.conn.query
    assert pool.conn.args[0] == "24h"
    assert json.loads(pool.conn.args[4]) == eq.ENGINE_CONFIG.as_dict()
    assert pool.conn.args[5] == "0.60-0.75"
    assert pool.conn.args[6] == "8-12%"


def test_explicit_config_override_is_passed_as_jsonb_not_just_version_label():
    pool = _Pool(None)
    custom = {"version": "futgg-2.3", "min_confidence_for_buy_signal": 0.8123}
    run(eq.exit_quality_for_card(
        pool,
        {"price_tier": "special"},
        _ci(),
        engine_version="futgg-2.3",
        engine_config=custom,
    ))
    assert json.loads(pool.conn.args[4]) == custom
    assert "s.engine_config = $5::jsonb" in pool.conn.query
