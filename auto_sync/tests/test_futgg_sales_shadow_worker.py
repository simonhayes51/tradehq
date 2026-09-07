from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_shadow_worker as worker  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_shadow_wrapper_returns_exact_live_due_rows_when_observation_succeeds(monkeypatch):
    due_rows = [{"source_card_id": 1}, {"source_card_id": 2}]
    seen = {}

    async def original_select_due(conn):
        return due_rows

    async def observe(conn, due, watchlist_conn=None):
        seen["due"] = due

    async def fake_main():
        result = await worker.sales.select_due(object())
        seen["result"] = result

    monkeypatch.delenv("WATCHLIST_DATABASE_URL", raising=False)
    monkeypatch.setattr(worker.sales, "select_due", original_select_due)
    monkeypatch.setattr(worker.sales, "main", fake_main)
    monkeypatch.setattr(worker, "observe_after_due_selection", observe)

    run(worker.run())

    assert seen["due"] is due_rows
    assert seen["result"] is due_rows
    assert worker.sales.select_due is original_select_due


def test_shadow_failure_cannot_change_or_fail_live_due_selection(monkeypatch):
    due_rows = [{"source_card_id": 99}]
    seen = {}

    async def original_select_due(conn):
        return due_rows

    async def exploding_observe(conn, due, watchlist_conn=None):
        raise RuntimeError("shadow DB unavailable")

    async def fake_main():
        seen["result"] = await worker.sales.select_due(object())

    monkeypatch.delenv("WATCHLIST_DATABASE_URL", raising=False)
    monkeypatch.setattr(worker.sales, "select_due", original_select_due)
    monkeypatch.setattr(worker.sales, "main", fake_main)
    monkeypatch.setattr(worker, "observe_after_due_selection", exploding_observe)

    run(worker.run())

    assert seen["result"] is due_rows
    assert worker.sales.select_due is original_select_due


def test_wrapper_delegates_to_sales_main_so_diagnostic_mode_is_preserved():
    text = (Path(__file__).resolve().parents[1] / "futgg_sales_shadow_worker.py").read_text()
    assert "await sales.main()" in text
    assert "await sales.run_forever()" not in text


def test_entrypoint_launches_shadow_worker_under_existing_supervisor():
    text = (Path(__file__).resolve().parents[1] / "docker-entrypoint-sales.sh").read_text()
    assert "python futgg_sales_shadow_worker.py &" in text
    assert "FUTGG_SALES_SELECT_LIMIT_MAX" in text
    assert "FUTGG_SALES_CYCLE_SLEEP_MIN" in text
    assert "Xvfb" in text
