"""
The engine's price-freshness gate must read "when did we last CHECK this
price", not "when did this price last CHANGE".

WHY THIS EXISTS
---------------
Those were the same event until futgg_bulk_price_sync.py started skipping
writes for unchanged prices. After that change, futgg_bin_history - and
therefore futgg_market_snapshot.bin_captured_at, which is max(captured_at)
per card - only advances when a price MOVES or when a per-card heartbeat
falls due at FUTGG_BULK_HEARTBEAT_MINUTES + (card_id % that): up to 29
minutes at the default settings.

futgg_config.max_price_age_minutes_by_tier gives special-tier cards 20
minutes. So every special-tier card whose price was merely stable failed
the gate, despite the bulk feed confirming that exact price every six
minutes. Production logged the result for hours, unchanged:

    futgg scan: evaluated=1344 coverage=13.34% recommendations=0

Reproduced end-to-end against a real Postgres before this file was
written: a special-tier card at a 58,000 BIN last changed 25 minutes ago
and last confirmed 3 minutes ago returned `insufficient_data` /
`stale_market` on the old clock and `watch` (then `buy`, at a 52,000 BIN)
on the new one.

Migration 052 adds the second clock as bin_checked_at. These tests pin
the preference order, the fallback, and the actual gate outcome.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.futgg_config import ENGINE_CONFIG
from app.services.futgg_intelligence import _price_observed_at, evaluate_card

NOW = datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)


def _sales(count: int = 30, price: int = 66_000):
    """A clean, liquid, low-dispersion series, so nothing but the
    price-age gate is ever in play in these tests."""
    return [
        {
            "sold_price": price + (i * 37) % 900,
            "approximate_sold_at": NOW - timedelta(minutes=i + 1),
        }
        for i in range(count)
    ]


def _snapshot(**overrides):
    snap = {
        "source_card_id": 134473924,
        "name": "Stable Special",
        "rating": 91,
        "price_tier": "special",
        "is_tradeable": True,
        "current_bin": 52_000,
        "sales_count": 30,
        "sales_median": 66_000,
        "sales_trimmed_mean": 66_000,
        "sales_low": 65_000,
        "sales_high": 67_000,
        "sales_dispersion_ratio": 0.02,
        "sales_interdecile_ratio": 0.03,
        "sales_window_span_minutes": 30.0,
        "latest_sale_at": NOW - timedelta(minutes=1),
    }
    snap.update(overrides)
    return snap


class TestWhichClockIsRead:
    def test_bin_checked_at_wins_when_present(self):
        checked = NOW - timedelta(minutes=3)
        captured = NOW - timedelta(minutes=25)
        assert _price_observed_at(
            {"bin_checked_at": checked, "bin_captured_at": captured}
        ) == checked

    def test_falls_back_to_bin_captured_at_when_absent(self):
        """A database still on migration 050's view shape has no
        bin_checked_at column at all. Behave exactly as before rather
        than reading every card as unpriced."""
        captured = NOW - timedelta(minutes=25)
        assert _price_observed_at({"bin_captured_at": captured}) == captured

    def test_falls_back_when_the_column_exists_but_is_null(self):
        """052 backfills price_checked_at from price_updated_at, but
        deliberately leaves it NULL for cards last seen with no market.
        Those must not read as unpriced either."""
        captured = NOW - timedelta(minutes=25)
        assert _price_observed_at(
            {"bin_checked_at": None, "bin_captured_at": captured}
        ) == captured

    def test_no_clock_at_all_is_still_none(self):
        assert _price_observed_at({}) is None


class TestTheGateOutcome:
    """The behaviour that actually cost recommendations, end to end."""

    STALE_CAPTURED = NOW - timedelta(minutes=25)
    FRESH_CHECKED = NOW - timedelta(minutes=3)

    def test_a_stable_special_card_is_no_longer_rejected_as_stale(self):
        ci = evaluate_card(
            _snapshot(
                bin_captured_at=self.STALE_CAPTURED,
                bin_checked_at=self.FRESH_CHECKED,
            ),
            as_of=NOW,
            sales=_sales(),
        )
        assert ci.price_age_minutes == 3
        assert "stale_market" not in (ci.blocking_codes or [])
        assert ci.signal != "insufficient_data"

    def test_the_same_card_without_the_new_clock_is_rejected(self):
        """Pins the regression itself, so this cannot silently come back
        if the preference order is ever reversed."""
        ci = evaluate_card(
            _snapshot(bin_captured_at=self.STALE_CAPTURED),
            as_of=NOW,
            sales=_sales(),
        )
        assert ci.price_age_minutes == 25
        assert "stale_market" in (ci.blocking_codes or [])
        assert ci.signal == "insufficient_data"

    def test_a_genuinely_unchecked_card_is_still_rejected(self):
        """The gate must keep working. A card the feed has not confirmed
        for well past its tier limit is still stale - the fix changes
        which clock is read, not whether staleness blocks."""
        limit = ENGINE_CONFIG.max_price_age_for_tier("special")
        ci = evaluate_card(
            _snapshot(
                bin_captured_at=NOW - timedelta(minutes=limit * 4),
                bin_checked_at=NOW - timedelta(minutes=limit * 3),
            ),
            as_of=NOW,
            sales=_sales(),
        )
        assert "stale_market" in (ci.blocking_codes or [])
        assert ci.signal == "insufficient_data"

    @pytest.mark.parametrize("tier", ["special", "gold_rare", "gold_common"])
    def test_the_worst_case_heartbeat_lag_clears_every_tier_limit(self, tier):
        """The specific arithmetic that broke: the bulk sync's heartbeat
        can leave bin_captured_at up to HEARTBEAT_MINUTES + (card_id %
        HEARTBEAT_MINUTES) old - 29 minutes at the 15-minute default -
        which exceeds the 20-minute special-tier limit outright. Against
        bin_checked_at the age is one bulk cycle, and every tier passes."""
        worst_case_heartbeat_lag = 29
        one_bulk_cycle = 6
        assert worst_case_heartbeat_lag > ENGINE_CONFIG.max_price_age_for_tier("special")

        ci = evaluate_card(
            _snapshot(
                price_tier=tier,
                bin_captured_at=NOW - timedelta(minutes=worst_case_heartbeat_lag),
                bin_checked_at=NOW - timedelta(minutes=one_bulk_cycle),
            ),
            as_of=NOW,
            sales=_sales(),
        )
        assert "stale_market" not in (ci.blocking_codes or [])
