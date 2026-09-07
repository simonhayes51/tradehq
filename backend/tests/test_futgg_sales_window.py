from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.futgg_sales_window import (
    VALUATION_SALES_WINDOW_DAYS,
    filter_valuation_sales,
)

NOW = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)


def test_valuation_window_matches_seven_day_snapshot_definition():
    assert VALUATION_SALES_WINDOW_DAYS == 7

    sales = [
        {"sold_price": 100_000, "approximate_sold_at": NOW - timedelta(days=1)},
        {"sold_price": 101_000, "approximate_sold_at": NOW - timedelta(days=6, hours=23)},
        {"sold_price": 99_000, "approximate_sold_at": NOW - timedelta(days=7, minutes=1)},
    ]

    kept = filter_valuation_sales(sales, as_of=NOW)
    assert [row["sold_price"] for row in kept] == [100_000, 101_000]


def test_undated_sales_are_not_allowed_into_trend_evidence():
    kept = filter_valuation_sales(
        [
            {"sold_price": 100_000, "approximate_sold_at": None},
            {"sold_price": 101_000, "approximate_sold_at": NOW - timedelta(minutes=5)},
        ],
        as_of=NOW,
    )
    assert [row["sold_price"] for row in kept] == [101_000]


def test_naive_source_timestamps_are_treated_as_utc():
    naive = (NOW - timedelta(hours=2)).replace(tzinfo=None)
    kept = filter_valuation_sales(
        [{"sold_price": 100_000, "approximate_sold_at": naive}],
        as_of=NOW,
    )
    assert len(kept) == 1
