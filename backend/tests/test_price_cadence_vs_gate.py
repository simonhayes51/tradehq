# tests/test_price_cadence_vs_gate.py
#
# WHY THIS EXISTS
# ---------------
# market_data_provider.py's EXPECTED_PRICE_INTERVAL_MINUTES decides
# whether a card counts as "stale" for cards_stale/stale_by_price_tier -
# which feeds both readiness.py's health check and the customer-facing
# "Our price data is behind" message (EmptyFeedReason.jsx). It was set
# assuming auto_sync/futgg_bulk_price_sync.py writes a fresh
# price_updated_at for the WHOLE catalogue every FUTGG_BULK_INTERVAL_
# SECONDS. True for the feed FETCH, false for the database WRITE that
# threshold actually measures: that worker's CHANGE_ONLY_WRITES
# optimization (default on) skips writing - and skips bumping
# price_updated_at - for any card whose price has not changed and whose
# own per-card heartbeat window has not elapsed. Reported live: "Our
# price data is behind" started firing (cards_stale at 56.7% of priced)
# right after an unrelated fix stopped a DIFFERENT gate (sales
# staleness) from firing first and masking this one - the collector's
# own heartbeat showed ok=true, cycle=40, coverage_pct=100.0 the whole
# time. The threshold demanded a write cadence the collector was
# deliberately never going to hit for most of the catalogue.
#
# EXPECTED_PRICE_INTERVAL_MINUTES now derives from the worst-case gap
# between forced per-card writes (2*HEARTBEAT_MINUTES - 1, per that
# worker's own _heartbeat_minutes_for spread) plus one bulk-fetch cycle
# of slack - not a re-guess, a value read off the actual write-
# scheduling code. HEARTBEAT_MINUTES/FUTGG_BULK_INTERVAL_SECONDS are
# duplicated (same env var names, same defaults) rather than imported:
# this worker deploys separately from the backend and cannot import
# app.services. The tests below assert the two defaults stay equal, so
# drift fails CI rather than silently reintroducing this exact
# contradiction one more time.
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "auto_sync"))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_bulk_price_sync as bps  # noqa: E402

from app.services import market_data_provider as mdp  # noqa: E402


class TestTheMirroredConstantsDoNotDrift:
    def test_heartbeat_minutes_equals_the_auto_sync_worker(self):
        assert mdp._BULK_PRICE_HEARTBEAT_MINUTES == bps.HEARTBEAT_MINUTES, (
            "market_data_provider's mirror of HEARTBEAT_MINUTES has drifted "
            "from futgg_bulk_price_sync.py. Update both, or the freshness "
            "gate goes back to demanding a write cadence the collector was "
            "never going to hit."
        )

    def test_bulk_interval_seconds_equals_the_auto_sync_worker(self):
        assert mdp._BULK_PRICE_INTERVAL_SECONDS == bps.INTERVAL_SECONDS


class TestExpectedPriceIntervalReflectsTheRealWriteCadence:
    def test_it_covers_the_worst_case_heartbeat_spread(self):
        """A card whose price never changes still gets price_updated_at
        bumped at least once every 2*HEARTBEAT_MINUTES - 1 minutes (see
        _heartbeat_minutes_for's card_id % HEARTBEAT_MINUTES spread) -
        the threshold must not be tighter than that or every stable-
        priced card in the catalogue cycles in and out of "stale" purely
        from this optimization, never from anything actually wrong."""
        worst_case_heartbeat_gap = 2 * bps.HEARTBEAT_MINUTES - 1
        assert mdp.EXPECTED_PRICE_INTERVAL_MINUTES > worst_case_heartbeat_gap

    def test_it_matches_the_documented_formula(self):
        expected = (2 * bps.HEARTBEAT_MINUTES - 1) + (bps.INTERVAL_SECONDS / 60.0)
        assert mdp.EXPECTED_PRICE_INTERVAL_MINUTES == expected

    def test_no_card_id_produces_a_heartbeat_gap_the_threshold_misses(self):
        """Exhaustive over the actual spread formula, not just the
        theoretical worst case - a regression in the modulo arithmetic
        (e.g. an off-by-one) would otherwise slip through the single
        worst-case check above."""
        for card_id in range(0, bps.HEARTBEAT_MINUTES * 3):
            gap = bps._heartbeat_minutes_for(card_id)
            assert gap <= mdp.EXPECTED_PRICE_INTERVAL_MINUTES - (bps.INTERVAL_SECONDS / 60.0)
