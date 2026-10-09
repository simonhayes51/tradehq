"""Regression guards for the production sales transport transition."""
from pathlib import Path

SYNC = Path(__file__).resolve().parents[1] / "futgg_sales_sync.py"
ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint-sales.sh"


def test_auto_fallback_skips_signed_fetch_hour_long_backoff():
    source = SYNC.read_text()
    assert 'transport == "signed_fetch"' in source
    assert 'current_transport() == "page_capture"' in source
    assert 'CYCLE_SLEEP if switched_to_capture' in source
    assert 'max(backoff_seconds, throttle.cooldown_remaining())' in source


def test_page_capture_breaker_still_has_normal_backoff():
    source = SYNC.read_text()
    assert 'else next_backoff_seconds(consecutive_cycle_trips) if breaker_tripped' in source


def test_xvfb_warning_filter_keeps_actual_errors():
    source = ENTRYPOINT.read_text()
    assert "^> Warning:.*Could not resolve keysym XF86" in source
    assert "2> >(grep -Ev" in source
