# tests/test_futgg_scanner_cursor_wiring.py
#
# Covers the wiring in futgg_scanner.run_scan() that stamps
# provider.record_scan_cursor() for every card a pass evaluated - not just
# the ones that produced a persisted recommendation - and skips it entirely
# on a persist=False (metrics/staging) pass, mirroring how those passes
# already skip writing recommendation snapshots. The SQL-level behaviour
# this cursor enables is covered separately in test_futgg_scan_cursor.py.
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.services.futgg_scanner as scanner


def run(coro):
    return asyncio.run(coro)


class FakeProvider:
    def __init__(self, rows_by_segment):
        self.rows_by_segment = rows_by_segment
        self.record_scan_cursor_calls = []
        self.select_candidates_calls = []

    async def select_candidates_by_segment(self, *, exclude_recently_evaluated=False):
        self.select_candidates_calls.append(exclude_recently_evaluated)
        return self.rows_by_segment

    async def get_sales_by_ids(self, card_ids):
        return {}

    async def get_bin_observations_by_ids(self, card_ids):
        return {}

    async def record_scan_cursor(self, card_ids, as_of):
        self.record_scan_cursor_calls.append((sorted(card_ids), as_of))


class FakePool:
    def acquire(self):
        raise RuntimeError("no db in this test - covered by run_scan's own try/except")


def _fake_ci(signal):
    return SimpleNamespace(
        signal=signal, valuation_source=None, blocking_codes=[], trend_state=None,
    )


@pytest.fixture(autouse=True)
def stub_evaluate_and_persist(monkeypatch):
    monkeypatch.setattr(scanner, "evaluate_card", lambda row, **kw: _fake_ci("avoid"))

    async def fake_record_many(pool, to_persist):
        return len(to_persist)

    monkeypatch.setattr(scanner, "record_many", fake_record_many)


class TestScanCursorWiring:
    def test_persist_true_stamps_every_evaluated_card(self):
        rows = {"liquid": [{"source_card_id": 1}, {"source_card_id": 2}]}
        provider = FakeProvider(rows)
        metrics = run(scanner.run_scan(provider, FakePool(), persist=True))
        assert len(provider.record_scan_cursor_calls) == 1
        card_ids, as_of = provider.record_scan_cursor_calls[0]
        assert card_ids == [1, 2]
        assert isinstance(as_of, datetime)
        assert metrics.unique_cards_evaluated == 2
        # run_scan is the background rotation pass - it must opt into the
        # recency filter. Live-request callers (_segmented_candidates in
        # futgg_market.py) never call run_scan and must never opt in.
        assert provider.select_candidates_calls == [True]

    def test_persist_false_never_moves_the_cursor(self):
        rows = {"liquid": [{"source_card_id": 1}, {"source_card_id": 2}]}
        provider = FakeProvider(rows)
        run(scanner.run_scan(provider, FakePool(), persist=False))
        assert provider.record_scan_cursor_calls == []

    def test_no_candidates_is_a_no_op(self):
        provider = FakeProvider({"liquid": []})
        run(scanner.run_scan(provider, FakePool(), persist=True))
        assert provider.record_scan_cursor_calls == []
