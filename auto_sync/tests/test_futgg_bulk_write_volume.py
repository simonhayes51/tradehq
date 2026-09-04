"""
Tests for futgg_bulk_price_sync.py's write-volume controls.

WHY THIS EXISTS
---------------
Measured in production (2026-08-11): futgg_bin_history held 14.8M rows /
1.94 GB, growing ~2.1M rows/day, with 91.9% of writes recording the same
price as the previous observation. Postgres was visibly suffering under
it - a 269-second checkpoint, vacuum unable to lock
futgg_market_snapshot, and SalesSync's db_write p95 at 57 SECONDS against
a 235ms median while its actual network fetching took 4 seconds a cycle.

No database and no network here: the selection logic is a pure function.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_bulk_price_sync as bulk  # noqa: E402

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_state():
    bulk._last_written.clear()
    yield
    bulk._last_written.clear()


class TestChangeOnlyWrites:
    def test_the_first_observation_of_a_card_is_always_written(self):
        recs, changed, hb = bulk.select_rows_to_write([(1, 10000)], NOW)
        assert len(recs) == 1 and changed == 1 and hb == 0

    def test_an_unchanged_price_is_skipped(self):
        bulk.select_rows_to_write([(1, 10000)], NOW)
        recs, changed, hb = bulk.select_rows_to_write(
            [(1, 10000)], NOW + timedelta(minutes=5),
        )
        assert recs == [] and changed == 0 and hb == 0

    def test_a_changed_price_is_always_written_immediately(self):
        """Cost control must never delay a real price move - the entire
        BIN-first idea depends on seeing changes promptly."""
        bulk.select_rows_to_write([(1, 10000)], NOW)
        recs, changed, _ = bulk.select_rows_to_write(
            [(1, 11000)], NOW + timedelta(seconds=30),
        )
        assert len(recs) == 1 and changed == 1

    def test_a_quiet_card_still_gets_a_heartbeat(self):
        """Without this, bin_market_signal's observation counts collapse
        and a perfectly stable card reports INSUFFICIENT_BIN_HISTORY -
        breaking the signal for exactly the cards whose price is most
        settled."""
        bulk.select_rows_to_write([(1, 10000)], NOW)
        later = NOW + timedelta(minutes=bulk._heartbeat_minutes_for(1) + 1)
        recs, changed, hb = bulk.select_rows_to_write([(1, 10000)], later)
        assert len(recs) == 1 and changed == 0 and hb == 1

    def test_disabling_the_feature_writes_everything(self, monkeypatch):
        monkeypatch.setattr(bulk, "CHANGE_ONLY_WRITES", False)
        bulk.select_rows_to_write([(1, 10000)], NOW)
        recs, changed, hb = bulk.select_rows_to_write(
            [(1, 10000)], NOW + timedelta(seconds=1),
        )
        assert len(recs) == 1 and hb == 0


class TestPriceChangeDetail:
    """_last_price_changes - what a future triggered-detailed-sales
    consumer (Part 5) reads to learn WHICH cards changed price this
    cycle, not just how many. Exposed alongside select_rows_to_write()'s
    long-established (records, changed, heartbeat) return rather than
    widening it - see that function's own comment."""

    def test_a_genuine_change_is_captured_with_old_and_new_price(self):
        bulk.select_rows_to_write([(1, 10000)], NOW)
        bulk.select_rows_to_write([(1, 12500)], NOW + timedelta(minutes=5))
        assert bulk._last_price_changes == [(1, 10000, 12500)]

    def test_a_first_observation_is_not_treated_as_a_price_change(self):
        # There is no PREVIOUS price to compare against - this is a card
        # being seen for the first time, not one whose price moved.
        bulk.select_rows_to_write([(1, 10000)], NOW)
        assert bulk._last_price_changes == []

    def test_a_heartbeat_rewrite_is_not_a_price_change(self):
        bulk.select_rows_to_write([(1, 10000)], NOW)
        later = NOW + timedelta(minutes=bulk._heartbeat_minutes_for(1) + 1)
        bulk.select_rows_to_write([(1, 10000)], later)
        assert bulk._last_price_changes == []

    def test_an_unchanged_price_leaves_no_stale_change_from_a_prior_call(self):
        # Each call must refresh the list, not accumulate across cycles -
        # a caller reading it after an unchanged cycle must not see a
        # change from two cycles ago.
        bulk.select_rows_to_write([(1, 10000)], NOW)
        bulk.select_rows_to_write([(1, 12500)], NOW + timedelta(minutes=5))
        assert bulk._last_price_changes  # sanity: the change above was captured
        bulk.select_rows_to_write([(1, 12500)], NOW + timedelta(minutes=6))
        assert bulk._last_price_changes == []

    def test_multiple_genuine_changes_in_one_cycle_are_all_captured(self):
        bulk.select_rows_to_write([(1, 10000), (2, 20000)], NOW)
        bulk.select_rows_to_write(
            [(1, 11000), (2, 20000), (3, 30000)], NOW + timedelta(minutes=5),
        )
        # card 1 changed, card 2 unchanged, card 3 is a first observation.
        assert bulk._last_price_changes == [(1, 10000, 11000)]

    def test_disabling_change_only_writes_reports_no_price_changes(self, monkeypatch):
        monkeypatch.setattr(bulk, "CHANGE_ONLY_WRITES", False)
        bulk.select_rows_to_write([(1, 10000)], NOW)
        bulk.select_rows_to_write([(1, 11000)], NOW + timedelta(minutes=5))
        assert bulk._last_price_changes == []


