"""Ensure successful SalesSync cycles do not immediately exhaust shared quota."""
import ast
from pathlib import Path

TEXT = (Path(__file__).resolve().parents[1] / "futgg_sales_sync.py").read_text()


def test_clean_cycle_has_bounded_configurable_pacing():
    ast.parse(TEXT)
    assert 'FUTGG_SALES_CLEAN_CYCLE_COOLDOWN_SECONDS' in TEXT
    assert 'else max(CYCLE_SLEEP, CLEAN_CYCLE_COOLDOWN_SECONDS)' in TEXT


def test_breaker_still_uses_existing_cooldown_and_retry_after():
    assert 'next_backoff_seconds(consecutive_cycle_trips) if breaker_tripped' in TEXT
    assert 'max(backoff_seconds, throttle.cooldown_remaining())' in TEXT
    assert 'sleep_within_budget(' in TEXT
