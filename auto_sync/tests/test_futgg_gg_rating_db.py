"""Optional PostgreSQL contract tests for GG Rating persistence.

Set FUTGG_TEST_DATABASE_URL to run. A private schema is created and
dropped, so production-shaped SQL is exercised without touching shared
tables.
"""

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_player_sync as mod
from futgg_common import FutggCard

DB_URL = os.getenv("FUTGG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="FUTGG_TEST_DATABASE_URL not set")


def test_real_postgres_upsert_and_listing_update_contract():
    async def scenario():
        conn = await asyncpg.connect(DB_URL)
        schema = f"gg_rating_test_{uuid.uuid4().hex}"
        try:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'SET search_path TO "{schema}"')
            await mod.ensure_schema(conn)

            card = FutggCard(
                source_card_id=1001,
                source_player_id=1,
                source_slug="player",
                game_year=26,
                source_url="https://www.fut.gg/players/1-player/26-1001/",
                gg_rating=Decimal("87.3"),
            )
            await mod.upsert_card(conn, card)
            first = await conn.fetchrow(
                "SELECT gg_rating, gg_rating_updated_at FROM futgg_players WHERE source_card_id=1001"
            )
            assert first["gg_rating"] == Decimal("87.3")
            assert first["gg_rating_updated_at"] is not None

            card.gg_rating = None
            await mod.upsert_card(conn, card)
            after_null = await conn.fetchrow(
                "SELECT gg_rating, gg_rating_updated_at FROM futgg_players WHERE source_card_id=1001"
            )
            assert after_null == first

            await conn.execute("SELECT pg_sleep(0.01)")
            card.gg_rating = Decimal("88.1")
            await mod.upsert_card(conn, card)
            refreshed = await conn.fetchrow(
                "SELECT gg_rating, gg_rating_updated_at FROM futgg_players WHERE source_card_id=1001"
            )
            assert refreshed["gg_rating"] == Decimal("88.1")
            assert refreshed["gg_rating_updated_at"] > first["gg_rating_updated_at"]

            wrong_year = await mod.update_listing_gg_ratings(
                conn,
                {card.source_url.replace("26-1001", "27-1001"): mod.ListingCardMetadata(gg_rating=Decimal("90.0"))},
                27,
                only_missing=False,
            )
            assert wrong_year == mod.ListingRatingUpdateResult(matched=0, updated=0)
            assert await conn.fetchval(
                "SELECT gg_rating FROM futgg_players WHERE source_card_id=1001"
            ) == Decimal("88.1")
        finally:
            await conn.execute("SET search_path TO public")
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await conn.close()

    asyncio.run(scenario())
