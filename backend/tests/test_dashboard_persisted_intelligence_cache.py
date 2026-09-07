from __future__ import annotations

import asyncio

from app.services import dashboard_market_cache as cache


def run(coro):
    return asyncio.run(coro)


def _reset():
    cache._pairs = None
    cache._cached_at = 0.0
    cache._cache_source = "empty"
    cache._refresh_task = None


def test_partial_prewarm_is_not_promoted_to_live_cache(monkeypatch):
    _reset()
    partial = [({"source_card_id": 1}, object())]

    async def fake_compute(provider, *, allow_fallback=True):
        assert allow_fallback is False
        return partial, "warming"

    monkeypatch.setattr(cache, "_compute", fake_compute)

    result = run(cache._refresh(object(), allow_fallback=False))
    assert result == []
    assert cache._pairs is None
    assert cache.cache_health()["source"] == "empty"


def test_sufficient_persisted_state_becomes_dashboard_source(monkeypatch):
    _reset()
    persisted = [({"source_card_id": 1}, object()), ({"source_card_id": 2}, object())]

    async def fake_compute(provider, *, allow_fallback=True):
        return persisted, "persisted"

    monkeypatch.setattr(cache, "_compute", fake_compute)

    result = run(cache._refresh(object(), allow_fallback=False))
    assert result == persisted
    assert cache._pairs == persisted
    assert cache.cache_health()["source"] == "persisted"
