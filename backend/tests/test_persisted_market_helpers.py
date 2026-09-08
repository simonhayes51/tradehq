from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.services import persisted_market_helpers as helpers


def run(coro):
    return asyncio.run(coro)


def _ci(**overrides):
    values = {
        "signal": "buy",
        "risk_level": "low",
        "expected_profit_after_tax": Decimal("2500"),
        "expected_roi": Decimal("0.08"),
        "confidence_score": 0.82,
        "liquidity_score": 0.70,
        "valuation_source": "completed_sales",
        "evidence_quality": "confirmed",
        "evidence_age_minutes": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_segmented_compatibility_helper_carries_persisted_intelligence(monkeypatch):
    ci = _ci()

    async def load(provider):
        return [({"source_card_id": 42, "name": "Test"}, ci)]

    monkeypatch.setattr(helpers, "load_market_surface_pairs", load)
    rows = run(helpers.persisted_segmented_candidates(object()))

    assert len(rows) == 1
    assert rows[0]["source_card_id"] == 42
    assert rows[0][helpers._CI_KEY] is ci


def test_score_filter_preserves_old_filter_contract_and_returns_clean_rows():
    good = {"source_card_id": 1, helpers._CI_KEY: _ci()}
    low_conf = {"source_card_id": 2, helpers._CI_KEY: _ci(confidence_score=0.40)}
    wrong_signal = {"source_card_id": 3, helpers._CI_KEY: _ci(signal="watch")}

    result = run(helpers.persisted_score_and_filter(
        [good, low_conf, wrong_signal],
        signals={"buy", "strong_buy"},
        min_confidence=0.70,
        min_expected_profit=2000,
        min_roi=0.05,
        min_liquidity=0.60,
        require_confirmed_sales=True,
        max_evidence_age_minutes=60,
        sales_by_card={1: [{"ignored": True}]},
    ))

    assert len(result) == 1
    row, ci = result[0]
    assert row == {"source_card_id": 1}
    assert helpers._CI_KEY not in row
    assert ci.confidence_score == 0.82


def test_missing_persisted_intelligence_fails_closed_without_evaluation():
    result = run(helpers.persisted_score_and_filter(
        [{"source_card_id": 9}],
        signals={"buy"},
    ))
    assert result == []


def test_sales_compatibility_helper_never_loads_raw_sales():
    assert run(helpers.persisted_sales_for_rows(object(), [{"source_card_id": 1}])) == {}


def test_installer_replaces_only_futgg_market_bulk_helper_names(monkeypatch):
    import app.routers.v2.futgg_market as market
    import app.services.futgg_scanner as scanner

    scanner_run_scan = scanner.run_scan
    monkeypatch.setattr(market, "_segmented_candidates", object())
    monkeypatch.setattr(market, "_sales_for_rows", object())
    monkeypatch.setattr(market, "_score_and_filter", object())

    helpers.install_persisted_market_helpers()

    assert market._segmented_candidates is helpers.persisted_segmented_candidates
    assert market._sales_for_rows is helpers.persisted_sales_for_rows
    assert market._score_and_filter is helpers.persisted_score_and_filter
    assert scanner.run_scan is scanner_run_scan
