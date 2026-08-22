"""
Parser tests for the FUT.GG release-metadata fields:
  * released_at   - the "Added On" timestamp
  * display_name  - the SHORT name from the player-profile anchor
  * full_name     - the Player Information "Name" row

Uses representative HTML shaped like the real Player Information panel
(label/value pairs), NOT generated Tailwind classes - the parser is
deliberately structural so a restyle cannot break it, and these fixtures
must exercise that same structural path.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from futgg_common import parse_added_on, parse_futgg_card

CARD_URL = "https://www.fut.gg/players/212198-bruno-fernandes/26-100875494/"


def _page(
    *,
    name="Bruno Miguel Borges Fernandes",
    added_on="Mar 18, 2026, 6:00 PM",
    display_anchor='<a href="/players/212198-bruno-fernandes/">Bruno Fernandes</a>',
    rarity="Team of the Week",
    squad="TOTW 27",
):
    rows = [f"<li><span>Name</span><span>{name}</span></li>"] if name else []
    if rarity:
        rows.append(f"<li><span>Rarity</span><span>{rarity}</span></li>")
    if squad:
        rows.append(f"<li><span>Squad</span><span>{squad}</span></li>")
    rows.append("<li><span>Player ID</span><span>212198</span></li>")
    rows.append("<li><span>Item ID</span><span>100875494</span></li>")
    if added_on:
        rows.append(f"<li><span>Added On</span><span>{added_on}</span></li>")
    return f"""<html><body>
      {display_anchor}
      <div><h2>Player Information</h2><ul>{''.join(rows)}</ul></div>
    </body></html>"""


class TestAddedOnParsing:
    def test_parses_the_documented_format(self):
        assert parse_added_on("Mar 18, 2026, 6:00 PM") == datetime(
            2026, 3, 18, 18, 0, tzinfo=timezone.utc
        )

    def test_parses_a_full_month_name(self):
        assert parse_added_on("March 18, 2026, 6:00 PM") == datetime(
            2026, 3, 18, 18, 0, tzinfo=timezone.utc
        )

    def test_parses_a_date_without_a_time(self):
        assert parse_added_on("Sep 26, 2025") == datetime(2025, 9, 26, 0, 0, tzinfo=timezone.utc)

    def test_handles_non_breaking_space_before_meridiem(self):
        """Some renderers emit U+202F/U+00A0 around the AM/PM, which would
        otherwise defeat an exact strptime match."""
        assert parse_added_on("Mar 18, 2026, 6:00 PM") == datetime(
            2026, 3, 18, 18, 0, tzinfo=timezone.utc
        )

    def test_midnight_and_noon_meridiem_are_not_confused(self):
        assert parse_added_on("Mar 18, 2026, 12:00 AM").hour == 0
        assert parse_added_on("Mar 18, 2026, 12:00 PM").hour == 12

    @pytest.mark.parametrize("value", [None, "", "   ", "not a date", "18/03/2026", "soon"])
    def test_unparseable_values_return_none_never_a_guess(self, value):
        """A fabricated timestamp would silently corrupt the entire
        promo/event timeline built on top of this field."""
        assert parse_added_on(value) is None

    def test_result_is_always_timezone_aware(self):
        parsed = parse_added_on("Mar 18, 2026, 6:00 PM")
        assert parsed.tzinfo is not None

    def test_assumed_timezone_is_configurable(self):
        """The UTC default is correct because the scraper pins the browser
        timezone - but the assumption must remain overridable if FUT.GG is
        ever shown to render in a different fixed zone."""
        from datetime import timedelta
        cet = timezone(timedelta(hours=1))
        parsed = parse_added_on("Mar 18, 2026, 6:00 PM", assume_timezone=cet)
        assert parsed.utcoffset() == timedelta(hours=1)
        # Same wall-clock reading, different instant.
        assert parsed.hour == 18


class TestCardParsing:
    def test_extracts_all_release_metadata(self):
        card = parse_futgg_card(_page(), CARD_URL)
        assert card.full_name == "Bruno Miguel Borges Fernandes"
        assert card.display_name == "Bruno Fernandes"
        assert card.rarity == "Team of the Week"
        assert card.squad == "TOTW 27"
        assert card.released_at == datetime(2026, 3, 18, 18, 0, tzinfo=timezone.utc)

    def test_legacy_name_column_semantics_are_unchanged(self):
        """97 backend call sites read futgg_players.name and there is a
        lower(name) search index on the market snapshot. `name` must keep
        holding the Player Information value exactly as it always has."""
        card = parse_futgg_card(_page(), CARD_URL)
        assert card.name == "Bruno Miguel Borges Fernandes"
        assert card.full_name == card.name

    def test_raw_string_is_preserved_for_later_re_derivation(self):
        card = parse_futgg_card(_page(), CARD_URL)
        assert card.released_at_raw == "Mar 18, 2026, 6:00 PM"

    def test_missing_added_on_does_not_crash_and_warns(self):
        card = parse_futgg_card(_page(added_on=None), CARD_URL)
        assert card.released_at is None
        assert card.released_at_raw is None
        assert "released_at_not_found" in card.parse_warnings

    def test_malformed_added_on_warns_rather_than_inventing_a_date(self):
        card = parse_futgg_card(_page(added_on="sometime last Tuesday"), CARD_URL)
        assert card.released_at is None
        # The raw value is still kept, so the failure is diagnosable.
        assert card.released_at_raw == "sometime last Tuesday"
        assert any(w.startswith("released_at_unparseable") for w in card.parse_warnings)

    def test_missing_display_anchor_falls_back_safely_and_warns(self):
        card = parse_futgg_card(_page(display_anchor=""), CARD_URL)
        assert card.display_name is None
        assert card.full_name == "Bruno Miguel Borges Fernandes"
        assert "display_name_not_found" in card.parse_warnings


class TestDisplayNameIsIdentifiedStructurally:
    def test_ignores_anchors_for_a_different_player(self):
        """A 'similar players' or navigation link must never be mistaken
        for this card's own display name."""
        page = _page(display_anchor=(
            '<a href="/players/999999-someone-else/">Someone Else</a>'
            '<a href="/players/212198-bruno-fernandes/">Bruno Fernandes</a>'
        ))
        assert parse_futgg_card(page, CARD_URL).display_name == "Bruno Fernandes"

    def test_ignores_the_card_version_url(self):
        """The card-version URL carries an extra /{game_year}-{card_id}/
        segment; only the bare player-profile URL is the display name."""
        page = _page(display_anchor=(
            '<a href="/players/212198-bruno-fernandes/26-100875494/">Bruno Fernandes TOTW 91</a>'
        ))
        assert parse_futgg_card(page, CARD_URL).display_name is None

    def test_accepts_an_absolute_url(self):
        page = _page(display_anchor=(
            '<a href="https://www.fut.gg/players/212198-bruno-fernandes/">Bruno Fernandes</a>'
        ))
        assert parse_futgg_card(page, CARD_URL).display_name == "Bruno Fernandes"

    def test_duplicate_matching_anchors_are_fine(self):
        page = _page(display_anchor=(
            '<a href="/players/212198-bruno-fernandes/">Bruno Fernandes</a>'
            '<a href="/players/212198-bruno-fernandes/">Bruno Fernandes</a>'
        ))
        assert parse_futgg_card(page, CARD_URL).display_name == "Bruno Fernandes"

    def test_an_image_only_anchor_is_skipped(self):
        """An anchor wrapping a badge/photo has no usable text - it must
        not yield an empty display name."""
        page = _page(display_anchor=(
            '<a href="/players/212198-bruno-fernandes/"><img src="/x.png"/></a>'
            '<a href="/players/212198-bruno-fernandes/">Bruno Fernandes</a>'
        ))
        assert parse_futgg_card(page, CARD_URL).display_name == "Bruno Fernandes"

    def test_does_not_depend_on_css_classes(self):
        """Explicitly pinned: identification is structural (URL shape +
        known player id), so a Tailwind restyle cannot break it."""
        page = _page(display_anchor=(
            '<a class="totally-different-classes-now" '
            'href="/players/212198-bruno-fernandes/">Bruno Fernandes</a>'
        ))
        assert parse_futgg_card(page, CARD_URL).display_name == "Bruno Fernandes"


class TestBaseGoldCardShape:
    """Base gold cards have no squad and often no promo rarity - they must
    still parse cleanly, since the backfill covers every card, not only
    specials."""

    def test_base_gold_without_squad_still_gets_release_and_names(self):
        page = _page(rarity="Rare", squad=None, added_on="Sep 26, 2025, 6:00 PM")
        card = parse_futgg_card(page, CARD_URL)
        assert card.squad is None
        assert card.rarity == "Rare"
        assert card.released_at == datetime(2025, 9, 26, 18, 0, tzinfo=timezone.utc)
        assert card.display_name == "Bruno Fernandes"
