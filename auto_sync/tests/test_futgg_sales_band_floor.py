# tests/test_futgg_sales_band_floor.py
#
# select_due() used to pick cards with a single priority-ordered query:
# never-read cards first (backfill), then by RATING band (95+, 85+,
# 80-84, 75-79, everything else), then by how overdue each card was.
# That ordering was right while backfill was still running, but
# production confirmed backfill exhausts ("0 never_read" in every
# recent coverage report) - and once it does, the fast bands (95+ due
# every 180min, 85+ every 720min) generate enough "due" volume on their
# own to fill an entire cycle's SELECT_LIMIT forever, so the tail
# (75-79, everything else - also the LARGEST population) never reached
# the front of the queue at all. select_due() first fixed this by
# reserving each RATING band a guaranteed minimum share of SELECT_LIMIT.
#
# That fix held throughput steady, but a second, deeper problem
# surfaced live once it did: the customer-facing "sales data is behind"
# signal was never judged against rating at all. It comes from
# card_priority.effective_tier (migration 044's adaptive-priority
# engine - CRITICAL/HIGH/NORMAL/LOW/DORMANT, scored on liquidity/
# volatility/opportunity, refreshed every 15 minutes independently of
# this worker). A rating-banded scheduler could run perfectly - clean
# cycles, climbing coverage, zero stale heartbeats - and STILL leave
# CRITICAL/HIGH mostly "stale" by the metric actually shown to users,
# because nothing in the rating-band schedule ever tried to hit their
# 15/60-minute intervals. Confirmed live: CRITICAL+HIGH accounted for
# most of a stale-sum that stayed over 50% of priced cards through a
# 6-hour stretch where rating-banded coverage was climbing steadily the
# whole time.
#
# select_due() now bands by card_priority.effective_tier instead of
# rating - same guaranteed-floor-then-overflow mechanism, different
# axis - so "due" and "reported stale" finally agree on every card.
# These tests exercise that against a fake connection - no live
# database - built to route each query to the right synthetic tier by
# recognising the same WHERE-clause fragments the production code
# actually emits (imported from the module, never duplicated, so the
# test cannot silently drift from what select_due() really sends).
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_sync as sales  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _card(card_id, tier, *, rating=75, due=True, never_read=False):
    now = datetime.now(timezone.utc)
    return {
        "source_card_id": card_id,
        "rating": rating,
        "next_sales_due_at": None if not due else now - timedelta(minutes=1),
        "source_url": f"https://www.fut.gg/players/{card_id}/",
        "sales_first_read_at": None if never_read else now - timedelta(days=1),
        "sales_consecutive_failures": 0,
        "effective_tier": tier,
    }


class FakeRecord(dict):
    """asyncpg.Record supports both getitem and attribute-free mapping
    access; select_due()'s own code only ever does row["source_card_id"]/
    row["effective_tier"]/etc, which a plain dict already satisfies."""


class FakeConn:
    """Routes each query to the synthetic population by recognising the
    exact WHERE-clause fragments select_due() emits - not a real SQL
    engine (there is no real card_priority join here; each synthetic
    card already carries its resolved "effective_tier"), just enough
    pattern matching to answer each of the shapes that function
    actually sends."""

    def __init__(self, population: list[dict]):
        self.population = population
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((sql, args))
        rows = list(self.population)

        if "NOT (source_card_id = ANY($1::bigint[]))" in sql:
            excluded, limit = args
            excluded_set = set(excluded)
            rows = [r for r in rows if r["source_card_id"] not in excluded_set]
            rows.sort(key=lambda r: (
                r["sales_first_read_at"] is not None,
                _tier_rank(r["effective_tier"]),
                r["next_sales_due_at"] or datetime.min.replace(tzinfo=timezone.utc),
            ))
            return [FakeRecord(r) for r in rows[:limit]]

        # A per-tier query: find which tier's WHERE fragment is present.
        for tier, condition in sales._TIER_WHERE.items():
            if condition in sql:
                (limit,) = args
                tier_rows = [r for r in rows if r["effective_tier"] == tier]
                tier_rows.sort(key=lambda r: (
                    r["sales_first_read_at"] is not None,
                    r["next_sales_due_at"] or datetime.min.replace(tzinfo=timezone.utc),
                ))
                return [FakeRecord(r) for r in tier_rows[:limit]]

        raise AssertionError(f"FakeConn could not route query: {sql[:200]}")


_TIER_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3, "DORMANT": 4}


def _tier_rank(tier):
    return _TIER_RANK.get(tier, 4)


