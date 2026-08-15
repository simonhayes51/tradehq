"""
An empty sales window has two meanings, and the product stated only one.

WHY THIS EXISTS
---------------
Reported by the user: "Most of the players that I search are saying 0
sales in the last 3 days."

That message reads as a fact about the market - nobody has bought this
card. For most of the catalogue it is a fact about our own coverage.
futgg_sales_sync re-reads by rating band, and the configured intervals
for the lower bands (96 hours for 75-79, 28 days for under-75 in
production) are LONGER than the windows the UI offers. A 78-rated card on
a 96-hour cadence reports "no sales in 24h" essentially always, however
actively it is being traded, because we only look every four days.

Stating our own blind spot as an observation about the market is the same
class of defect as the frozen age_text in the Recent sales table: the UI
asserting something it has not actually measured.

get_sales_coverage supplies the missing half - when we last LOOKED - so
the caller can say which of the two it is.
"""
from __future__ import annotations

import asyncio
import functools
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.services.market_data_provider import FutggMarketDataProvider

NOW = datetime.now(timezone.utc)


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.new_event_loop().run_until_complete(fn(*args, **kwargs))
    return wrapper


class _Conn:
    def __init__(self, row, raise_undefined=False):
        self._row = row
        self._raise = raise_undefined

    async def fetchrow(self, *_a, **_k):
        if self._raise:
            raise asyncpg.exceptions.UndefinedColumnError("column does not exist")
        return self._row


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _provider(row, raise_undefined=False):
    return FutggMarketDataProvider(_Pool(_Conn(row, raise_undefined)))


class TestSalesCoverage:
    @sync
    async def test_a_card_we_have_never_read_says_so(self):
        """Distinct from "checked, found nothing" - this card has no sales
        because we have not been to it, and the UI must not blame the
        market for that."""
        result = await _provider({
            "last_sales_status": None, "last_sales_status_at": None,
            "next_sales_due_at": None, "sales_first_read_at": None, "rating": 78,
        }).get_sales_coverage(1)
        assert result["never_checked"] is True
        assert result["checked_at"] is None

    @sync
    async def test_a_card_we_read_days_ago_reports_when(self):
        """The number that explains the user's report: a 78-rated card on
        a 96-hour cadence, last checked four days ago. Any 24h or 7d
        window will look empty, and now the UI can say why."""
        checked = NOW - timedelta(days=4)
        result = await _provider({
            "last_sales_status": "ok", "last_sales_status_at": checked,
            "next_sales_due_at": NOW + timedelta(hours=2),
            "sales_first_read_at": NOW - timedelta(days=30), "rating": 78,
        }).get_sales_coverage(1)
        assert result["never_checked"] is False
        assert result["checked_at"] == checked
        assert result["status"] == "ok"

    @sync
    async def test_an_unknown_card_does_not_pretend_to_know(self):
        result = await _provider(None).get_sales_coverage(999)
        assert result == {"checked_at": None, "never_checked": None, "status": None}

    @sync
    async def test_a_database_without_salessync_columns_degrades(self):
        """SalesSync owns these columns via its own ensure_schema. A
        backend that boots against a database it has never run on must
        report "unknown", not 500 the player page - the same tolerance
        every other cross-worker column read in this module has."""
        result = await _provider(None, raise_undefined=True).get_sales_coverage(1)
        assert result == {"checked_at": None, "never_checked": None, "status": None}
