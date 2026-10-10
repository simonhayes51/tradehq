"""Regression guards for the October SalesSync throttle incident.

A clean batch must not automatically accelerate toward the upstream
rate-limit wall when default recovery is disabled.
"""
import ast
from pathlib import Path

TEXT = (Path(__file__).resolve().parents[1] / "futgg_sales_sync.py").read_text()


def test_default_sustainable_throttle_and_longer_cycle_pause():
    ast.parse(TEXT)
    assert 'FUTGG_SALES_SUSTAINABLE_MAX_BATCH", 6' in TEXT
    assert 'FUTGG_SALES_SUSTAINABLE_MIN_DELAY", 20.0' in TEXT
    assert 'FUTGG_SALES_THROTTLE_AUTO_RECOVERY", False' in TEXT
    assert 'FUTGG_SALES_CLEAN_CYCLE_COOLDOWN_SECONDS", 900.0' in TEXT


def test_persisted_throttle_cannot_override_effective_bounds():
    snippet = TEXT[TEXT.index("class AdaptiveThrottle:"):TEXT.index("#: AdaptiveThrottle only ever lives")]
    assert "min(batch_size, SUSTAINABLE_MAX_BATCH)" in snippet
    assert "max(delay, SUSTAINABLE_MIN_DELAY)" in snippet
    assert "if resume_batch_size is not None else self.max_batch" in snippet
    assert "if resume_delay is not None else self.min_delay" in snippet
    assert "if not THROTTLE_AUTO_RECOVERY:" in snippet
    assert "if throttled:" in snippet
