"""Sales catalogue boundaries and scheduling budget, without live services."""
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')

import futgg_sales_sync as sales
import futgg_sales_shadow_worker as worker
from futgg_sales_information_shadow import ShadowSelection
from futgg_sales_triggers import TriggeredCard


def test_catalogue_boundaries(monkeypatch):
    monkeypatch.setattr(sales, 'GAME_YEAR', '27')
    monkeypatch.setattr(sales, 'MIN_SALES_RATING', 80)
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE players (id INTEGER, is_active BOOLEAN, is_tradeable BOOLEAN, game_year INTEGER, rating INTEGER, rarity TEXT)')
    db.executemany('INSERT INTO players VALUES (?, ?, ?, ?, ?, ?)', [
        (1, True, True, 27, 79, 'Gold Rare'),
        (2, True, True, 27, 80, 'Gold Rare'),
        (3, True, True, 27, 91, 'Icon'),
        (4, True, True, 27, 82, 'Silver Rare'),
        (5, True, True, 27, 82, 'Bronze Rare'),
        (6, True, True, 27, None, 'Gold Rare'),
        (7, True, True, 26, 91, 'Gold Rare'),
        (8, False, True, 27, 91, 'Gold Rare'),
        (9, True, False, 27, 91, 'Gold Rare'),
        (10, True, None, 27, 85, None),
    ])
    ids = [row[0] for row in db.execute(f'SELECT id FROM players p WHERE {sales.sales_catalogue_filter("p")} ORDER BY id')]
    assert ids == [2, 3, 10]
    db.close()


def test_baseline_floors_and_overflow_share_one_budget(monkeypatch):
    monkeypatch.setattr(sales, 'SELECT_LIMIT', 120)
    calls = []

    class Conn:
        async def fetch(self, query, *args):
            calls.append((query, args))
            assert sales.sales_catalogue_filter() in query
            limit = args[-1]
            start = sum(len(rows) for rows in returned)
            rows = [{'source_card_id': start + i} for i in range(limit)]
            if len(args) == 2:
                assert args[0] == list(range(start))
            returned.append(rows)
            return rows

    returned = []
    rows = asyncio.run(sales.select_due(Conn()))
    assert len(rows) == len({row['source_card_id'] for row in rows}) == 120
    assert [args[-1] for _, args in calls] == [36, 36, 18, 3, 2, 25]
    assert all('(sales_first_read_at IS NOT NULL)' in query for query, _ in calls)


def test_live_trigger_cannot_bypass_catalogue_scope():
    class Conn:
        async def fetch(self, query, *args):
            assert sales.sales_catalogue_filter('p') in query
            assert args[0] == [241667]
            return []

    selection = ShadowSelection(cards=(TriggeredCard(card_id=241667, reasons=('new_release',), details={}),), due_ids=())
    assert asyncio.run(worker._hydrate_rebalance_candidates(Conn(), selection)) == []


def test_coverage_counts_the_same_catalogue():
    class Conn:
        async def fetchrow(self, query):
            assert sales.sales_catalogue_filter('p') in query
            return {'total': 0}

    assert asyncio.run(sales.report_coverage(Conn())) is None


def test_information_reserve_scales_without_adding_requests(monkeypatch):
    monkeypatch.setattr(worker, 'LIVE_REBALANCE_ENABLED', True)
    monkeypatch.setattr(worker, 'LIVE_REBALANCE_SHARE', 0.20)
    monkeypatch.setattr(worker, 'LIVE_REBALANCE_MAX_SLOTS', 24)
    assert worker.rebalance_slot_count(120) == 24
    assert worker.rebalance_slot_count(60) == 12
