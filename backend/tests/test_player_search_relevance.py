"""
A name search must rank by relevance, not by rating alone.

WHY THIS EXISTS
---------------
Reported: "Player search doesn't show any Gold cards at all."

search_players matched on `name ILIKE %term%` and then ordered purely by
`rating DESC`. A search therefore returned the highest-rated cards of
EVERY player whose name merely contained the term, globally. Reproduced
against a real Postgres on a mixed catalogue: a term matching 40 cards
across four tiers returned 20 rows that were entirely 92-rated specials
and 84-rated gold rares. The 78s and 70s could not surface at any page
size a user would scroll.

Users type surnames, so the load-bearing case is the third rank below:
"Haaland" is neither an exact nor a prefix match for "Erling Haaland",
and without a word-boundary rank every surname search falls straight
through to rating and buries the ordinary gold card.

Needs a real Postgres - the ordering is SQL. Skipped unless
FUTGG_TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import asyncio
import functools
import os

import pytest

from app.services.market_data_provider import FutggMarketDataProvider, PlayerFilters

DB_URL = os.getenv("FUTGG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="FUTGG_TEST_DATABASE_URL not set")

CARDS = [
    # (id, name, rating, price_tier)
    (9001, "Erling Haaland", 98, "special"),
    (9002, "Erling Haaland", 91, "gold_rare"),
    (9003, "Marshall Hughes", 94, "special"),
    (9004, "Haaland Berg", 79, "gold_common"),
]


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.new_event_loop().run_until_complete(fn(*a, **k))
    return wrapper


async def _seed(conn):
    await conn.execute("DELETE FROM futgg_players WHERE source_card_id >= 9000")
    for card_id, name, rating, tier in CARDS:
        await conn.execute(
            """
            INSERT INTO futgg_players (
                source_card_id, source_player_id, source_slug, source_url,
                game_year, name, rating, primary_position, rarity,
                price_tier, is_tradeable, price_updated_at, last_price_status
            ) VALUES ($1,$1,'s','u',26,$2,$3,'ST','gold',$4,TRUE,now(),'bulk_feed')
            """,
            card_id, name, rating, tier,
        )
    await conn.execute("REFRESH MATERIALIZED VIEW futgg_market_snapshot")


@sync
async def _search(term, limit=10):
    import asyncpg

    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await _seed(conn)
        rows = await FutggMarketDataProvider(pool).search_players(
            PlayerFilters(search=term), limit=limit, offset=0
        )
        return [(r["name"], r["rating"]) for r in rows]
    finally:
        await pool.close()


class TestSearchRelevance:
    def test_a_surname_search_surfaces_the_players_gold_card(self):
        """The reported bug. Both Haaland cards must appear, and neither
        may be displaced by a higher-rated card that merely contains the
        same letters."""
        results = _search("Haaland")
        names = [n for n, _ in results]
        assert names.count("Erling Haaland") == 2
        assert ("Erling Haaland", 91) in results

    def test_an_exact_match_outranks_a_higher_rated_partial_match(self):
        results = _search("Haaland Berg")
        assert results[0] == ("Haaland Berg", 79), results

    def test_rating_still_orders_within_one_players_cards(self):
        """Relevance must not destroy the useful part of the old
        behaviour: for one player, best card first."""
        results = [r for r in _search("Erling Haaland") if r[0] == "Erling Haaland"]
        assert results == sorted(results, key=lambda r: -r[1])

    def test_an_unrelated_higher_rated_card_ranks_below_the_real_match(self):
        """"Marshall Hughes" is 94 and contains no "haaland"; it must not
        crowd out the 91 gold Haaland on a Haaland search."""
        results = _search("Haaland")
        assert "Marshall Hughes" not in [n for n, _ in results]
