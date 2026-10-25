"""Access-control checks for the private FLIPIQ service feed."""
import asyncio

import pytest
from fastapi import HTTPException

from app.routers.v2 import flipiq_market


def test_route_is_registered_under_v2():
    from app.routers.v2 import router

    assert "/api/v2/market/flipiq-opportunities" in {route.path for route in router.routes}


def _invoke(token):
    return asyncio.run(flipiq_market.flipiq_opportunities(
        min_profit=500, page=1, page_size=25,
        service_token=token, provider=object(),
    ))


def test_service_route_requires_configuration(monkeypatch):
    monkeypatch.delenv("FLIPIQ_SERVICE_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        _invoke("arbitrary")
    assert exc.value.status_code == 503


def test_service_route_rejects_incorrect_token(monkeypatch):
    monkeypatch.setenv("FLIPIQ_SERVICE_TOKEN", "a" * 64)
    with pytest.raises(HTTPException) as exc:
        _invoke("incorrect")
    assert exc.value.status_code == 401


def test_service_route_reuses_live_market_checks(monkeypatch):
    monkeypatch.setenv("FLIPIQ_SERVICE_TOKEN", "a" * 64)
    observed = {}

    async def candidates(provider):
        return [{"source_card_id": 42}]

    async def sales(provider, rows):
        return {}

    async def bin_history(provider, rows):
        return {}

    async def score(rows, **kwargs):
        observed.update(kwargs)
        return []

    monkeypatch.setattr(flipiq_market, "_segmented_candidates", candidates)
    monkeypatch.setattr(flipiq_market, "_apply_filters_in_python", lambda rows, filters: rows)
    monkeypatch.setattr(flipiq_market, "_sales_for_rows", sales)
    monkeypatch.setattr(flipiq_market, "_bin_observations_for_rows", bin_history)
    monkeypatch.setattr(flipiq_market, "_score_and_filter", score)

    result = _invoke("a" * 64)
    assert result["items"] == []
    assert result["scanner_candidates_considered"] == 1
    assert observed["require_confirmed_sales"] is True
    assert observed["signals"] == {"buy", "strong_buy"}
    assert observed["min_expected_profit"] == 500
    assert "bin_by_card" in observed
