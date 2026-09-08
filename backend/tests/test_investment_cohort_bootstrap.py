from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import investment_cohort_bootstrap as bootstrap


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self, rows=None, membership_count=0):
        self.rows = rows or []
        self.membership_count = membership_count
        self.fetch_calls = []
        self.fetchval_calls = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self.rows

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self.membership_count


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_populate_live_cohorts_is_scoped_to_requested_game_year(monkeypatch):
    conn = _Conn(rows=[{
        "source_card_id": 123,
        "primary_position": "ST",
        "current_bin": 50000,
        "price_updated_at": None,
    }], membership_count=1)
    pool = _Pool(conn)
    seen = {}

    async def fake_ensure(conn_arg, *, game_year, cards, config):
        seen["conn"] = conn_arg
        seen["game_year"] = game_year
        seen["cards"] = cards
        return 1

    monkeypatch.setattr(bootstrap, "ensure_cohort_membership", fake_ensure)
    result = run(bootstrap.populate_live_cohorts(pool, game_year=27))

    sql, args = conn.fetch_calls[0]
    assert "FROM futgg_market_snapshot" in sql
    assert "WHERE game_year = $1" in sql
    assert "current_bin IS NOT NULL" in sql
    assert "is_tradeable IS DISTINCT FROM FALSE" in sql
    assert args == (27,)
    assert seen["game_year"] == 27
    assert seen["cards"][0].card_id == 123
    assert result == {
        "game_year": 27,
        "priced_cards": 1,
        "memberships": 1,
        "classification_attempts": 1,
    }


def test_zero_priced_cards_is_harmless_and_does_not_call_classifier(monkeypatch):
    conn = _Conn(rows=[])
    pool = _Pool(conn)
    called = {"value": False}

    async def fake_ensure(*args, **kwargs):
        called["value"] = True
        return 0

    monkeypatch.setattr(bootstrap, "ensure_cohort_membership", fake_ensure)
    result = run(bootstrap.populate_live_cohorts(pool, game_year=27))

    assert called["value"] is False
    assert result["priced_cards"] == 0
    assert result["memberships"] == 0


def test_installer_registers_only_when_investment_engine_enabled(monkeypatch):
    registered = {}

    def fake_register(name, job):
        registered[name] = job

    import app.services.managed_startup_tasks as managed
    monkeypatch.setattr(managed, "register_startup_job", fake_register)

    app = SimpleNamespace(state=SimpleNamespace())
    application = SimpleNamespace(app=app)

    monkeypatch.setattr(
        bootstrap,
        "INVESTMENT_ENGINE_CONFIG",
        SimpleNamespace(enabled=False, cohort_refresh_interval_seconds=21600),
    )
    bootstrap.install_investment_cohort_bootstrap(application)
    assert registered == {}

    monkeypatch.setattr(
        bootstrap,
        "INVESTMENT_ENGINE_CONFIG",
        SimpleNamespace(enabled=True, cohort_refresh_interval_seconds=21600),
    )
    bootstrap.install_investment_cohort_bootstrap(application)
    assert "investment_cohort_bootstrap" in registered
