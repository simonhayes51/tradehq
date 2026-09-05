# tests/test_futgg_recommendation_store_bin_fields.py
#
# record_recommendation()'s _INSERT gained 8 new columns for the
# BIN-history fallback (migration 082/futgg_bin_valuation.py) - this
# checks the positional params actually line up with the SQL's column
# list, against a fake connection (no database needed), the same pattern
# test_attach_sbc_reward_watch.py already uses for a similar "no view,
# no real Postgres" case.
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

from app.services.futgg_intelligence import CardIntelligence
from app.services.futgg_recommendation_store import _INSERT, record_recommendation


def run(coro):
    return asyncio.run(coro)


class _FakeConn:
    def __init__(self):
        self.fetchval_calls = []

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, args))
        return 1


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquireCtx(self._conn)


AS_OF = datetime(2026, 1, 10, 12, 0, tzinfo=None)


def _bin_derived_ci() -> CardIntelligence:
    return CardIntelligence(
        card_id=42,
        signal="buy",
        status="active",
        valuation_source="bin_history",
        evidence_quality="estimated",
        bin_sample_count=10,
        bin_history_span_hours=30.0,
        bin_reference_price=Decimal("10000"),
        bin_trend_direction="flat",
        bin_volatility=0.02,
        evidence_age_minutes=5,
        evaluated_at=AS_OF,
    )


class TestRecordRecommendationBinFields:
    def test_insert_column_list_matches_positional_param_count(self):
        """The _INSERT string's own column list and VALUES placeholder
        count must agree - a mismatch here is exactly the kind of typo
        that would silently write the wrong field into the wrong column
        with asyncpg (no error, just wrong data)."""
        columns_section = _INSERT.split("VALUES")[0]
        num_columns = columns_section.count(",") + 1
        placeholders_section = _INSERT.split("VALUES")[1].split(")")[0]
        num_placeholders = placeholders_section.count("$")
        assert num_columns == num_placeholders == 48

    def test_bin_evidence_fields_are_bound_in_order(self):
        conn = _FakeConn()
        pool = _FakePool(conn)
        ci = _bin_derived_ci()
        run(record_recommendation(pool, snapshot={}, ci=ci))
        assert len(conn.fetchval_calls) == 1
        _, args = conn.fetchval_calls[0]
        # Last 8 positional args ($41-$48) are the BIN evidence fields, in
        # the exact order _INSERT's own column list declares them.
        tail = args[-8:]
        assert tail == (
            "bin_history", "estimated", 10, 30.0, 10000.0, "flat", 0.02, 5,
        )

    def test_a_completed_sales_result_writes_null_bin_fields(self):
        conn = _FakeConn()
        pool = _FakePool(conn)
        ci = CardIntelligence(
            card_id=42, signal="buy", status="active",
            valuation_source="completed_sales", evidence_quality="confirmed",
            evaluated_at=AS_OF,
        )
        run(record_recommendation(pool, snapshot={}, ci=ci))
        _, args = conn.fetchval_calls[0]
        tail = args[-8:]
        assert tail == ("completed_sales", "confirmed", None, None, None, None, None, None)
