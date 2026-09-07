from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services import futgg_calibration_decisions as decisions


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self):
        self.args = None

    async def fetchrow(self, query, *args):
        self.args = args
        return {
            "id": 1,
            "decided_at": datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc),
            "parameter": args[0],
            "from_engine_version": args[1],
            "to_engine_version": args[2],
            "old_value": 0.45,
            "new_value": 0.60,
            "horizon": args[5],
            "window_days": args[6],
            "lower_bucket": args[7],
            "higher_bucket": args[8],
            "lower_sample": args[9],
            "higher_sample": args[10],
            "profitable_delta_pp": args[11],
            "p_value": args[12],
            "adjusted_alpha": args[13],
            "config_fingerprint": args[14],
            "rationale": args[15],
            "evidence": {},
            "status": args[17],
            "source_commit": args[18],
            "created_at": datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc),
        }

    async def fetch(self, query, *args):
        return []


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        return _Acquire(self.conn)


def test_decision_write_records_evidence_but_returns_no_engine_action():
    pool = _Pool()
    item = run(decisions.record_decision(
        pool,
        parameter="min_confidence_for_buy_signal",
        from_engine_version="futgg-2.3",
        to_engine_version="futgg-2.4",
        old_value=0.45,
        new_value=0.60,
        horizon="24h",
        window_days=90,
        lower_bucket="0.45-0.60",
        higher_bucket="0.60-0.75",
        lower_sample=120,
        higher_sample=140,
        profitable_delta_pp=8.2,
        p_value=0.002,
        adjusted_alpha=0.0042,
        config_fingerprint="abc123",
        rationale="Higher-confidence calls materially outperformed after correction.",
        evidence={"report": "snapshot"},
        status="accepted",
        source_commit="deadbeef",
    ))
    assert item["parameter"] == "min_confidence_for_buy_signal"
    assert item["status"] == "accepted"
    assert item["decided_at"].endswith("+00:00")
    assert pool.conn.args[0] == "min_confidence_for_buy_signal"


def test_decision_rejects_unknown_status_and_bad_horizon():
    pool = _Pool()
    with pytest.raises(ValueError):
        run(decisions.record_decision(
            pool,
            parameter="x",
            from_engine_version="futgg-2.3",
            rationale="test",
            status="deployed",
        ))
    with pytest.raises(ValueError):
        run(decisions.record_decision(
            pool,
            parameter="x",
            from_engine_version="futgg-2.3",
            rationale="test",
            horizon="30d",
        ))


def test_accepted_decision_must_bump_version_and_point_to_a_commit():
    pool = _Pool()
    with pytest.raises(ValueError, match="bump engine_version"):
        run(decisions.record_decision(
            pool,
            parameter="min_confidence_for_buy_signal",
            from_engine_version="futgg-2.3",
            to_engine_version="futgg-2.3",
            old_value=0.45,
            new_value=0.60,
            rationale="test",
            status="accepted",
            source_commit="deadbeef",
        ))
    with pytest.raises(ValueError, match="source_commit"):
        run(decisions.record_decision(
            pool,
            parameter="min_confidence_for_buy_signal",
            from_engine_version="futgg-2.3",
            to_engine_version="futgg-2.4",
            old_value=0.45,
            new_value=0.60,
            rationale="test",
            status="accepted",
        ))
