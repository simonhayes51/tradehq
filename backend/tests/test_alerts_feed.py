# tests/test_alerts_feed.py
#
# The alert feed's whole job is to be honest and finite: never print an
# internal id at a user, never let one drifting card flood the feed, and
# never report a badge count that doesn't match what the user can see.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.alerts_feed import (
    SIGNIFICANT_MOVE_PCT, age_label, build_alert_item, card_label, collapse_repeats,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        "id": 1, "card_id": 117493814, "platform": "ps", "direction": "fall",
        "pct": -13.0, "price": 87_000, "ref_price": 100_000,
        "sent_at": NOW - timedelta(minutes=10), "read_at": None,
        "source": "futgg", "metric": "price",
    }
    base.update(overrides)
    return base


class TestCardLabel:
    def test_resolved_card_reads_as_a_player_not_an_id(self):
        assert card_label("Mbappé", 91, "team_of_the_week") == "Mbappé (91 Team Of The Week)"

    def test_internal_rarity_key_is_never_shown_raw(self):
        assert "_" not in card_label("Haaland", 98, "trailblazers_plus")

    def test_unresolved_card_degrades_to_words_never_to_the_id(self):
        # The reported bug was "Card 117493814 is down 13%". If the
        # catalogue lookup fails we still must not print the id.
        label = card_label(None, None, None)
        assert label == "A watched card"
        assert "117493814" not in label

    def test_placeholder_versions_are_dropped_rather_than_printed(self):
        assert card_label("Vini Jr", 90, "Card") == "Vini Jr (90)"


class TestBuildAlertItem:
    def test_title_carries_the_player_and_the_move(self):
        item = build_alert_item(_row(), card={"name": "Mbappé", "rating": 91, "version": "icon"}, now=NOW)
        assert item["title"] == "Mbappé (91 Icon) is down 13.0% (price)"
        assert item["movement"] == "100,000 → 87,000 coins"

    def test_no_raw_card_id_in_any_user_facing_string(self):
        item = build_alert_item(_row(), card=None, now=NOW)
        user_facing = " ".join([item["title"], item["movement"], item["whyItMatters"], item["age"]])
        assert "117493814" not in user_facing

    def test_age_is_reported_so_a_stale_alert_is_visible_as_stale(self):
        item = build_alert_item(_row(sent_at=NOW - timedelta(hours=30)), now=NOW)
        assert item["age"] == "Yesterday"

    def test_significant_moves_are_flagged_high_severity(self):
        small = build_alert_item(_row(pct=-4.0), now=NOW)
        large = build_alert_item(_row(pct=-(SIGNIFICANT_MOVE_PCT + 1)), now=NOW)
        assert small["severity"] == "medium"
        assert large["severity"] == "high"

    def test_liquidity_alerts_explain_liquidity_not_price(self):
        item = build_alert_item(_row(metric="liquidity", direction="fall", price=1.4, ref_price=None), now=NOW)
        assert "sell" in item["whyItMatters"]
        assert "coins" not in item["movement"]

    def test_unread_flag_tracks_read_at(self):
        assert build_alert_item(_row(read_at=None), now=NOW)["unread"] is True
        assert build_alert_item(_row(read_at=NOW), now=NOW)["unread"] is False

    def test_links_to_the_card_it_is_about(self):
        assert build_alert_item(_row(), now=NOW)["href"] == "/players/117493814"


class TestCollapseRepeats:
    def test_repeat_firings_on_one_card_become_one_entry(self):
        rows = [
            _row(id=3, sent_at=NOW - timedelta(minutes=5)),
            _row(id=2, sent_at=NOW - timedelta(minutes=40)),
            _row(id=1, sent_at=NOW - timedelta(minutes=80)),
        ]
        grouped = collapse_repeats(rows)
        assert len(grouped) == 1
        head, count = grouped[0]
        assert head["id"] == 3  # newest survives
        assert count == 3

    def test_opposite_directions_are_genuinely_different_news(self):
        rows = [_row(id=2, direction="rise", pct=9.0), _row(id=1, direction="fall")]
        assert len(collapse_repeats(rows)) == 2

    def test_a_move_days_later_is_its_own_entry_not_a_repeat(self):
        rows = [
            _row(id=2, sent_at=NOW - timedelta(minutes=5)),
            _row(id=1, sent_at=NOW - timedelta(days=3)),
        ]
        assert len(collapse_repeats(rows)) == 2

    def test_different_cards_are_never_collapsed_together(self):
        rows = [_row(id=2, card_id=1), _row(id=1, card_id=2)]
        assert len(collapse_repeats(rows)) == 2


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(seconds=10), "Just now"),
        (timedelta(minutes=20), "20 min ago"),
        (timedelta(hours=5), "5h ago"),
        (timedelta(days=4), "4 days ago"),
    ],
)
def test_age_label(delta, expected):
    assert age_label(NOW - delta, now=NOW) == expected
