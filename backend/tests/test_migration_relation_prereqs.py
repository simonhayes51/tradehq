from __future__ import annotations

import asyncio
from pathlib import Path

from scripts import run_migrations


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self, present):
        self.present = set(present)
        self.query = None
        self.args = None

    async def fetch(self, query, names):
        self.query = query
        self.args = names
        return [
            {"relation_name": name}
            for name in names
            if name in self.present
        ]


def test_relation_prerequisites_accept_materialized_views_via_pg_class():
    conn = _Conn({"futgg_market_snapshot", "fair_value_mv"})
    missing = run(run_migrations._missing_tables(
        conn,
        ["futgg_market_snapshot", "fair_value_mv", "not_there"],
    ))

    assert missing == ["not_there"]
    assert "FROM pg_class c" in conn.query
    assert "c.relkind IN ('r', 'p', 'v', 'm', 'f')" in conn.query
    assert "information_schema.tables" not in conn.query


def test_migration_073_has_no_runtime_market_snapshot_prerequisite():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "073_free_daily_picks.sql").read_text()

    assert "requires-table" not in sql
    assert "CREATE TABLE IF NOT EXISTS free_daily_picks" in sql


def test_migration_067_declares_every_relation_it_indexes():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "067_restore_futgg_market_snapshot_indexes.sql").read_text()
    required = run_migrations._file_requires_tables(sql)

    assert required == ["futgg_market_snapshot", "fair_value_mv"]
    assert "ON futgg_market_snapshot" in sql
    assert "ON fair_value_mv" in sql
