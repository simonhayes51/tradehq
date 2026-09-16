from __future__ import annotations

from typing import Optional

from app.db import get_core_pool


async def record_leak_outcome(tweet_id: str, confirmed: bool, notes: Optional[str] = None) -> bool:
    """Record the known outcome of one leak and refresh that source's accuracy.

    False outcomes are deliberately explicit rather than inferred from age alone.
    A leak can be early, delayed, cancelled or not yet present in our catalogue.
    """
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """UPDATE leak_signals
                   SET confirmed=$2,confirmed_at=now(),confirmation_notes=$3,updated_at=now()
                   WHERE tweet_id=$1
                   RETURNING source_id""",
                tweet_id, confirmed, notes,
            )
            if not row:
                return False
            source_id = row["source_id"]
            stats = await conn.fetchrow(
                """SELECT count(*) FILTER (WHERE confirmed IS NOT NULL) AS verified,
                          count(*) FILTER (WHERE confirmed IS TRUE) AS correct
                   FROM leak_signals WHERE source_id=$1""",
                source_id,
            )
            verified = int(stats["verified"] or 0)
            correct = int(stats["correct"] or 0)
            accuracy = (correct / verified) if verified else None
            await conn.execute(
                """UPDATE leak_sources
                   SET verified_leaks=$2,correct_leaks=$3,historical_accuracy=$4,updated_at=now()
                   WHERE id=$1""",
                source_id, verified, correct, accuracy,
            )
            return True


async def auto_confirm_shipped_cards() -> int:
    """Confirm positive matches when the leaked FC27 card now exists.

    This only marks TRUE outcomes. It never marks a leak false because absence is
    not enough evidence of inaccuracy.
    """
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ls.tweet_id
               FROM leak_signals ls
               WHERE ls.confirmed IS NULL
                 AND ls.player_name IS NOT NULL
                 AND EXISTS (
                     SELECT 1 FROM futgg_players p
                     WHERE p.game_year=27
                       AND (lower(COALESCE(p.display_name,p.name))=lower(ls.player_name)
                            OR lower(p.name)=lower(ls.player_name))
                       AND (ls.rating IS NULL OR p.rating=ls.rating)
                 )"""
        )
    confirmed = 0
    for row in rows:
        if await record_leak_outcome(row["tweet_id"], True, "Matched shipped FC27 catalogue card"):
            confirmed += 1
    return confirmed
