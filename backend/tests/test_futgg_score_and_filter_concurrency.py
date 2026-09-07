# tests/test_futgg_score_and_filter_concurrency.py
#
# Production incident (2026-09-07): one page load fires /dashboard,
# /opportunities, /trade-finder/strategy/best_overall, and /track-record
# within moments of each other, and every one of them calls
# _score_and_filter() (app/routers/v2/futgg_market.py). _evaluate_card_cache
# shares evaluate_card()'s RESULTS across calls, but nothing serialized the
# CALLS themselves - two requests arriving while the cache was still cold
# each independently looped through the same ~1,300+ candidates, calling
# the same synchronous, CPU-bound evaluate_card() for the same card_ids in
# true duplicate, concurrently. On a single-threaded event loop that is N
# full 15-20s CPU-bound passes competing for the one CPU available, long
# enough to starve even a trivially cheap, unrelated request
# (/api/v2/market/freshness measured timing out at its own 8s client
# limit during exactly this pileup) and to push the heavier requests past
# their own client-side timeouts, surfacing as canceled requests rather
# than merely slow ones.
#
# These tests cover the lock added in response: concurrent calls must
# collapse into evaluating each card_id exactly once, with every caller
# after the first reusing the now-cached result instead of redoing the
# same work.
from __future__ import annotations

import asyncio

import pytest

import app.routers.v2.futgg_market as market_module
from app.routers.v2.futgg_market import _score_and_filter


def run(coro):
    return asyncio.run(coro)


class _FakeCardIntelligence:
    """Minimal stand-in - _score_and_filter only reads attributes off this
    when the corresponding filter kwarg is not None, so a call with no
    filters (the shape used below) never touches any of them."""


@pytest.fixture(autouse=True)
def reset_caches():
    market_module._evaluate_card_cache.clear()
    yield
    market_module._evaluate_card_cache.clear()


def _rows(*card_ids):
    return [{"source_card_id": cid} for cid in card_ids]


class TestScoreAndFilterConcurrency:
    def test_concurrent_calls_over_the_same_rows_evaluate_each_card_once(self, monkeypatch):
        calls_per_card: dict[int, int] = {}

        def _counting_evaluate_card(row, **kwargs):
            card_id = int(row["source_card_id"])
            calls_per_card[card_id] = calls_per_card.get(card_id, 0) + 1
            return _FakeCardIntelligence()

        monkeypatch.setattr(market_module, "evaluate_card", _counting_evaluate_card)
        rows = _rows(1, 2, 3)

        async def gather_five():
            return await asyncio.gather(*[_score_and_filter(rows) for _ in range(5)])

        results = run(gather_five())
        assert calls_per_card == {1: 1, 2: 1, 3: 1}, (
            "each card_id must be evaluated exactly once across 5 concurrent "
            "callers over the same rows, not once per caller"
        )
        # Every caller still gets a full, correct result - the lock changes
        # WHEN work happens, never what each caller receives back.
        assert all(len(r) == 3 for r in results)

    def test_a_slow_first_caller_does_not_block_a_second_caller_from_a_different_result(self, monkeypatch):
        # Different callers ask different questions (e.g. signals filter)
        # over an overlapping row set - the lock must serialize the
        # underlying evaluate_card() work without corrupting either
        # caller's own independent filtering.
        def _fake_evaluate_card(row, **kwargs):
            ci = _FakeCardIntelligence()
            ci.signal = "buy" if int(row["source_card_id"]) == 1 else "avoid"
            return ci

        monkeypatch.setattr(market_module, "evaluate_card", _fake_evaluate_card)
        rows = _rows(1, 2)

        async def gather_both():
            return await asyncio.gather(
                _score_and_filter(rows, signals={"buy"}),
                _score_and_filter(rows, signals={"avoid"}),
            )

        buy_only, avoid_only = run(gather_both())
        assert [row["source_card_id"] for row, _ in buy_only] == [1]
        assert [row["source_card_id"] for row, _ in avoid_only] == [2]
