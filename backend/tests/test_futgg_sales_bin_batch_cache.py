# tests/test_futgg_sales_bin_batch_cache.py
#
# Production incident (2026-09-06): Coolify's Postgres metrics showed a
# >200% CPU spike lining up exactly with one page load's burst of
# requests (dashboard, /opportunities, /trade-finder, a Strategy Finder
# tab, /track-record, all within ~15 seconds) - with NO matching
# "futgg_market_snapshot refresh took Xs" warning in that window, ruling
# out the (separately tracked, separately slow) materialized view refresh
# as the cause. _segmented_candidates() and evaluate_card() were already
# cached by this point, but _sales_for_rows()/_bin_observations_for_rows()
# were not - every one of those calls independently re-queried Postgres
# for sales/BIN history across the full candidate set (up to ~1700 cards),
# even for cards whose evaluate_card() result was already a cache hit and
# never needed that data at all.
#
# These tests cover the short-lived cache added in response, keyed by the
# actual requested card_id set rather than a bare TTL - a cache built from
# the full candidate list must still serve a caller that only asks for a
# subset of it, without ever serving a card that was never fetched.
from __future__ import annotations

import asyncio

import pytest

import app.routers.v2.futgg_market as futgg_market_module
from app.routers.v2.futgg_market import _bin_observations_for_rows, _sales_for_rows


def run(coro):
    return asyncio.run(coro)


class FakeProvider:
    def __init__(self):
        self.sales_calls = []
        self.bin_calls = []

    async def get_sales_by_ids(self, card_ids):
        self.sales_calls.append(sorted(card_ids))
        return {cid: [f"sale-{cid}"] for cid in card_ids}

    async def get_bin_observations_by_ids(self, card_ids):
        self.bin_calls.append(sorted(card_ids))
        return {cid: [f"bin-{cid}"] for cid in card_ids}


def _rows(*card_ids):
    return [{"source_card_id": cid} for cid in card_ids]


class TestSalesForRowsCache:
    def test_second_call_with_the_same_rows_reuses_the_cache(self):
        provider = FakeProvider()
        rows = _rows(1, 2, 3)
        first = run(_sales_for_rows(provider, rows))
        second = run(_sales_for_rows(provider, rows))
        assert len(provider.sales_calls) == 1
        assert first == second

    def test_a_subset_request_reuses_the_wider_cached_result(self):
        # The production shape: one call fetches for the full candidate
        # set, a later call (a filtered endpoint) only needs a subset.
        provider = FakeProvider()
        run(_sales_for_rows(provider, _rows(1, 2, 3, 4, 5)))
        result = run(_sales_for_rows(provider, _rows(2, 4)))
        assert len(provider.sales_calls) == 1
        assert set(result.keys()) >= {2, 4}

    def test_a_request_for_an_uncached_card_triggers_a_fresh_fetch(self):
        provider = FakeProvider()
        run(_sales_for_rows(provider, _rows(1, 2)))
        run(_sales_for_rows(provider, _rows(1, 2, 3)))  # card 3 not yet cached
        assert len(provider.sales_calls) == 2

    def test_expired_cache_triggers_a_fresh_fetch(self):
        provider = FakeProvider()
        run(_sales_for_rows(provider, _rows(1, 2)))
        futgg_market_module._sales_by_card_cached_at -= (
            futgg_market_module._SEGMENTED_CANDIDATES_CACHE_SECONDS + 1
        )
        run(_sales_for_rows(provider, _rows(1, 2)))
        assert len(provider.sales_calls) == 2

    def test_a_failed_fetch_still_degrades_to_empty_dict(self):
        class FailingProvider:
            async def get_sales_by_ids(self, card_ids):
                raise RuntimeError("db gone")

        result = run(_sales_for_rows(FailingProvider(), _rows(1)))
        assert result == {}


class TestBinObservationsForRowsCache:
    def test_second_call_with_the_same_rows_reuses_the_cache(self):
        provider = FakeProvider()
        rows = _rows(10, 20)
        run(_bin_observations_for_rows(provider, rows))
        run(_bin_observations_for_rows(provider, rows))
        assert len(provider.bin_calls) == 1

    def test_a_failed_fetch_still_degrades_to_empty_dict(self):
        class FailingProvider:
            async def get_bin_observations_by_ids(self, card_ids):
                raise RuntimeError("db gone")

        result = run(_bin_observations_for_rows(FailingProvider(), _rows(1)))
        assert result == {}

    def test_sales_and_bin_caches_are_independent(self):
        # Populating one must not satisfy (or corrupt) the other.
        provider = FakeProvider()
        run(_sales_for_rows(provider, _rows(1, 2)))
        run(_bin_observations_for_rows(provider, _rows(1, 2)))
        assert len(provider.sales_calls) == 1
        assert len(provider.bin_calls) == 1