class TestTierFloorsExist:
    def test_every_tier_has_a_positive_floor(self):
        assert all(share > 0 for share in sales._TIER_MIN_SHARE.values())

    def test_floors_leave_real_overflow_capacity(self):
        # Guaranteed floors must not consume the whole cycle, or the
        # "let a quiet tier's slack flow to whoever actually has the
        # backlog" property this was built to keep is gone.
        assert sum(sales._TIER_MIN_SHARE.values()) < 1.0

    def test_critical_and_high_get_the_largest_guaranteed_shares(self):
        # CORRECTED after live evidence. DORMANT used to get the
        # largest share, ported straight from the old rating-band
        # allocation - but DORMANT's interval (SALES_INTERVALS) is now
        # loose enough (4000min) that a small floor keeps it satisfied,
        # while CRITICAL/HIGH's much tighter intervals (180/720min) for
        # populations in the thousands need real guaranteed throughput.
        # Confirmed live: under the old shares, DORMANT's stale count
        # fell every reading while CRITICAL/HIGH/NORMAL's kept rising
        # despite already-achievable interval targets - a 20-card
        # CRITICAL floor per cycle was never going to keep pace with a
        # population in the thousands. CRITICAL and HIGH must now be
        # guaranteed at least as much as any other single tier.
        critical_share = sales._TIER_MIN_SHARE["CRITICAL"]
        high_share = sales._TIER_MIN_SHARE["HIGH"]
        for tier, share in sales._TIER_MIN_SHARE.items():
            if tier in ("CRITICAL", "HIGH"):
                continue
            assert critical_share >= share, f"CRITICAL's floor must be >= {tier}'s"
            assert high_share >= share, f"HIGH's floor must be >= {tier}'s"

    def test_dormant_no_longer_dominates_the_floor(self):
        # The specific regression this correction guards against: DORMANT
        # silently creeping back to the largest share (e.g. someone
        # "restoring" the old allocation without re-checking it against
        # the new, much looser DORMANT interval) would reproduce the
        # exact starvation this fix corrected, just for CRITICAL/HIGH
        # instead of DORMANT.
        assert sales._TIER_MIN_SHARE["DORMANT"] <= sales._TIER_MIN_SHARE["CRITICAL"]
        assert sales._TIER_MIN_SHARE["DORMANT"] <= sales._TIER_MIN_SHARE["HIGH"]

    def test_every_tier_has_a_where_clause(self):
        assert set(sales._TIER_WHERE) == {"CRITICAL", "HIGH", "NORMAL", "LOW", "DORMANT"}

    def test_min_share_and_where_cover_the_same_tiers(self):
        # No gap (a tier with a floor but no WHERE fragment to select
        # it) and no orphan (a WHERE fragment for a tier select_due()
        # never reserves a floor for).
        assert set(sales._TIER_MIN_SHARE) == set(sales._TIER_WHERE)

    def test_each_tier_condition_names_only_that_tier(self):
        # COALESCE(cp.effective_tier, 'NORMAL') = '<TIER>' - an
        # unscored card must fall back to NORMAL on both the scheduling
        # side here and market_data_provider.py's staleness-reporting
        # side, or the two silently disagree about which cards are due.
        for tier, condition in sales._TIER_WHERE.items():
            assert condition == f"COALESCE(cp.effective_tier, 'NORMAL') = '{tier}'"


class TestSelectDueGuaranteesTheTail:
    def test_a_flooded_fast_tier_no_longer_shuts_out_the_tail(self):
        # The exact production shape: thousands of CRITICAL/HIGH cards
        # due at once (a whole cycle's worth on their own), plus a
        # handful of DORMANT cards also due. Without the floor, DORMANT
        # gets zero.
        population = (
            [_card(1000 + i, "CRITICAL") for i in range(500)]
            + [_card(2000 + i, "DORMANT") for i in range(20)]
        )
        conn = FakeConn(population)
        sales.SELECT_LIMIT = 100
        try:
            rows = run(sales.select_due(conn))
        finally:
            sales.SELECT_LIMIT = sales.env_int("FUTGG_SALES_SELECT_LIMIT", 200, minimum=1)
        dormant_ids = {r["source_card_id"] for r in rows if r["effective_tier"] == "DORMANT"}
        assert dormant_ids, "DORMANT got nothing even with a guaranteed floor"

    def test_total_selected_never_exceeds_select_limit(self):
        population = (
            [_card(1000 + i, "CRITICAL") for i in range(1000)]
            + [_card(2000 + i, "DORMANT") for i in range(1000)]
        )
        conn = FakeConn(population)
        sales.SELECT_LIMIT = 137
        try:
            rows = run(sales.select_due(conn))
        finally:
            sales.SELECT_LIMIT = sales.env_int("FUTGG_SALES_SELECT_LIMIT", 200, minimum=1)
        assert len(rows) <= 137

    def test_no_card_is_selected_twice(self):
        population = (
            [_card(1000 + i, "CRITICAL") for i in range(50)]
            + [_card(2000 + i, "DORMANT") for i in range(50)]
        )
        conn = FakeConn(population)
        rows = run(sales.select_due(conn))
        ids = [r["source_card_id"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_never_read_cards_still_come_first_within_their_tier(self):
        population = [
            _card(1, "CRITICAL", never_read=False),
            _card(2, "CRITICAL", never_read=True),
        ]
        conn = FakeConn(population)
        rows = run(sales.select_due(conn))
        ids = [r["source_card_id"] for r in rows]
        assert ids.index(2) < ids.index(1)

    def test_a_quiet_fast_tier_lets_overflow_reach_deeper_into_the_tail(self):
        # No CRITICAL/HIGH/NORMAL/LOW cards due at all - every slot
        # beyond the tail's own floor should still be usable by DORMANT
        # via overflow, not wasted.
        sales.SELECT_LIMIT = 50
        try:
            population = [_card(2000 + i, "DORMANT") for i in range(200)]
            conn = FakeConn(population)
            rows = run(sales.select_due(conn))
        finally:
            sales.SELECT_LIMIT = sales.env_int("FUTGG_SALES_SELECT_LIMIT", 200, minimum=1)
        assert len(rows) == 50

    def test_every_selected_row_carries_its_tier(self):
        # apply_batch_results() needs effective_tier on every row to
        # schedule the card's NEXT read via sales_interval_minutes() -
        # a row missing it would silently fall back to NORMAL cadence
        # regardless of how urgent the card actually is.
        population = [_card(1, "CRITICAL"), _card(2, "DORMANT")]
        conn = FakeConn(population)
        rows = run(sales.select_due(conn))
        assert {r["source_card_id"]: r["effective_tier"] for r in rows} == {
            1: "CRITICAL", 2: "DORMANT",
        }