class TestHeartbeatsAreStaggered:
    def test_cards_do_not_all_heartbeat_on_the_same_cycle(self):
        """A single shared interval turns the removed writes back into one
        ~10,000-row spike every heartbeat - the same burst, less often,
        and worse for lock contention than a steady trickle."""
        intervals = {bulk._heartbeat_minutes_for(c) for c in range(1, 500)}
        assert len(intervals) > 1

    def test_the_stagger_is_deterministic(self):
        # A card's write schedule must be reproducible, and a restart must
        # not re-align every card onto the same cycle again.
        assert bulk._heartbeat_minutes_for(4242) == bulk._heartbeat_minutes_for(4242)

    def test_no_card_waits_less_than_the_configured_heartbeat(self):
        for card_id in range(1, 500):
            assert bulk._heartbeat_minutes_for(card_id) >= bulk.HEARTBEAT_MINUTES

    def test_the_peak_cycle_is_well_below_the_whole_catalogue(self):
        cards = [(c, 10000) for c in range(1, 10001)]
        bulk.select_rows_to_write(cards, NOW)
        peak = 0
        for step in range(1, 25):
            recs, _, _ = bulk.select_rows_to_write(cards, NOW + timedelta(minutes=5 * step))
            peak = max(peak, len(recs))
        assert peak < len(cards)

    def test_total_write_volume_falls_substantially(self):
        cards = [(c, 10000) for c in range(1, 10001)]
        bulk.select_rows_to_write(cards, NOW)
        written = sum(
            len(bulk.select_rows_to_write(cards, NOW + timedelta(minutes=5 * s))[0])
            for s in range(1, 13)
        )
        unchanged_always = len(cards) * 12
        assert written < unchanged_always * 0.35


class TestRetentionSettings:
    def test_retention_covers_more_than_the_signal_ever_reads(self):
        # bin_market_signal reads at most a 7-day window.
        assert bulk.BIN_HISTORY_RETENTION_DAYS == 0 or bulk.BIN_HISTORY_RETENTION_DAYS >= 7

    def test_pruning_is_chunked_rather_than_one_huge_delete(self):
        # An unbounded DELETE over millions of rows is precisely the
        # long-running lock-holder this work exists to eliminate.
        source = Path(bulk.__file__).read_text()
        prune = source.split("async def prune_bin_history")[1].split("def decode_ids")[0]
        assert "LIMIT $2" in prune
        assert "max_chunks" in prune
