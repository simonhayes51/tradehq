# tests/test_futgg_event_loop_yielding.py
#
# Production incident (2026-09-06): a browser request to /api/v2/opportunities
# stalled at "Initial connection" for exactly the frontend's 8s timeout -
# not a slow response, a server that could not get around to accepting or
# servicing ANY connection at all. Root cause: evaluate_card() is a plain
# synchronous function, and both _score_and_filter() (futgg_market.py) and
# futgg_scanner.run_scan()'s main loop called it in a bare for-loop with no
# await point - a long enough loop (the segment-limit increase in an
# earlier commit made these loops bigger) monopolizes the single asyncio
# event loop for its entire duration, during which the process cannot
# accept new connections or service any other in-flight request.
#
# These tests confirm both loops now yield control periodically (an
# `await asyncio.sleep(0)` every N rows) without changing what they
# actually compute - covered by counting real asyncio.sleep(0) calls
# (via a monkeypatched wrapper) rather than mocking it away, so a
# regression that silently removes the yield would fail these tests.
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.routers.v2.futgg_market as futgg_market_module
import app.services.futgg_scanner as scanner_module
from app.routers.v2.futgg_market import _score_and_filter


def run(coro):
    return asyncio.run(coro)


def _fake_ci(signal="avoid"):
    return SimpleNamespace(
        signal=signal, valuation_source=None, blocking_codes=[], trend_state=None,
        risk_level=None, expected_profit_after_tax=None, expected_roi=None,
        confidence_score=None, liquidity_score=None,
    )


class TestScoreAndFilterYields:
    def test_yields_at_least_once_for_a_large_row_set(self, monkeypatch):
        monkeypatch.setattr(
            futgg_market_module, "evaluate_card", lambda row, **kw: _fake_ci()
        )
        sleep_calls = []
        real_sleep = asyncio.sleep

        async def counting_sleep(seconds):
            sleep_calls.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", counting_sleep)

        rows = [{"source_card_id": i} for i in range(1, 251)]  # > yield threshold
        run(_score_and_filter(rows))
        assert len(sleep_calls) > 0
        assert all(s == 0 for s in sleep_calls)  # a real yield, not a real delay

    def test_never_yields_for_a_small_row_set(self, monkeypatch):
        monkeypatch.setattr(
            futgg_market_module, "evaluate_card", lambda row, **kw: _fake_ci()
        )
        sleep_calls = []

        async def counting_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", counting_sleep)

        rows = [{"source_card_id": i} for i in range(1, 6)]
        run(_score_and_filter(rows))
        assert sleep_calls == []

    def test_still_returns_correct_scored_results(self, monkeypatch):
        # The yield must not change what gets evaluated or returned.
        def fake_evaluate(row, **kw):
            return _fake_ci("buy" if row["source_card_id"] % 2 == 0 else "avoid")

        monkeypatch.setattr(futgg_market_module, "evaluate_card", fake_evaluate)
        rows = [{"source_card_id": i} for i in range(1, 121)]
        scored = run(_score_and_filter(rows, signals={"buy", "strong_buy"}))
        assert len(scored) == 60
        assert all(row["source_card_id"] % 2 == 0 for row, _ci in scored)


class TestScannerLoopYields:
    def test_run_scan_yields_at_least_once_for_a_large_candidate_set(self, monkeypatch):
        monkeypatch.setattr(scanner_module, "evaluate_card", lambda row, **kw: _fake_ci())

        async def fake_record_many(pool, to_persist):
            return len(to_persist)

        monkeypatch.setattr(scanner_module, "record_many", fake_record_many)

        sleep_calls = []
        real_sleep = asyncio.sleep

        async def counting_sleep(seconds):
            sleep_calls.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", counting_sleep)

        class FakeProvider:
            async def select_candidates_by_segment(self, *, exclude_recently_evaluated=False):
                return {"all": [{"source_card_id": i} for i in range(1, 251)]}

            async def get_sales_by_ids(self, card_ids):
                return {}

            async def get_bin_observations_by_ids(self, card_ids):
                return {}

            async def record_scan_cursor(self, card_ids, as_of):
                pass

        class FakePool:
            def acquire(self):
                raise RuntimeError("not needed for this test")

        run(scanner_module.run_scan(FakeProvider(), FakePool(), persist=True))
        assert len(sleep_calls) > 0
        assert all(s == 0 for s in sleep_calls)
