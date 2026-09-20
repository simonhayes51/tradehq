from __future__ import annotations

import random
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    import asyncpg

STAT_KEYS = ("pace", "shooting", "passing", "dribbling", "defending", "physicality")
STAT_LABELS = {
    "pace": "PAC",
    "shooting": "SHO",
    "passing": "PAS",
    "dribbling": "DRI",
    "defending": "DEF",
    "physicality": "PHY",
}


@dataclass(frozen=True)
class Card:
    card_id: int
    name: str
    rating: int
    position: str
    version: str
    rarity: str
    image_url: Optional[str]
    pace: int
    shooting: int
    passing: int
    dribbling: int
    defending: int
    physicality: int
    club: Optional[str] = None
    league: Optional[str] = None
    nation: Optional[str] = None

    @classmethod
    def from_row(cls, row: "asyncpg.Record | dict[str, Any]") -> "Card":
        d = dict(row)
        return cls(
            card_id=int(d["source_card_id"]),
            name=d.get("name") or f"Card {d.get('source_card_id')}",
            rating=int(d.get("rating") or 0),
            position=d.get("primary_position") or "?",
            version=d.get("rarity") or "Gold",
            rarity=d.get("rarity") or "Gold",
            image_url=d.get("card_image_stored_url") or d.get("card_image_url") or d.get("player_image_url"),
            pace=int(d.get("pace") or 0),
            shooting=int(d.get("shooting") or 0),
            passing=int(d.get("passing") or 0),
            dribbling=int(d.get("dribbling") or 0),
            defending=int(d.get("defending") or 0),
            physicality=int(d.get("physicality") or 0),
            club=d.get("club"),
            league=d.get("league"),
            nation=d.get("nation"),
        )

    def stat(self, key: str) -> int:
        if key not in STAT_KEYS:
            raise ValueError(f"Unknown stat: {key}")
        return int(getattr(self, key))


CARD_SELECT = """
    source_card_id,
    COALESCE(NULLIF(BTRIM(display_name), ''), NULLIF(BTRIM(name), ''), 'Card ' || source_card_id::text) AS name,
    rating, primary_position, rarity,
    card_image_stored_url, card_image_url, player_image_url,
    pace, shooting, passing, dribbling, defending, physicality,
    club, league, nation
"""


def active_game_year() -> int:
    return int(os.getenv("FC_CARDS_GAME_YEAR", "27"))


def normalize_stat(stat: str) -> str:
    value = (stat or "").strip().lower()
    aliases = {
        "pac": "pace",
        "pace": "pace",
        "sho": "shooting",
        "shooting": "shooting",
        "shot": "shooting",
        "pas": "passing",
        "passing": "passing",
        "pass": "passing",
        "dri": "dribbling",
        "dribbling": "dribbling",
        "dribble": "dribbling",
        "def": "defending",
        "defending": "defending",
        "defence": "defending",
        "defense": "defending",
        "phy": "physicality",
        "physical": "physicality",
        "physicality": "physicality",
    }
    if value not in aliases:
        raise ValueError("Pick one of PAC, SHO, PAS, DRI, DEF or PHY.")
    return aliases[value]


def stat_label(stat: str) -> str:
    return STAT_LABELS.get(stat, stat.upper())


def random_decider(excluded: Iterable[str]) -> str:
    choices = [s for s in STAT_KEYS if s not in set(excluded)]
    return random.choice(choices or list(STAT_KEYS))


async def get_card(conn: "asyncpg.Connection", card_id: int | str) -> Optional[Card]:
    row = await conn.fetchrow(
        f"""
        SELECT {CARD_SELECT}
        FROM futgg_market_snapshot
        WHERE source_card_id = $1
          AND game_year = $2
          AND rating >= 75
        """,
        int(card_id),
        active_game_year(),
    )
    return Card.from_row(row) if row else None


