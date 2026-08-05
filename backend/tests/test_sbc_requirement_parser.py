# tests/test_sbc_requirement_parser.py
#
# Pure-function tests for app/services/sbc_requirement_parser.py.
# Requirement text samples below are taken from the real formats both
# SBC sources produce - see auto_sync/futbin_sbc_sync.py's HTML-scraped
# rows and auto_sync/easysbc_sbc_sync.py's own docstring example strings
# ("Team Rating: Min. 89", "Min. 1 Players Any TOTW/TOTS/FOF") - not
# invented text, real observed shapes.
from __future__ import annotations

from app.services.sbc_requirement_parser import parse_requirement_text

KNOWN_NATIONS = ["Saudi Arabia", "England", "Brazil"]
KNOWN_LEAGUES = ["Saudi Pro League", "Premier League", "Serie A"]
KNOWN_CLUBS = ["Paris Saint-Germain", "Real Madrid"]


def test_count_and_nation_and_rarity_all_extracted():
    parsed = parse_requirement_text(
        "Min. 11 Saudi Arabia Silver Players",
        known_nations=KNOWN_NATIONS, known_leagues=KNOWN_LEAGUES,
    )
    assert parsed.parsed is True
    assert parsed.nation == "Saudi Arabia"
    assert parsed.rarity == "silver"
    assert parsed.min_count == 11


def test_league_requirement_matches_longest_known_value():
    parsed = parse_requirement_text(
        "Min. 1 Saudi Pro League Player",
        known_nations=KNOWN_NATIONS, known_leagues=KNOWN_LEAGUES,
    )
    assert parsed.league == "Saudi Pro League"
    assert parsed.min_count == 1
    assert parsed.parsed is True


def test_rating_only_requirement_parses_rating_min_no_count():
    parsed = parse_requirement_text("83 Rated Gold Rares")
    assert parsed.rating_min == 83
    assert parsed.rarity == "gold_rare"
    assert parsed.parsed is True


def test_rating_range_requirement():
    parsed = parse_requirement_text("Rating: 83 - 86")
    assert parsed.rating_min == 83
    assert parsed.rating_max == 86
    assert parsed.parsed is True


def test_team_rating_minimum_easysbc_style_string():
    # "Team Rating: Min. 89" - a squad-level constraint, not a per-card
    # nation/league/rarity/rating filter, so this deliberately does NOT
    # parse into a card-matching filter (there is no single card that
    # "is" a team rating requirement) - kept unparsed, not guessed.
    parsed = parse_requirement_text("Team Rating: Min. 89")
    assert parsed.parsed is False


def test_totw_and_tots_and_icon_and_hero_rarity_keywords():
    assert parse_requirement_text("Min. 1 Players Any TOTW/TOTS/FOF").rarity in ("totw",)
    assert parse_requirement_text("Min. 1 Icon Player").rarity == "icon"
    assert parse_requirement_text("Min. 1 Hero Player").rarity == "hero"


def test_or_condition_and_slash_alternatives_are_flagged_ambiguous():
    # "Any TOTW/TOTS/FOF" only actually extracts "totw" (see the test
    # above) - silently treating that as the whole requirement would be
    # WRONG (a TOTS or FOF card also satisfies this line), so it must be
    # flagged ambiguous even though a rarity value was extracted.
    parsed = parse_requirement_text("Min. 1 Players Any TOTW/TOTS/FOF")
    assert parsed.ambiguous is True

    parsed_or = parse_requirement_text(
        "Min. 1 Brazil or Argentina Player", known_nations=["Brazil", "Argentina"],
    )
    assert parsed_or.ambiguous is True


def test_multiple_known_nation_mentions_are_ambiguous():
    parsed = parse_requirement_text(
        "Min. 1 Brazil Player and Min. 1 England Player",
        known_nations=KNOWN_NATIONS,
    )
    assert parsed.ambiguous is True


def test_single_unambiguous_requirement_is_not_flagged():
    parsed = parse_requirement_text(
        "Min. 11 Saudi Arabia Silver Players", known_nations=KNOWN_NATIONS,
    )
    assert parsed.ambiguous is False


def test_club_requirement_matches_multi_word_club_with_hyphen():
    parsed = parse_requirement_text(
        "Min. 1 Paris Saint-Germain Player", known_clubs=KNOWN_CLUBS,
    )
    assert parsed.club == "Paris Saint-Germain"
    assert parsed.parsed is True


def test_unrecognised_free_text_is_not_confidently_parsed():
    parsed = parse_requirement_text("Squad Chemistry: Min. 18")
    assert parsed.parsed is False
    assert parsed.nation is None
    assert parsed.rarity is None
    assert parsed.rating_min is None


def test_empty_or_whitespace_text_is_not_parsed():
    assert parse_requirement_text("").parsed is False
    assert parse_requirement_text("   ").parsed is False


def test_default_min_count_is_one_when_no_count_present():
    parsed = parse_requirement_text("Gold Rare Player", )
    assert parsed.min_count == 1
