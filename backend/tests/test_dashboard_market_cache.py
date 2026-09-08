from __future__ import annotations

import asyncio
import inspect

import pytest

from app.services import dashboard_market_cache as cache


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def reset_cache_state(monkeypatch):
    monkeypatch.setattr(cache, "_pairs", None)
    monkeypatch.setattr(cache, "_cached_at", 0.0)
    monkeypatch.setattr(cache, "_last_attempt_at", 0.0)
    monkeypatch.setattr(cache, "_cache_source", "empty")
    monkeypatch.setattr(cache, "_refresh_task", None)
    monkeypatch.setattr(cache, "WARMING_RETRY_SECONDS", 60.0)


def _pair(card_id):
    return ({"source_card_id": card_id}, object())


def test_low_persisted_coverage_returns_warming_not_live_fallback(monkeypatch):
    async def partial(provider):
        return [_pair(1), _pair(2)], 0.20, 10

    monkeypatch.setattr(cache, "_load_persisted", partial)
    pairs, source = run(cache._compute(object()))

    assert source == "warming"
    assert len(pairs) == 2
    assert cache.cache_health()["live_fallback_enabled"] is False


def test_warming_never_promotes_partial_population_over_last_complete_cache(monkeypatch):
    complete = [_pair(1), _pair(2), _pair(3)]
    cache._pairs = complete
    cache._cached_at = -10_000.0

    async def partial(provider):
        return [_pair(9)], 0.10, 10

    monkeypatch.setattr(cache, "_load_persisted", partial)
    result = run(cache._refresh(object()))

    assert result is complete
    assert cache._pairs is complete
    assert cache._cache_source == "warming"


def test_cold_warming_state_is_retry_throttled(monkeypatch):
    calls = {"count": 0}

    async def partial(provider):
        calls["count"] += 1
        return [_pair(1)], 0.10, 10

    monkeypatch.setattr(cache, "_load_persisted", partial)

    assert run(cache.get_pairs(object())) == []
    assert run(cache.get_pairs(object())) == []
    assert calls["count"] == 1


def test_sufficient_persisted_coverage_becomes_live_cache(monkeypatch):
    persisted = [_pair(i) for i in range(6)]

    async def enough(provider):
        return persisted, 0.60, 10

    monkeypatch.setattr(cache, "_load_persisted", enough)
    result = run(cache._refresh(object()))

    assert result == persisted
    assert cache._pairs == persisted
    assert cache._cache_source == "persisted"


def test_module_contains_no_market_wide_live_scoring_fallback():
    source = inspect.getsource(cache)
    assert "_compute_live_fallback" not in source
    assert "market._sales_for_rows(provider" not in source
    assert "market._bin_observations_for_rows(provider" not in source
    assert "market._score_and_filter(" not in source
