from __future__ import annotations

import json
from typing import Any, Dict

from app.db import get_core_pool


async def sync_leak_context() -> int:
    """Push recent structured leak signals into current market intelligence.

    Only structured fields are copied. Tweet text, X usernames, tweet URLs and
    media URLs never cross this boundary.
    """
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (matched_source_card_id)
                matched_source_card_id,
                player_name,
                rating,
                card_type,
                promo_name,
                confidence_tag,
                reliability_score,
                parse_confidence,
                created_at
            FROM active_leak_signals
            WHERE matched_source_card_id IS NOT NULL
            ORDER BY matched_source_card_id, reliability_score DESC, created_at DESC
            """
        )
        updated = 0
        active_ids = []
        for row in rows:
            card_id = int(row["matched_source_card_id"])
            active_ids.append(card_id)
            context: Dict[str, Any] = {
                "active": True,
                "player_name": row["player_name"],
                "rating": row["rating"],
                "card_type": row["card_type"],
                "promo_name": row["promo_name"],
                "confidence": row["confidence_tag"],
                "reliability_score": float(row["reliability_score"] or 0),
                "parse_confidence": float(row["parse_confidence"] or 0),
                "observed_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            result = await conn.execute(
                """
                UPDATE futgg_current_intelligence
                SET leak_context=$1::jsonb, updated_at=updated_at
                WHERE source_card_id=$2
                """,
                json.dumps(context),
                card_id,
            )
            if result.endswith("1"):
                updated += 1

        # Clear expired/confirmed-false contexts without touching the market engine's
        # own evaluated_at/updated_at semantics.
        if active_ids:
            await conn.execute(
                """
                UPDATE futgg_current_intelligence
                SET leak_context='{}'::jsonb
                WHERE leak_context <> '{}'::jsonb
                  AND NOT (source_card_id = ANY($1::bigint[]))
                """,
                active_ids,
            )
        else:
            await conn.execute(
                "UPDATE futgg_current_intelligence SET leak_context='{}'::jsonb WHERE leak_context <> '{}'::jsonb"
            )
        return updated
