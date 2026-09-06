# tests/test_futgg_segmented_candidates_cache.py
#
# Production incident (2026-09-06): a single page load fires
# /opportunities, /trade-finder/strategy/*, and /dashboard within about a
# second of each other, and _segmented_candidates() (app/routers/v2/
# futgg_market.py) had no cache - each call independently re-ran the full
# segmented SQL scan, measured at 2.7-4.6 seconds apiece, landing
# back-to-back on the same database. Combined with the sales/bin batch
# fetch and ~1700-row evaluate_card() loop on top, requests were tipping
# past the frontend's 8s timeout, surfacing as "Opportunities aren't
# available right now" - indistinguishable from the endpoint being down.
#
# These tests cover the short-lived cache added in response: concurrent
# and rapid-succession calls within the TTL must share one scan, and a
# call after the TTL expires must trigger a fresh one.
from __future__ import annotations

import asyncio

import pytest

import app.routers.v2.futgg_market as futgg_market_module
from app.routers.v2.futgg_market import _segmented_candidates


def run(coro):
    return asyncio.run(coro)


class FakeProvider:
    def __init__(self, rows):
        self.rows = rows
        self.call_count = 0

    async def select_candidates_by_segment(self):
        self.call_count += 1
        return {"all": list(self.rows)}


@pytest.fixture(autouse=True)
def reset_cache():
    futgg_market_module._segmented_candidates_cache = None
    futgg_market_module._segmented_candidates_cached_at = 0.0
    yield
    futgg_market_module._segmented_candidates_cache = None
    futgg_market_module._segmented_candidates_cached_at = 0.0


class TestSegmentedCandidatesCache:
    def test_a_second_call_within_the_ttl_reuses_the_cached_scan(self):
        provider = FakeProvider([{"source_card_id": 1}, {"source_card_id": 2}])
        first = run(_segmented_candidates(provider))
        second = run(_segmented_candidates(provider))
        assert provider.call_count == 1
        assert first == second

    def test_concurrent_calls_collapse_into_a_single_scan(self):
        # The exact production shape: several requests arriving at almost
        # the same instant (one dashboard page load firing multiple
        # endpoints) must not each trigger their own full scan.
        provider = FakeProvider([{"source_card_id": 1}])

        async def gather_five():
            return await asyncio.gather(*[_segmented_candidates(provider) for _ in range(5)])

        results = run(gather_five())
        assert provider.call_count == 1
        assert all(r == results[0] for r in results)

    def test_a_call_after_the_ttl_expires_triggers_a_fresh_scan(self, monkeypatch):
        provider = FakeProvider([{"source_card_id": 1}])
        run(_segmented_candidates(provider))
        assert provider.call_count == 1

        # Simulate the TTL having elapsed without sleeping in the test.
        futgg_market_module._segmented_candidates_cached_at -= (
            futgg_market_module._SEGMENTED_CANDIDATES_CACHE_SECONDS + 1
        )
        run(_segmented_candidates(provider))
        assert provider.call_count == 2

    def test_cache_holds_raw_rows_not_a_filtered_or_scored_result(self):
        # The cache must only ever short-circuit the SQL candidate scan -
        # never anything viewer-specific or evaluation-specific. Asserted
        # here by checking the cached value is exactly the row list, with
        # no added intelligence/score fields.
        rows = [{"source_card_id": 1, "current_bin": 100}]
        provider = FakeProvider(rows)
        result = run(_segmented_candidates(provider))
        assert result == rows
