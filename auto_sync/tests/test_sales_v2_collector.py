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
