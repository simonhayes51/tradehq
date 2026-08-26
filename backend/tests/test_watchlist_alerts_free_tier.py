# tests/test_watchlist_alerts_free_tier.py
#
# Direct unit tests for main.py's POST /api/watchlist-alerts
# (create_watchlist_alert) Free/Premium (£3/mo) active_alert_limit gate -
# this endpoint had no limit at all before this commit despite Billing.jsx
# marketing price alerts as Premium-only. No test file exercises main.py
# directly anywhere in this repo today, so this sets up the minimum
# needed to import it: main.py hard-requires DATABASE_URL/SECRET_KEY at
# import time (raises ValueError otherwise) and JWT_PRIVATE_KEY must not
# be the dev default when ENV=production - setting ENV=development here
# (matching local/test usage, never production) sidesteps that without
# touching production behaviour. Calls create_watchlist_alert directly
# with a fake watchlist_pool (module-global, monkeypatched) and a fake
# compute_entitlements - same "no real DB" bar the rest of this repo's
# test suite sets, via the same asyncio.run() pattern tests/
# test_watchlist_router.py already established (no pytest-asyncio here).
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

os.environ.setdefault("DATABASE_URL", "postgres://user:pass@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ENV", "development")

import pytest
from fastapi import HTTPException

import main as main_module


def run_async(coro_fn):
    return asyncio.run(coro_fn())


class FakeAcquireCtx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakeAlertsWatchlistPool:
    """In-memory stand-in for the watchlist_alerts / watchlist tables this
    endpoint reads and upserts against."""

    def __init__(self):
        self.alerts: List[Dict[str, Any]] = []  # {user_id, card_id, platform, metric}
        self.watchlist_rows: List[Dict[str, Any]] = []

    def acquire(self):
        return FakeAcquireCtx(self)

    async def fetchrow(self, query: str, *args):
        if "SELECT source FROM watchlist" in query:
            user_id, card_id, plat = args
            for r in self.watchlist_rows:
                if r["user_id"] == user_id and r["card_id"] == card_id and r["platform"] == plat:
                    return {"source": r["source"]}
            return None
        return None

    async def fetchval(self, query: str, *args):
        if "SELECT 1 FROM watchlist_alerts" in query:
            user_id, card_id, plat, metric = args
            for a in self.alerts:
                if (a["user_id"], a["card_id"], a["platform"], a["metric"]) == (user_id, card_id, plat, metric):
                    return 1
            return None
        if "COUNT(*) FROM watchlist_alerts WHERE user_id" in query:
            (user_id,) = args
            return len([a for a in self.alerts if a["user_id"] == user_id])
        return 0

    async def execute(self, query: str, *args):
        if "INSERT INTO watchlist_alerts" in query:
            user_id, _discord_id, card_id, plat, metric = args[0], args[1], args[2], args[3], args[4]
            key = (user_id, card_id, plat, metric)
            existing = next(
                (a for a in self.alerts
                 if (a["user_id"], a["card_id"], a["platform"], a["metric"]) == key),
                None,
            )
            if existing is None:
                self.alerts.append({"user_id": user_id, "card_id": card_id, "platform": plat, "metric": metric})
        return "INSERT 1"


def _free_ent(alert_limit: int = 1) -> Dict[str, Any]:
    return {"user_id": "free-user-1", "is_premium": False, "limits": {"active_alert_limit": alert_limit}}


def _premium_ent() -> Dict[str, Any]:
    return {"user_id": "premium-user-1", "is_premium": True, "limits": {"active_alert_limit": None}}


async def _async_return(value):
    return value


def _alert_payload(**overrides) -> "main_module.WatchlistAlertCreate":
    defaults = dict(card_id=555, platform="ps", metric="price", rise_pct=5, fall_pct=5)
    defaults.update(overrides)
    return main_module.WatchlistAlertCreate(**defaults)


class FakeRequest:
    pass  # compute_entitlements is monkeypatched away, so this is never touched


# ---- Scenario 7: Free active-alert limit --------------------------------

def test_free_second_distinct_alert_is_rejected(monkeypatch):
    monkeypatch.setattr(main_module, "compute_entitlements", lambda req: _async_return(_free_ent(alert_limit=1)))
    fake_pool = FakeAlertsWatchlistPool()
    monkeypatch.setattr(main_module, "watchlist_pool", fake_pool)

    async def create(card_id):
        return await main_module.create_watchlist_alert(
            _alert_payload(card_id=card_id), FakeRequest(), user_id="free-user-1",
        )

    first = run_async(lambda: create(555))
    assert first["ok"] is True

    with pytest.raises(HTTPException) as exc_info:
        run_async(lambda: create(556))  # a genuinely different card -> a 2nd alert
    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["error"] == "limit_reached"
    assert exc_info.value.detail["feature"] == "watchlist_alerts"
    assert len(fake_pool.alerts) == 1  # the rejected 2nd alert was never created


def test_free_editing_the_same_alert_does_not_count_as_a_new_one(monkeypatch):
    monkeypatch.setattr(main_module, "compute_entitlements", lambda req: _async_return(_free_ent(alert_limit=1)))
    fake_pool = FakeAlertsWatchlistPool()
    monkeypatch.setattr(main_module, "watchlist_pool", fake_pool)

    async def create(**kwargs):
        return await main_module.create_watchlist_alert(
            _alert_payload(**kwargs), FakeRequest(), user_id="free-user-1",
        )

    first = run_async(lambda: create(card_id=555, platform="ps", metric="price", rise_pct=5))
    assert first["ok"] is True

    # Same (card_id, platform, metric) - a re-submission/edit of the exact
    # same rule, with a different threshold - must succeed even though
    # Free is already "at" the 1-alert cap.
    edited = run_async(lambda: create(card_id=555, platform="ps", metric="price", rise_pct=10))
    assert edited["ok"] is True
    assert len(fake_pool.alerts) == 1  # still just the one alert, not a second


def test_free_alert_on_a_different_metric_for_the_same_card_counts_as_new(monkeypatch):
    # (card_id, platform, metric) is the identity key - a different metric
    # on the same card is a genuinely distinct rule, not an edit.
    monkeypatch.setattr(main_module, "compute_entitlements", lambda req: _async_return(_free_ent(alert_limit=1)))
    fake_pool = FakeAlertsWatchlistPool()
    monkeypatch.setattr(main_module, "watchlist_pool", fake_pool)

    async def create(**kwargs):
        return await main_module.create_watchlist_alert(
            _alert_payload(**kwargs), FakeRequest(), user_id="free-user-1",
        )

    run_async(lambda: create(card_id=555, platform="ps", metric="price"))
    with pytest.raises(HTTPException) as exc_info:
        run_async(lambda: create(card_id=555, platform="ps", metric="liquidity", ref_price=1000))
    assert exc_info.value.status_code == 402


def test_premium_has_no_active_alert_limit(monkeypatch):
    monkeypatch.setattr(main_module, "compute_entitlements", lambda req: _async_return(_premium_ent()))
    fake_pool = FakeAlertsWatchlistPool()
    monkeypatch.setattr(main_module, "watchlist_pool", fake_pool)

    async def create(card_id):
        return await main_module.create_watchlist_alert(
            _alert_payload(card_id=card_id), FakeRequest(), user_id="premium-user-1",
        )

    for card_id in (555, 556, 557, 558):
        result = run_async(lambda cid=card_id: create(cid))
        assert result["ok"] is True
    assert len(fake_pool.alerts) == 4
