# app/services/sbc_requirement_parser.py
"""
Turns sbc_challenges.requirements (free-text, scraped display strings -
see migration 043's header for why there's no structured source data to
read instead) into structured filters in sbc_requirement_filters, so
priority_recompute.py can answer "which cards does this SBC need" without
re-parsing text at query time on every pass.

DELIBERATELY CONSERVATIVE. A requirement line this can't confidently
parse is stored with parsed=False and every filter column NULL, never a
guessed value - a wrong nation/league guess would silently promote the
wrong cards (and, worse, silently fail to promote the right ones), which
is a worse failure mode than just not reacting to that one requirement
line. This mirrors the rest of the FUT.GG intelligence layer's own rule
("omit rather than fake" - see futgg_intelligence.py's module docstring).

Nation/league values are matched against whatever already exists in
futgg_players.nation/league for real cards (passed in as `known_nations`/
`known_leagues`), not a hardcoded reference list - grounds "this SBC
needs Saudi League" in "Saudi League is a value our own scraped card data
actually uses", which is the only way a downstream card-matching query
against futgg_players can possibly work anyway.

Pure functions, no I/O - the DB read (distinct nation/league values) and
write (upserting parsed rows) both live in sbc_card_matching.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# Rarity keywords -> the rarity vocabulary futgg_players.rarity actually
# uses (see auto_sync/futgg_common.py::classify_price_tier's base_values
# set for the closest existing precedent of this vocabulary). Ordered so
# more specific phrases are tried before their substrings ("gold rare"
# before "gold", "totw" before nothing that would collide).
_RARITY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bicons?\b", "icon"),
    (r"\bhero(?:es)?\b", "hero"),
    (r"\btotw\b|\bteam of the week\b", "totw"),
    (r"\btots\b|\bteam of the season\b", "tots"),
    (r"\bfut\s*birthday\b", "fut birthday"),
    (r"\bgold\s+rares?\b", "gold_rare"),
    (r"\bgold\s+commons?\b", "gold_common"),
    (r"\bsilver\s+rares?\b", "silver_rare"),
    (r"\bsilver\s+commons?\b", "silver_common"),
    (r"\bbronze\s+rares?\b", "bronze_rare"),
    (r"\bbronze\s+commons?\b", "bronze_common"),
    (r"\bgolds?\b", "gold"),
    (r"\bsilvers?\b", "silver"),
    (r"\bbronzes?\b", "bronze"),
)

# "Min. 11 Saudi Arabia Silver Players", "11 Silver Players", "Min. 1
# Icon Player" - the count is a leading number (optionally after "Min."/
# "Minimum"), not necessarily adjacent to the word "player(s)" (other
# constraint words - nation/league/rarity - usually sit between them).
# Gated on the text actually containing "player" at all, so a bare
# rating requirement like "83 Rated Gold Rares" (no player-count being
# expressed) never has its rating number misread as a count.
_LEADING_COUNT_RE = re.compile(r"^\s*(?:min\.?|minimum)?\s*(\d+)\b", re.IGNORECASE)
_COUNT_NEAR_PLAYER_RE = re.compile(
    r"(?:min\.?\s*)?(\d+)\s*(?:x\s*)?players?", re.IGNORECASE
)

# "83 Rated", "Rating: 83", "Min. Rating 83", "83+ Rated", "Rating 83-86"
_RATING_RANGE_RE = re.compile(
    r"rat(?:ing|ed)?\D{0,15}?(\d{2,3})\s*(?:-|to)\s*(\d{2,3})", re.IGNORECASE
)
_RATING_MIN_RE = re.compile(
    r"(?:min\.?\s*(?:rating)?\D{0,10}?)?(\d{2,3})\s*\+?\s*rat(?:ing|ed)?", re.IGNORECASE
)


@dataclass(frozen=True)
class ParsedRequirement:
    nation: Optional[str] = None
    league: Optional[str] = None
    club: Optional[str] = None
    rarity: Optional[str] = None
    rating_min: Optional[int] = None
    rating_max: Optional[int] = None
    min_count: int = 1
    parsed: bool = False


def _extract_count(text: str) -> int:
    if "player" not in text.lower():
        return 1
    m = _LEADING_COUNT_RE.match(text)
    if m:
        return max(1, int(m.group(1)))
    m = _COUNT_NEAR_PLAYER_RE.search(text)
    if m:
        return max(1, int(m.group(1)))
    return 1


def _extract_rating_range(text: str) -> tuple[Optional[int], Optional[int]]:
    m = _RATING_RANGE_RE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return min(lo, hi), max(lo, hi)
    m = _RATING_MIN_RE.search(text)
    if m:
        rating = int(m.group(1))
        if 40 <= rating <= 99:  # guards against matching an unrelated 2-3 digit number
            return rating, None
    return None, None


def _extract_rarity(text: str) -> Optional[str]:
    lowered = text.lower()
    for pattern, rarity in _RARITY_PATTERNS:
        if re.search(pattern, lowered):
            return rarity
    return None


def _extract_reference_match(text: str, known_values: Iterable[str]) -> Optional[str]:
    """Finds the longest known nation/league/club name that appears as a
    whole-word(ish) substring of `text` - longest-first so "Saudi Arabia"
    wins over a shorter unrelated partial match, and re.escape guards
    against values containing regex metacharacters (club names commonly
    do, e.g. "Paris Saint-Germain")."""
    lowered = text.lower()
    for value in sorted((v for v in known_values if v), key=len, reverse=True):
        if re.search(rf"\b{re.escape(value.lower())}\b", lowered):
            return value
    return None


def parse_requirement_text(
    text: str,
    known_nations: Iterable[str] = (),
    known_leagues: Iterable[str] = (),
    known_clubs: Iterable[str] = (),
) -> ParsedRequirement:
    """Best-effort structured parse of one SBC requirement line.

    Returns parsed=True only when at least one concrete filter dimension
    (nation/league/club/rarity/rating) was confidently extracted -
    a bare player-count with no other constraint isn't useful for card
    matching and isn't worth claiming as "parsed"."""
    if not text or not text.strip():
        return ParsedRequirement(parsed=False)

    nation = _extract_reference_match(text, known_nations)
    league = _extract_reference_match(text, known_leagues)
    club = _extract_reference_match(text, known_clubs)
    rarity = _extract_rarity(text)
    rating_min, rating_max = _extract_rating_range(text)
    min_count = _extract_count(text)

    has_constraint = any([nation, league, club, rarity, rating_min is not None])
    return ParsedRequirement(
        nation=nation, league=league, club=club, rarity=rarity,
        rating_min=rating_min, rating_max=rating_max, min_count=min_count,
        parsed=has_constraint,
    )
