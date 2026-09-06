# tests/test_futgg_evaluate_card_cache.py
#
# The SQL-level cache (_segmented_candidates, see
# test_futgg_segmented_candidates_cache.py) only ever addressed one layer
# of the redundant work one page load does. The dominant cost was always
# running evaluate_card() itself - a single page load fires the same
# candidate set through _score_and_filter() 5+ times over (dashboard's
# own three internal calls, plus /opportunities, /trade-finder, and
# whichever Strategy Finder tab is open), each re-evaluating every
# candidate from scratch. These tests cover _cached_evaluate_card()'s
# short-lived memoization, keyed by card_id, that collapses that
# redundant work without changing what gets evaluated or how fresh it is
# (the underlying row data is already only as fresh as
# _segmented_candidates()'s own cache for the same duration).
from __future__ import annotations

import asyncio
import time

import pytest

import app.routers.v2.futgg_market as futgg_market_module
from app.routers.v2.futgg_market import _score_and_filter


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def reset_cache():
    futgg_market_module._evaluate_card_cache.clear()
    yield
    futgg_market_module._evaluate_card_cache.clear()


class TestCachedEvaluateCard:
    def test_a_second_call_for_the_same_card_reuses_the_cached_result(self, monkeypatch):
        call_count = {"n": 0}

        def fake_evaluate(row, **kw):
            call_count["n"] += 1
            return object()

        monkeypatch.setattr(futgg_market_module, "evaluate_card", fake_evaluate)
        row = {"source_card_id": 42}
        run(_score_and_filter([row]))
        run(_score_and_filter([row]))
        assert call_count["n"] == 1

    def test_one_page_loads_worth_of_repeat_calls_evaluates_each_card_once(self, monkeypatch):
        # The actual production shape: dashboard's 3 internal calls plus
        # /opportunities and /trade-finder all evaluating the same
        # candidate set within the same few seconds.
        call_count = {"n": 0}

        def fake_evaluate(row, **kw):
            call_count["n"] += 1
            return object()

        monkeypatch.setattr(futgg_market_module, "evaluate_card", fake_evaluate)
        rows = [{"source_card_id": i} for i in range(1, 51)]
        for _ in range(5):
            run(_score_and_filter(rows))
        assert call_count["n"] == 50

    def test_expired_entry_is_evaluated_fresh(self, monkeypatch):
        call_count = {"n": 0}

        def fake_evaluate(row, **kw):
            call_count["n"] += 1
            return object()

        monkeypatch.setattr(futgg_market_module, "evaluate_card", fake_evaluate)
        row = {"source_card_id": 7}
        run(_score_and_filter([row]))
        assert call_count["n"] == 1

        # Simulate the TTL having elapsed.
        cached_at, ci = futgg_market_module._evaluate_card_cache[7]
        futgg_market_module._evaluate_card_cache[7] = (
            cached_at - (futgg_market_module._EVALUATE_CARD_CACHE_SECONDS + 1), ci,
        )
        run(_score_and_filter([row]))
        assert call_count["n"] == 2

    def test_different_cards_are_evaluated_independently(self, monkeypatch):
        seen_ids = []

        def fake_evaluate(row, **kw):
            seen_ids.append(row["source_card_id"])
            return object()

        monkeypatch.setattr(futgg_market_module, "evaluate_card", fake_evaluate)
        rows = [{"source_card_id": 1}, {"source_card_id": 2}]
        run(_score_and_filter(rows))
        assert sorted(seen_ids) == [1, 2]

    def test_cache_stores_a_monotonic_timestamp_and_the_real_result(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(futgg_market_module, "evaluate_card", lambda row, **kw: sentinel)
        before = time.monotonic()
        run(_score_and_filter([{"source_card_id": 99}]))
        after = time.monotonic()
        cached_at, ci = futgg_market_module._evaluate_card_cache[99]
        assert before <= cached_at <= after
        assert ci is sentinel
