# app/services/market_events.py
#
# Read queries over market_events + sbc_details + sbc_challenges +
# event_market_impact (migrations 018/019). Kind is a plain TEXT
# discriminator so new event kinds never need a schema change here.
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import asyncpg

log = logging.getLogger("market_events")


async def _get_events_base(
    conn, *, kind: Optional[str], limit: int, offset: int,
) -> List[Dict[str, Any]]:
    """Schema-minimal event list used when optional SBC/card-art joins drift.

    Market Events is an admin observability surface. A missing optional legacy
    card-art column must not make the entire event table unreadable. Keep the
    response shape stable by returning the enrichment keys as NULL.
    """
    where = "WHERE e.kind = $3" if kind else ""
    params: List[Any] = [limit, offset]
    if kind:
        params.append(kind)
    rows = await conn.fetch(
        f"""
        SELECT e.id, e.kind, e.source, e.external_id, e.title, e.description,
               e.starts_at, e.ends_at, e.fingerprint, e.first_seen_at, e.updated_at,
               NULL::text AS category,
               NULL::bigint AS total_cost_coins,
               NULL::boolean AS repeatable,
               NULL::text AS reward_card_name,
               NULL::integer AS reward_card_rating,
               NULL::text AS reward_card_version,
               NULL::text AS reward_card_image_url,
               NULL::text AS reward_card_bg_image,
               NULL::text AS reward_card_cutout_image,
               NULL::text AS reward_card_cutout_type,
               NULL::text AS reward_card_card_name
        FROM market_events e
        {where}
        ORDER BY e.starts_at DESC NULLS LAST, e.first_seen_at DESC
        LIMIT $1 OFFSET $2
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_events(
    pool: asyncpg.Pool, *, kind: Optional[str] = None, limit: int = 30, offset: int = 0
) -> List[Dict[str, Any]]:
    """List events with SBC/reward-card enrichment when that schema exists.

    The list previously failed completely if an optional legacy fut_players
    card-art column was missing on a deployment whose market_events table was
    otherwise healthy. Retry the base event query on schema drift so admin can
    still inspect real events while migrations catch up.
    """
    where = "WHERE e.kind = $3" if kind else ""
    params: List[Any] = [limit, offset]
    if kind:
        params.append(kind)
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                f"""
                SELECT e.id, e.kind, e.source, e.external_id, e.title, e.description,
                       e.starts_at, e.ends_at, e.fingerprint, e.first_seen_at, e.updated_at,
                       d.category, d.total_cost_coins, d.repeatable,
                       rc.name AS reward_card_name, rc.rating AS reward_card_rating,
                       rc.version AS reward_card_version, rc.image_url AS reward_card_image_url,
                       rc.card_bg_image AS reward_card_bg_image,
                       rc.card_cutout_image AS reward_card_cutout_image,
                       rc.card_cutout_type AS reward_card_cutout_type,
                       rc.card_name AS reward_card_card_name
                FROM market_events e
                LEFT JOIN sbc_details d ON d.event_id = e.id
                LEFT JOIN fut_players rc ON rc.card_id = d.reward_card_id
                {where}
                ORDER BY e.starts_at DESC NULLS LAST, e.first_seen_at DESC
                LIMIT $1 OFFSET $2
                """,
                *params,
            )
            return [dict(r) for r in rows]
        except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError):
            log.warning(
                "market-events enrichment schema unavailable; serving base events",
                exc_info=True,
            )
            return await _get_events_base(conn, kind=kind, limit=limit, offset=offset)


async def get_event(pool: asyncpg.Pool, event_id: int) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        event = await conn.fetchrow(
            """
            SELECT id, kind, source, external_id, title, description,
                   starts_at, ends_at, fingerprint, payload, first_seen_at, updated_at
            FROM market_events WHERE id = $1
            """,
            event_id,
        )
        if not event:
            return None
        result = dict(event)
        if isinstance(result.get("payload"), str):
            result["payload"] = json.loads(result["payload"])

        if event["kind"] == "sbc":
            try:
                details = await conn.fetchrow(
                    """
                    SELECT d.set_name, d.category, d.total_cost_coins, d.total_cost_pc,
                           d.repeatable, d.repeat_count, d.repeat_refresh_seconds,
                           d.reward_card_id, d.reward_description, d.expires_at,
                           d.image_url, d.futgg_url, d.detail_scraped_at,
                           rc.name AS reward_card_name, rc.rating AS reward_card_rating,
                           rc.version AS reward_card_version, rc.image_url AS reward_card_image_url,
                           rc.card_bg_image AS reward_card_bg_image,
                           rc.card_cutout_image AS reward_card_cutout_image,
                           rc.card_cutout_type AS reward_card_cutout_type,
                           rc.card_name AS reward_card_card_name
                    FROM sbc_details d
                    LEFT JOIN fut_players rc ON rc.card_id = d.reward_card_id
                    WHERE d.event_id = $1
                    """,
                    event_id,
                )
                result["sbc_details"] = dict(details) if details else None
            except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError):
                log.warning("SBC event enrichment unavailable", exc_info=True)
                result["sbc_details"] = None

            try:
                challenges = await conn.fetch(
                    """
                    SELECT id, challenge_name, requirements, estimated_cost_coins,
                           estimated_cost_pc, display_order, external_challenge_id,
                           image_url, reward, solution_console_uuid, solution_pc_uuid,
                           solution_console_url, solution_pc_url
                    FROM sbc_challenges WHERE event_id = $1
                    ORDER BY display_order ASC, id ASC
                    """,
                    event_id,
                )
            except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError):
                challenges = []
            parsed_challenges = []
            for c in challenges:
                cd = dict(c)
                if isinstance(cd.get("requirements"), str):
                    cd["requirements"] = json.loads(cd["requirements"])
                if isinstance(cd.get("reward"), str):
                    cd["reward"] = json.loads(cd["reward"])
                try:
                    solution_rows = await conn.fetch(
                        """
                        SELECT si.platform,si.squad_uuid,si.position_idx,si.player_ea_id,
                               si.source_card_id,
                               coalesce(p.display_name,p.name,'Player ' || si.player_ea_id::text) AS display_name,
                               p.rating,p.primary_position,p.rarity,p.club,p.league,p.nation,
                               p.card_image_url,p.card_image_stored_url,p.player_image_url
                        FROM sbc_solution_items si
                        LEFT JOIN futgg_players p ON p.source_card_id=si.source_card_id
                        WHERE si.challenge_id=$1
                        ORDER BY si.platform,si.position_idx
                        """,
                        c["id"],
                    )
                    cd["solution_items"] = [dict(item) for item in solution_rows]
                except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError):
                    cd["solution_items"] = []
                parsed_challenges.append(cd)
            result["sbc_challenges"] = parsed_challenges

        return result


async def get_event_impact(pool: asyncpg.Pool, event_id: int) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.card_id,
                   COALESCE(p.display_name, p.card_name, 'Player ' || i.card_id::text) AS name,
                   p.rating, p.version, p.image_url, i.relation,
                   p.card_bg_image, p.card_cutout_image, p.card_cutout_type, p.card_name,
                   i.price_before, i.price_after, i.price_change_pct,
                   i.volume_before_24h, i.volume_after_24h,
                   i.measured_before_at, i.measured_after_at, i.computed_at
            FROM event_market_impact i
            LEFT JOIN fut_players p ON p.card_id = i.card_id
            WHERE i.event_id = $1
            ORDER BY ABS(COALESCE(i.price_change_pct, 0)) DESC
            """,
            event_id,
        )
    return [dict(r) for r in rows]
