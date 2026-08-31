# tests/test_trade_diagnosis.py
#
# "Why is the feed empty" has now been answered three times by inferring
# deployment state from screenshots - which is the exact complaint
# app/why_no_trades.py opens with, written the last time it happened.
#
# The distinction that matters, and that a bare "0 opportunities" cannot
# make: the sales collector being down looks identical to a quiet market.
# One is an outage; the other is the product working correctly.
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
os.environ.setdefault("ENV", "test")

from app.services import trade_diagnosis as td  # noqa: E402

NOW = datetime.now(timezone.utc)


def run(coro):
    return asyncio.run(coro)


def card(card_id, *, sales_count=40, latest_sale_at=None, name=None):
    return {
        "source_card_id": card_id,
        "name": name or f"Card {card_id}",
        "price_tier": "gold_rare",
        "sales_count": sales_count,
        "latest_sale_at": latest_sale_at if latest_sale_at is not None else NOW - timedelta(minutes=10),
        "price_updated_at": NOW - timedelta(minutes=5),
        "bin_captured_at": NOW - timedelta(minutes=5),
    }


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *args):
        return self.rows


class FakeProvider:
    def __init__(self, sales=None, fail=False):
        self.sales = sales or {}
        self.fail = fail

    async def get_sales_by_ids(self, ids):
        if self.fail:
            raise RuntimeError("sales fetch is down")
        return self.sales


class Intel:
    def __init__(self, signal, blocking=()):
        self.signal = signal
        self.blocking_codes = list(blocking)


def diagnose(rows, *, evaluate, provider=None):
    """Run the diagnosis with a stubbed engine, so these tests are about
    the DIAGNOSIS, not about re-testing evaluate_card."""
    import app.services.futgg_intelligence as fi
    import app.services.market_data_provider as mdp

    original_eval, original_cols = fi.evaluate_card, mdp.resolve_snapshot_columns

    async def fake_columns(_conn):
        return "*"

    fi.evaluate_card = evaluate
    mdp.resolve_snapshot_columns = fake_columns
    try:
        return run(td.diagnose(FakeConn(rows), provider or FakeProvider()))
    finally:
        fi.evaluate_card = original_eval
        mdp.resolve_snapshot_columns = original_cols


class TestItTellsTheCasesApart:
    def test_no_sales_at_all_is_named_as_the_collector(self):
        rows = [card(i, sales_count=0) for i in range(20)]
        result = diagnose(rows, evaluate=lambda *a, **k: Intel("insufficient_data", ["low_confidence"]))

        assert result["verdict"] == "sales_collector"
        assert result["inputs"]["cards_with_no_sales"] == 20
        # The sentence has to name the RIGHT collector: prices being fine
        # says nothing about sales, and the two run separately.
        assert "sales collector" in result["summary"]
        assert "not the price collector" in result["summary"]

    def test_stale_sales_reads_as_behind_rather_than_stopped(self):
        rows = [card(i, latest_sale_at=NOW - timedelta(hours=100)) for i in range(20)]
        result = diagnose(rows, evaluate=lambda *a, **k: Intel("insufficient_data", ["stale_sales"]))

        assert result["inputs"]["cards_with_sales_older_than_72h"] == 20
        assert "running behind rather than stopped" in result["summary"]

    def test_a_quiet_market_is_not_reported_as_a_fault(self):
        # Everything values fine; nothing is cheap enough. The engine is
        # working, and saying otherwise would send someone hunting a
        # non-existent outage.
        rows = [card(i) for i in range(20)]
        result = diagnose(rows, evaluate=lambda *a, **k: Intel("watch", ["price_above_max_buy"]))

        assert result["verdict"] == "quiet_market"
        assert "engine is working" in result["summary"]
        assert result["valued"] == 20

    def test_an_empty_snapshot_is_its_own_verdict(self):
        result = diagnose([], evaluate=lambda *a, **k: Intel("buy"))
        assert result["verdict"] == "empty_snapshot"
        assert result["pool_size"] == 0

    def test_a_healthy_feed_reports_partial_not_an_outage(self):
        rows = [card(i) for i in range(20)]
        result = diagnose(rows, evaluate=lambda *a, **k: Intel("buy"))
        assert result["verdict"] == "partial"
        assert result["valued"] == 20
        assert result["blocking"] == []


class TestWhatItReports:
    def test_it_counts_signals_and_blocking_codes(self):
        rows = [card(i) for i in range(10)]

        def evaluate(row, **kwargs):
            cid = int(row["source_card_id"])
            return Intel("buy") if cid < 3 else Intel("insufficient_data", ["low_confidence"])

        result = diagnose(rows, evaluate=evaluate)
        assert result["signals"]["buy"] == 3
        assert result["signals"]["insufficient_data"] == 7
        assert result["dominant_cause"] == "low_confidence"
        assert result["dominant_cause_cards"] == 7
        assert result["valued"] == 3
        assert result["valued_pct"] == 30.0

    def test_every_blocking_code_carries_guidance(self):
        rows = [card(i) for i in range(5)]
        result = diagnose(rows, evaluate=lambda *a, **k: Intel("insufficient_data", ["low_confidence"]))
        assert result["blocking"][0]["guidance"]
        # The guidance explains the geometric blend, which is the
        # non-obvious part: one zero component zeroes the whole score.
        assert "one zero component" in result["blocking"][0]["guidance"].lower()

    def test_it_gives_examples_so_a_card_can_be_checked_by_hand(self):
        rows = [card(i, name=f"Player {i}") for i in range(10)]
        result = diagnose(rows, evaluate=lambda *a, **k: Intel("insufficient_data", ["low_confidence"]))
        assert result["blocking"][0]["examples"]
        assert "Player 0" in result["blocking"][0]["examples"][0]

    def test_a_broken_sales_fetch_does_not_masquerade_as_a_quiet_market(self):
        # If the batch sales fetch is what is broken, that IS the answer.
        # Quietly evaluating without sales would blame the market.
        rows = [card(i, sales_count=0) for i in range(10)]
        result = diagnose(
            rows,
            evaluate=lambda *a, **k: Intel("insufficient_data", ["low_confidence"]),
            provider=FakeProvider(fail=True),
        )
        assert result["verdict"] == "sales_collector"


class TestItIsReadOnly:
    def test_the_module_never_writes(self):
        import inspect

        source = "\n".join(
            line for line in inspect.getsource(td).splitlines()
            if not line.strip().startswith("#")
        )
        for write in ("INSERT", "UPDATE ", "DELETE", "CREATE ", "DROP "):
            assert write not in source.upper(), write

    def test_it_uses_the_dashboards_own_pool_and_ordering(self):
        # A diagnosis of a different sample than the product scores would
        # be worse than none.
        assert td.ORDER_BY == "sales_count DESC NULLS LAST"

    def test_the_endpoint_is_admin_only(self):
        from app.routers.admin import require_admin
        from app.routers.admin_reporting import router

        for route in router.routes:
            if route.path.endswith("/why-no-trades"):
                assert require_admin in [d.call for d in route.dependant.dependencies]
                break
        else:
            pytest.fail("no /why-no-trades route")
