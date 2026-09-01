# tests/test_futgg_sales_band_floor.py
#
# select_due() used to pick cards with a single priority-ordered query:
# never-read cards first (backfill), then by rating band (95+, 85+,
# 80-84, 75-79, everything else), then by how overdue each card was.
# That ordering is right while backfill is still running, but production
# confirmed backfill exhausts ("0 never_read" in every recent coverage
# report) - and once it does, the fast bands (95+ due every 180min, 85+
# every 720min) generate enough "due" volume on their own to fill an
# entire cycle's SELECT_LIMIT forever, so the tail (75-79, everything
# else - also the LARGEST population) never reaches the front of the
# queue at all. Real sales coverage held flat across 1.5+ hours of
# otherwise-healthy continuous running because of exactly this: cycles
# kept refreshing cards that already counted while the tail's sales aged
# out of the 14-day freshness window unopposed.
#
# select_due() now reserves each rating band a guaranteed minimum share
# of SELECT_LIMIT before filling the rest by the original priority
# order. These tests exercise that against a fake connection - no live
# database - built to route each query to the right synthetic band by
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


def _card(card_id, rating, *, due=True, never_read=False):
    now = datetime.now(timezone.utc)
    return {
        "source_card_id": card_id,
        "rating": rating,
        "next_sales_due_at": None if not due else now - timedelta(minutes=1),
        "source_url": f"https://www.fut.gg/players/{card_id}/",
        "sales_first_read_at": None if never_read else now - timedelta(days=1),
        "sales_consecutive_failures": 0,
    }


class FakeRecord(dict):
    """asyncpg.Record supports both getitem and attribute-free mapping
    access; select_due()'s own code only ever does row["source_card_id"],
    which a plain dict already satisfies."""


class FakeConn:
    """Routes each query to the synthetic population by recognising the
    exact WHERE-clause fragments select_due() emits - not a real SQL
    engine, just enough pattern matching to answer each of the shapes
    that function actually sends."""

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
                _band_of(r["rating"]),
                r["next_sales_due_at"] or datetime.min.replace(tzinfo=timezone.utc),
            ))
            return [FakeRecord(r) for r in rows[:limit]]

        # A per-band query: find which band's WHERE fragment is present.
        for band, condition in sales._BAND_WHERE.items():
            if condition in sql:
                (limit,) = args
                band_rows = [r for r in rows if _band_of(r["rating"]) == band]
                band_rows.sort(key=lambda r: (
                    r["sales_first_read_at"] is not None,
                    r["next_sales_due_at"] or datetime.min.replace(tzinfo=timezone.utc),
                ))
                return [FakeRecord(r) for r in band_rows[:limit]]

        raise AssertionError(f"FakeConn could not route query: {sql[:200]}")


def _band_of(rating):
    if rating >= 95:
        return 0
    if rating >= 85:
        return 1
    if rating >= 80:
        return 2
    if rating >= 75:
        return 3
    return 4


class TestBandFloorsExist:
    def test_every_band_has_a_positive_floor(self):
        assert all(share > 0 for share in sales._BAND_MIN_SHARE.values())

    def test_floors_leave_real_overflow_capacity(self):
        # Guaranteed floors must not consume the whole cycle, or the
        # "let a quiet band's slack flow to whoever actually has the
        # backlog" property this was built to keep is gone.
        assert sum(sales._BAND_MIN_SHARE.values()) < 1.0

    def test_the_tail_gets_the_largest_guaranteed_share(self):
        # Band 4 ("everything else") is both the largest population and
        # the one production evidence showed being starved - it must be
        # guaranteed more than any single faster band, not just a token
        # allocation.
        tail_share = sales._BAND_MIN_SHARE[4]
        assert all(tail_share >= share for band, share in sales._BAND_MIN_SHARE.items() if band != 4)

    def test_band_where_clauses_cover_every_rating_exactly_once(self):
        # No gap (a rating that matches no band, silently excluded
        # forever) and no overlap (a rating whose floor gets double-
        # counted against SELECT_LIMIT).
        for rating in [0, 40, 74, 75, 79, 80, 84, 85, 94, 95, 99, None]:
            matches = [b for b, cond in sales._BAND_WHERE.items() if _sql_matches(cond, rating)]
            assert len(matches) == 1, f"rating={rating} matched bands {matches}"


def _sql_matches(condition: str, rating) -> bool:
    """Tiny evaluator for the fixed set of conditions _BAND_WHERE
    actually contains - not a general SQL parser, just enough to check
    the five known shapes against a sample rating."""
    if rating is None:
        return "IS NULL" in condition
    if condition == "rating >= 95":
        return rating >= 95
    if condition == "rating >= 85 AND rating < 95":
        return 85 <= rating < 95
    if condition == "rating >= 80 AND rating < 85":
        return 80 <= rating < 85
    if condition == "rating >= 75 AND rating < 80":
        return 75 <= rating < 80
    if condition == "(rating < 75 OR rating IS NULL)":
        return rating < 75
    raise AssertionError(f"unrecognised band condition: {condition}")


class TestSelectDueGuaranteesTheTail:
    def test_a_flooded_fast_band_no_longer_shuts_out_the_tail(self):
        # The exact production shape: thousands of 95+/85+ cards due at
        # once (a whole cycle's worth on their own), plus a handful of
        # band-4 cards also due. Without the floor, band 4 gets zero.
        population = (
            [_card(1000 + i, 96) for i in range(500)]
            + [_card(2000 + i, 60) for i in range(20)]
        )
        conn = FakeConn(population)
        sales.SELECT_LIMIT = 100
        try:
            rows = run(sales.select_due(conn))
        finally:
            sales.SELECT_LIMIT = sales.env_int("FUTGG_SALES_SELECT_LIMIT", 200, minimum=1)
        band4_ids = {r["source_card_id"] for r in rows if r["rating"] < 75}
        assert band4_ids, "band 4 got nothing even with a guaranteed floor"

    def test_total_selected_never_exceeds_select_limit(self):
        population = [_card(1000 + i, 96) for i in range(1000)] + [_card(2000 + i, 60) for i in range(1000)]
        conn = FakeConn(population)
        sales.SELECT_LIMIT = 137
        try:
            rows = run(sales.select_due(conn))
        finally:
            sales.SELECT_LIMIT = sales.env_int("FUTGG_SALES_SELECT_LIMIT", 200, minimum=1)
        assert len(rows) <= 137

    def test_no_card_is_selected_twice(self):
        population = [_card(1000 + i, 96) for i in range(50)] + [_card(2000 + i, 60) for i in range(50)]
        conn = FakeConn(population)
        rows = run(sales.select_due(conn))
        ids = [r["source_card_id"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_never_read_cards_still_come_first_within_their_band(self):
        population = [_card(1, 96, never_read=False), _card(2, 96, never_read=True)]
        conn = FakeConn(population)
        rows = run(sales.select_due(conn))
        ids = [r["source_card_id"] for r in rows]
        assert ids.index(2) < ids.index(1)

    def test_a_quiet_fast_band_lets_overflow_reach_deeper_into_the_tail(self):
        # No 95+/85+/80-84/75-79 cards due at all - every slot beyond
        # the tail's own floor should still be usable by the tail via
        # overflow, not wasted.
        sales.SELECT_LIMIT = 50
        try:
            population = [_card(2000 + i, 60) for i in range(200)]
            conn = FakeConn(population)
            rows = run(sales.select_due(conn))
        finally:
            sales.SELECT_LIMIT = sales.env_int("FUTGG_SALES_SELECT_LIMIT", 200, minimum=1)
        assert len(rows) == 50
