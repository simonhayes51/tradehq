"""Safety and selection wiring tests for V2 entrypoint."""
import pytest
import sales_v2_collector as v2
import futgg_sales_sync as sales


def test_fetch_opt_in_required(monkeypatch):
    monkeypatch.delenv("SALES_V2_ENABLE_FETCH", raising=False)
    with pytest.raises(SystemExit, match="fetch disabled"):
        v2.configure()


def test_writes_disabled_by_default(monkeypatch):
    monkeypatch.setenv("SALES_V2_ENABLE_FETCH", "true")
    monkeypatch.delenv("SALES_V2_ALLOW_WRITES", raising=False)
    monkeypatch.setattr(v2, "MIN_BIN", 5000)
    monkeypatch.setattr(v2, "TARGET_INTERVAL_MINUTES", 60)
    monkeypatch.setattr(sales, "APPLY", True)
    monkeypatch.setattr(sales, "SALES_INTERVALS", {
        "CRITICAL": 180, "HIGH": 720, "NORMAL": 1440
    })
    v2.configure()
    assert sales.APPLY is False
    assert sales.select_due is v2.select_market_due
    assert sales.report_coverage is v2.report_market_coverage
    assert set(sales.SALES_INTERVALS.values()) == {60}


def test_min_interval_and_min_bin_guardrails(monkeypatch):
    monkeypatch.setenv("SALES_V2_ENABLE_FETCH", "true")
    monkeypatch.setattr(v2, "MIN_BIN", 0)
    with pytest.raises(SystemExit, match="MIN_BIN"):
        v2.configure()
    monkeypatch.setattr(v2, "MIN_BIN", 5000)
    monkeypatch.setattr(v2, "TARGET_INTERVAL_MINUTES", 30)
    with pytest.raises(SystemExit, match="TARGET_MINUTES"):
        v2.configure()



def test_guarded_recovery_requires_sustained_clean_requests(monkeypatch):
    monkeypatch.setattr(v2, "RECOVERY_SUCCESS_REQUESTS", 3)
    monkeypatch.setattr(v2, "RECOVERY_DELAY_STEP_SECONDS", 5.0)
    worker = v2.GuardedRecoveryThrottle(
        batch_size=8, delay=20.0, resume_batch_size=1, resume_delay=80.0
    )
    assert worker.batch_size == worker.max_batch == 1
    worker.record(1, 0)
    worker.record(1, 0)
    assert worker.delay == 80.0
    worker.record(1, 0)
    assert worker.delay == 75.0


def test_throttle_error_resets_recovery_and_preserves_backoff(monkeypatch):
    monkeypatch.setattr(v2, "RECOVERY_SUCCESS_REQUESTS", 3)
    worker = v2.GuardedRecoveryThrottle(
        batch_size=8, delay=20.0, resume_batch_size=1, resume_delay=40.0
    )
    worker.record(1, 0)
    worker.record(1, 0)
    worker.record(1, 1)
    assert worker.delay == 80.0
    worker.record(1, 0)
    assert worker.delay == 80.0
    worker.record(1, 0)
    assert worker.delay == 80.0
    worker.record(1, 0)
    assert worker.delay == 75.0


def test_probe_stops_acceleration_after_first_throttle(monkeypatch):
    monkeypatch.setattr(v2, "PROBE_MODE", True)
    monkeypatch.setattr(v2, "RECOVERY_SUCCESS_REQUESTS", 30)
    monkeypatch.setattr(v2, "RECOVERY_DELAY_STEP_SECONDS", 10.0)
    worker = v2.GuardedRecoveryThrottle(
        batch_size=8, delay=20, resume_batch_size=1, resume_delay=80
    )
    for _ in range(30):
        worker.record(1, 0)
    assert worker.delay == 70
    worker.record(1, 1)
    assert worker._probe_stopped
    backed_off = worker.delay
    for _ in range(100):
        worker.record(1, 0)
    assert worker.delay == backed_off
