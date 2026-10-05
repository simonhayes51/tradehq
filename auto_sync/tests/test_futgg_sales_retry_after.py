"""Regression tests for FUT.GG sales retry-after and missing-data handling."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_sync as sales  # noqa: E402


def test_retry_after_delta_seconds_is_parsed_and_bounded():
    assert sales.parse_retry_after_seconds("90", now=datetime(2026, 1, 1, tzinfo=timezone.utc)) == 90
    assert sales.parse_retry_after_seconds("999999", now=datetime(2026, 1, 1, tzinfo=timezone.utc)) == sales.RETRY_AFTER_MAX_SECONDS
    assert sales.parse_retry_after_seconds("n/a") is None


def test_retry_after_http_date_is_parsed():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    retry_at = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
    assert sales.parse_retry_after_seconds(format_datetime(retry_at, usegmt=True), now=now) == 120


def test_404_remains_a_failure_but_does_not_retry_each_sales_cycle():
    assert sales.failure_backoff_minutes_for_error("data_404", 1) >= 180
    assert sales.failure_backoff_minutes_for_error("data_404", 4) >= 180


def test_429_card_backoff_respects_retry_after():
    assert sales.failure_backoff_minutes_for_error("data_429", 1, 901) >= 16
    assert sales.failure_backoff_minutes_for_error("data_429", 1, None) == sales.failure_backoff_minutes(1)


def test_global_cooldown_is_used_by_the_next_batch(monkeypatch):
    current = [100.0]
    monkeypatch.setattr(sales.time, "monotonic", lambda: current[0])
    throttle = sales.AdaptiveThrottle(8, 0.5)
    throttle.record(8, 1, retry_after_seconds=60)
    assert throttle.cooldown_remaining() == 60
    assert throttle.next_wait_seconds() == 60
    current[0] += 61
    assert throttle.cooldown_remaining() == 0
    assert throttle.next_wait_seconds() == throttle.delay
