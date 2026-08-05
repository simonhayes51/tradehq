# app/services/futgg_card_image_mirror.py
#
# Re-hosts FUT.GG's own pre-composited card image (futgg_players.
# card_image_url - the single background+cutout+badges render captured by
# auto_sync/futgg_common.py's "player-item-card/" branch) into our own R2
# storage, so the frontend's main card image never depends on FUT.GG's CDN
# staying up, not rate-limiting cross-origin hotlinks, or its URL/hash
# format not changing under us - same reasoning
# PLAYER_CARD_PNG_PIPELINE.md already gives for rendering our own PNG
# rather than hotlinking FUTBIN source images, just without needing a
# Chromium render here: this is one HTTP GET + one R2 PUT per card, not a
# browser screenshot.
#
# Ownership split (mirrors generated_card_* on fut_players /
# player_card_generation.py): auto_sync's futgg_player_sync.py owns
# futgg_players' schema and only ever writes card_image_url (the source
# URL). This module owns the card_image_stored_* lifecycle - it never
# alters card_image_url, only reads it.
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Dict

import aiohttp
import asyncpg

from app.services.object_storage import ObjectStorageError, upload_bytes

logger = logging.getLogger("futgg_card_image_mirror")

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=15)

# card_image_stored_status not IN ('ready') and card_image_url IS NOT NULL
# - includes NULL (never attempted), 'error' (retry), and a stale
# 'mirroring' claim from a crashed previous attempt. Select-then-UPDATE in
# one CTE so a batch is atomically claimed without a separate round trip
# (and without a race window between "select pending" and "claim" that a
# two-query version would have across concurrent replicas).
_CLAIM_BATCH_SQL = """
    WITH candidates AS (
        SELECT source_card_id
        FROM futgg_players
        WHERE is_active
          AND card_image_url IS NOT NULL
          AND (
            card_image_stored_status IS NULL
            OR card_image_stored_status = 'error'
            OR (card_image_stored_status = 'mirroring' AND card_image_stored_at < NOW() - INTERVAL '5 minutes')
          )
        ORDER BY source_card_id
        LIMIT $1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE futgg_players p
    SET card_image_stored_status = 'mirroring',
        card_image_stored_at = NOW()
    FROM candidates c
    WHERE p.source_card_id = c.source_card_id
    RETURNING p.source_card_id
"""


class CardImageNotFoundError(RuntimeError):
    pass


def _storage_key(card_id: int, content_hash: str) -> str:
    return f"fc26/futgg-card-images/{card_id}/{content_hash[:16]}.webp"


async def _mark_error(conn: asyncpg.Connection, card_id: int, error: str) -> None:
    await conn.execute(
        """
        UPDATE futgg_players
        SET card_image_stored_status = 'error', card_image_stored_error = $2
        WHERE source_card_id = $1
        """,
        card_id, error[:2000],
    )


async def ensure_mirrored_card_image(
    pool: asyncpg.Pool, card_id: int, *, already_claimed: bool = False
) -> Dict[str, Any]:
    """Fetches futgg_players.card_image_url once and re-hosts it in R2 if
    not already mirrored. already_claimed=True means the caller (the
    background sweep below) has already atomically transitioned this row
    to 'mirroring' via _CLAIM_BATCH_SQL - skips the redundant status check
    that would otherwise see the claim's own fresh status and no-op."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT source_card_id, card_image_url, card_image_stored_url,
                   card_image_stored_status
            FROM futgg_players WHERE source_card_id = $1
            """,
            card_id,
        )
        if row is None or row["card_image_url"] is None:
            raise CardImageNotFoundError(f"No card_image_url for source_card_id={card_id}")

        if not already_claimed:
            if row["card_image_stored_status"] == "ready" and row["card_image_stored_url"]:
                return {"mirrored": False, "url": row["card_image_stored_url"]}
            await conn.execute(
                "UPDATE futgg_players SET card_image_stored_status = 'mirroring', "
                "card_image_stored_at = NOW() WHERE source_card_id = $1",
                card_id,
            )

        source_url = row["card_image_url"]

    try:
        async with aiohttp.ClientSession(timeout=_FETCH_TIMEOUT) as session:
            async with session.get(source_url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status} fetching {source_url}")
                content_type = resp.headers.get("Content-Type", "image/webp").split(";")[0].strip()
                data = await resp.read()
    except Exception as exc:
        logger.warning("card %s: fetch failed: %s", card_id, exc)
        async with pool.acquire() as conn:
            await _mark_error(conn, card_id, f"Fetch failed: {exc}")
        return {"mirrored": False, "error": str(exc)}

    if not data:
        async with pool.acquire() as conn:
            await _mark_error(conn, card_id, "Empty response body")
        return {"mirrored": False, "error": "empty body"}

    content_hash = hashlib.sha256(data).hexdigest()
    key = _storage_key(card_id, content_hash)

    try:
        stored_url = await upload_bytes(key, data, content_type or "image/webp")
    except ObjectStorageError as exc:
        logger.error("card %s: upload failed: %s", card_id, exc)
        async with pool.acquire() as conn:
            await _mark_error(conn, card_id, f"Upload failed: {exc}")
        return {"mirrored": False, "error": str(exc)}

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE futgg_players
            SET card_image_stored_url = $2,
                card_image_stored_key = $3,
                card_image_stored_hash = $4,
                card_image_stored_at = NOW(),
                card_image_stored_status = 'ready',
                card_image_stored_error = NULL
            WHERE source_card_id = $1
            """,
            card_id, stored_url, key, content_hash,
        )

    return {"mirrored": True, "url": stored_url}


async def _run_claimed(pool: asyncpg.Pool, card_id: int) -> None:
    try:
        await ensure_mirrored_card_image(pool, card_id, already_claimed=True)
    except Exception:
        logger.exception("card image mirror failed for card_id=%s", card_id)


async def mirror_pending_batch(pool: asyncpg.Pool, batch_size: int = 50) -> int:
    """Claims up to batch_size unmirrored cards and mirrors them
    concurrently. Returns the number claimed this call. Safe under
    concurrent callers/replicas - the claim UPDATE's row locking is the
    sole source of truth for who mirrors a given card, same pattern as
    app/services/player_card_ondemand.py."""
    async with pool.acquire() as conn:
        claimed_rows = await conn.fetch(_CLAIM_BATCH_SQL, batch_size)
    claimed = [int(r["source_card_id"]) for r in claimed_rows]
    if not claimed:
        return 0
    await asyncio.gather(*(_run_claimed(pool, cid) for cid in claimed))
    return len(claimed)


async def mirror_loop(pool: asyncpg.Pool, poll_seconds: int = 60, batch_size: int = 50) -> None:
    """Best-effort background sweep - wired into main.py's lifespan next
    to the other refreshers. Runs frequently (default 60s) with a small
    batch size since this is just HTTP GET + R2 PUT per card, not a
    Chromium render - the whole catalogue backfills in the background
    over the first several minutes after this ships without needing a
    separate bulk script run, though scripts/mirror_futgg_card_images.py
    exists for an explicit one-shot catch-up too."""
    await asyncio.sleep(15)
    while True:
        try:
            claimed = await mirror_pending_batch(pool, batch_size)
            if claimed:
                logger.info("futgg card image mirror: claimed and processed %d cards", claimed)
        except Exception:
            logger.exception("futgg card image mirror loop iteration failed")
        await asyncio.sleep(poll_seconds)


__all__ = [
    "ensure_mirrored_card_image",
    "mirror_pending_batch",
    "mirror_loop",
    "CardImageNotFoundError",
]
