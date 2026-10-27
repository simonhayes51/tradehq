"""Regression tests for observed historical recovery episodes."""
from datetime import datetime, timedelta, timezone

from app.services.flipiq_recovery import recovery_evidence


def test_no_history_is_not_a_prediction():
    assert recovery_evidence([])["recovery_rate"] is None


def test_recovery_measured_only_on_completed_nonoverlapping_windows():
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    base = now - timedelta(hours=72)
    values = [10000] * 73
    # A drop at hour 12, followed by a BIN rebound at hour 15.
    values[12:15] = [8000] * 3
    points = [(price, base + timedelta(hours=i)) for i, price in enumerate(values)]
    result = recovery_evidence(points, now=now, drop_pct=16)
    assert result["sample_size"] >= 1
    assert result["recovered"] >= 1
    assert result["recovery_rate"] is None if result["sample_size"] < 3 else result["recovery_rate"] >= 0


def test_right_censored_recent_drop_not_counted_as_failure():
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    values = [10000] * 12 + [8000] * 3
    points = [(v, now - timedelta(hours=14-i)) for i, v in enumerate(values)]
    result = recovery_evidence(points, now=now)
    assert result["sample_size"] == 0
    assert result["recovery_rate"] is None