async def search_card(conn: "asyncpg.Connection", query: str) -> Optional[Card]:
    q = query.strip()
    if q.isdigit():
        return await get_card(conn, q)

    row = await conn.fetchrow(
        f"""
        SELECT {CARD_SELECT}
        FROM futgg_market_snapshot
        WHERE rating >= 75
          AND game_year = $2
          AND (
            display_name ILIKE '%' || $1 || '%'
            OR name ILIKE '%' || $1 || '%'
          )
        ORDER BY
          CASE
            WHEN display_name ILIKE $1 THEN 0
            WHEN name ILIKE $1 THEN 1
            ELSE 2
          END,
          rating DESC,
          source_card_id DESC
        LIMIT 1
        """,
        q,
        active_game_year(),
    )
    return Card.from_row(row) if row else None


async def search_cards(conn: "asyncpg.Connection", query: str, limit: int = 10) -> list[Card]:
    q = query.strip()
    if not q:
        return []

    rows = await conn.fetch(
        f"""
        SELECT {CARD_SELECT}
        FROM futgg_market_snapshot
        WHERE rating >= 75
          AND game_year = $2
          AND (
            display_name ILIKE '%' || $1 || '%'
            OR name ILIKE '%' || $1 || '%'
          )
        ORDER BY rating DESC, source_card_id DESC
        LIMIT $3
        """,
        q,
        active_game_year(),
        limit,
    )
    return [Card.from_row(r) for r in rows]


_CANDIDATE_POOL_CACHE: dict[int, tuple[float, list[dict]]] = {}


def _candidate_pool_ttl_seconds() -> float:
    try:
        return float(os.getenv("FC_CARDS_POOL_CACHE_SECONDS", "300"))
    except ValueError:
        return 300.0


async def _get_candidate_pool(conn: "asyncpg.Connection", game_year: int) -> list[dict]:
    """Eligible Gold+ pull pool, cached in-process.

    This set only changes when new cards sync in, so hitting Postgres for it
    on every single card pulled (previously via ORDER BY random() over the
    whole filtered table) was a needless full scan + sort on the hot path -
    it ran once per card in a pack, so a 5-card weekly pull fired it 5 times
    back to back. Cache it and do the weighted pick in Python instead.
    """
    now = time.monotonic()
    cached = _CANDIDATE_POOL_CACHE.get(game_year)
    if cached and now - cached[0] < _candidate_pool_ttl_seconds():
        return cached[1]

    rows = await conn.fetch(
        f"""
        SELECT {CARD_SELECT}
        FROM futgg_market_snapshot
        WHERE rating >= 75
          AND game_year = $1
          AND pace IS NOT NULL
          AND shooting IS NOT NULL
          AND passing IS NOT NULL
          AND dribbling IS NOT NULL
          AND defending IS NOT NULL
          AND physicality IS NOT NULL
          AND COALESCE(card_image_stored_url, card_image_url, player_image_url) IS NOT NULL
          AND COALESCE(is_active, TRUE) = TRUE
        """,
        game_year,
    )
    if not rows:
        raise RuntimeError("No eligible FC Card Clash cards found in futgg_market_snapshot.")

    pool = [dict(r) for r in rows]
    _CANDIDATE_POOL_CACHE[game_year] = (now, pool)
    return pool


def _base_pull_weight(rating: int, odds_boost: float) -> float:
    if rating >= 90:
        return 1.0 * odds_boost
    if rating >= 88:
        return 2.5 * odds_boost
    if rating >= 86:
        return 6.0 * odds_boost
    if rating >= 84:
        return 14.0
    if rating >= 82:
        return 28.0
    return 58.0


async def candidate_pool_size(conn: "asyncpg.Connection") -> int:
    pool = await _get_candidate_pool(conn, active_game_year())
    return len(pool)


async def pull_weighted_card(
    conn: "asyncpg.Connection",
    streak: int,
    *,
    odds_multiplier: float = 1.0,
) -> Card:
    odds_boost = 1.0
    if streak >= 30:
        odds_boost = 2.0
    elif streak >= 7:
        odds_boost = 1.55
    elif streak >= 3:
        odds_boost = 1.25
    odds_boost *= odds_multiplier

    pool = await _get_candidate_pool(conn, active_game_year())
    weights = [_base_pull_weight(int(r["rating"] or 0), odds_boost) for r in pool]
    return Card.from_row(random.choices(pool, weights=weights, k=1)[0])

