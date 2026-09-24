"""Read models for FUT.GG SBC solutions and FC27 Gallery."""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def list_sbc_events(
    pool: asyncpg.Pool,
    *,
    limit: int = 60,
    offset: int = 0,
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: str = "live",
    sort: str = "newest",
) -> dict[str, Any]:
    order = {
        "newest": "e.starts_at DESC NULLS LAST, e.first_seen_at DESC",
        "expiring": "e.ends_at ASC NULLS LAST, e.starts_at DESC NULLS LAST",
        "cheapest": "d.total_cost_coins ASC NULLS LAST, e.starts_at DESC NULLS LAST",
        "costliest": "d.total_cost_coins DESC NULLS LAST, e.starts_at DESC NULLS LAST",
    }.get(sort, "e.starts_at DESC NULLS LAST, e.first_seen_at DESC")
    clauses = ["e.kind = 'sbc'", "e.source = 'futgg'"]
    params: list[Any] = []

    def add(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if status == "live":
        clauses.append("(e.ends_at IS NULL OR e.ends_at > now())")
    elif status == "expired":
        clauses.append("e.ends_at <= now()")
    if search:
        marker = add(f"%{search.strip()}%")
        clauses.append(f"(e.title ILIKE {marker} OR e.description ILIKE {marker})")
    if category:
        clauses.append(f"d.category = {add(category)}")
    where = " AND ".join(clauses)
    limit_marker, offset_marker = add(limit), add(offset)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT e.id,e.kind,e.source,e.external_id,e.title,e.description,
                   e.starts_at,e.ends_at,e.fingerprint,e.first_seen_at,e.updated_at,
                   d.category,d.total_cost_coins,d.total_cost_pc,d.repeatable,
                   d.repeat_count,d.repeat_refresh_seconds,d.reward_description,
                   d.image_url,d.futgg_url,
                   count(DISTINCT c.id)::int AS challenge_count,
                   count(si.challenge_id)::int AS solution_item_count
            FROM market_events e
            JOIN sbc_details d ON d.event_id=e.id
            LEFT JOIN sbc_challenges c ON c.event_id=e.id
            LEFT JOIN sbc_solution_items si ON si.challenge_id=c.id AND si.platform='console'
            WHERE {where}
            GROUP BY e.id,d.event_id
            ORDER BY {order}
            LIMIT {limit_marker} OFFSET {offset_marker}
            """,
            *params,
        )
        total = await conn.fetchval(
            f"""
            SELECT count(*) FROM market_events e
            JOIN sbc_details d ON d.event_id=e.id
            WHERE {where}
            """,
            *params[: len(params) - 2],
        )
        categories = await conn.fetch(
            """
            SELECT coalesce(d.category,'other') AS category,count(*)::int AS count
            FROM market_events e JOIN sbc_details d ON d.event_id=e.id
            WHERE e.kind='sbc' AND e.source='futgg'
              AND (e.ends_at IS NULL OR e.ends_at > now())
            GROUP BY 1 ORDER BY count DESC,category
            """
        )
    return {
        "items": [dict(row) for row in rows],
        "count": int(total or 0),
        "categories": [dict(row) for row in categories],
    }


async def solution_exposure(pool: asyncpg.Pool, *, limit: int = 20) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT si.player_ea_id,si.source_card_id,
                   coalesce(p.display_name,p.name,'Player ' || si.player_ea_id::text) AS display_name,
                   p.rating,p.primary_position,p.rarity,p.card_image_url,
                   p.card_image_stored_url,p.player_image_url,m.current_bin,
                   count(DISTINCT c.event_id)::int AS sbc_count,
                   count(DISTINCT c.id)::int AS challenge_count,
                   count(*)::int AS solution_appearances
            FROM sbc_solution_items si
            JOIN sbc_challenges c ON c.id=si.challenge_id
            JOIN market_events e ON e.id=c.event_id
            LEFT JOIN futgg_players p ON p.source_card_id=si.source_card_id
            LEFT JOIN futgg_market_snapshot m ON m.source_card_id=si.source_card_id
            WHERE si.platform='console' AND e.source='futgg'
              AND (e.ends_at IS NULL OR e.ends_at > now())
            GROUP BY si.player_ea_id,si.source_card_id,p.source_card_id,m.current_bin
            ORDER BY sbc_count DESC,challenge_count DESC,current_bin ASC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
    return [dict(row) for row in rows]


async def list_gallery_sets(
    pool: asyncpg.Pool,
    *,
    limit: int = 60,
    offset: int = 0,
    search: Optional[str] = None,
    category: Optional[str] = None,
    grade: Optional[str] = None,
    sort: str = "tokens",
) -> dict[str, Any]:
    order = {
        "tokens": "max_tokens DESC NULLS LAST,total_cost_coins ASC NULLS LAST,name",
        "cheapest": "total_cost_coins ASC NULLS LAST,max_tokens DESC NULLS LAST,name",
        "costliest": "total_cost_coins DESC NULLS LAST,max_tokens DESC NULLS LAST,name",
        "grade": "position(coalesce(best_grade,'Z') in 'SABCDZ'),max_tokens DESC NULLS LAST,name",
        "name": "name ASC",
    }.get(sort, "max_tokens DESC NULLS LAST,total_cost_coins ASC NULLS LAST,name")
    clauses = ["game_year=27"]
    params: list[Any] = []

    def add(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if search:
        clauses.append(f"name ILIKE {add('%' + search.strip() + '%')}")
    if category:
        clauses.append(f"category_slug={add(category)}")
    if grade:
        clauses.append(f"best_grade={add(grade.upper())}")
    where = " AND ".join(clauses)
    count_params = list(params)
    limit_marker, offset_marker = add(limit), add(offset)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT set_id,name,category_name,category_slug,item_count,best_grade,
                   total_cost_coins,total_score,max_tokens,status,source_updated_at,updated_at
            FROM futgg_gallery_sets WHERE {where}
            ORDER BY {order} LIMIT {limit_marker} OFFSET {offset_marker}
            """,
            *params,
        )
        total = await conn.fetchval(
            f"SELECT count(*) FROM futgg_gallery_sets WHERE {where}", *count_params
        )
        categories = await conn.fetch(
            """
            SELECT coalesce(category_slug,'other') AS slug,
                   coalesce(category_name,'Other') AS name,count(*)::int AS count,
                   coalesce(sum(max_tokens),0)::int AS tokens
            FROM futgg_gallery_sets WHERE game_year=27
            GROUP BY 1,2 ORDER BY tokens DESC,name
            """
        )
        summary = await conn.fetchrow(
            """
            SELECT count(*)::int AS sets,coalesce(sum(max_tokens),0)::int AS tokens,
                   coalesce(sum(total_cost_coins),0)::bigint AS total_cost,
                   max(updated_at) AS updated_at
            FROM futgg_gallery_sets WHERE game_year=27
            """
        )
    return {
        "items": [dict(row) for row in rows],
        "count": int(total or 0),
        "categories": [dict(row) for row in categories],
        "summary": dict(summary) if summary else {"sets": 0, "tokens": 0, "total_cost": 0},
    }


async def get_gallery_set(pool: asyncpg.Pool, set_id: int) -> Optional[dict[str, Any]]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT set_id,name,category_name,category_slug,item_count,best_grade,
                   total_cost_coins,total_score,max_tokens,status,solution_payload,
                   source_updated_at,updated_at
            FROM futgg_gallery_sets WHERE set_id=$1 AND game_year=27
            """,
            set_id,
        )
        if not row:
            return None
        items = await conn.fetch(
            """
            SELECT gi.position_idx,gi.grade,gi.card_ea_id,gi.player_ea_id,gi.source_card_id,
                   gi.price,gi.score,gi.overall,gi.club_ea_id,gi.nation_ea_id,gi.rarity_ea_id,
                   coalesce(p.display_name,p.name,'Player ' || coalesce(gi.player_ea_id,gi.card_ea_id)::text) AS display_name,
                   p.rating,p.primary_position,p.rarity,p.club,p.league,p.nation,
                   p.card_image_url,p.card_image_stored_url,p.player_image_url,m.current_bin
            FROM futgg_gallery_solution_items gi
            LEFT JOIN futgg_players p ON p.source_card_id=gi.source_card_id
            LEFT JOIN futgg_market_snapshot m ON m.source_card_id=gi.source_card_id
            WHERE gi.set_id=$1 ORDER BY gi.position_idx
            """,
            set_id,
        )
        tags = await conn.fetch(
            """
            SELECT tag_id,name,colour,description,priority,payload
            FROM futgg_gallery_tags WHERE game_year=27 ORDER BY priority NULLS LAST,name
            """
        )
    result = dict(row)
    result["solution_payload"] = _decode(result.get("solution_payload"))
    result["items"] = [dict(item) for item in items]
    result["tags"] = [{**dict(tag), "payload": _decode(tag["payload"])} for tag in tags]
    return result


async def gallery_exposure(pool: asyncpg.Pool, *, limit: int = 24) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT gi.player_ea_id,gi.source_card_id,
                   coalesce(p.display_name,p.name,'Player ' || gi.player_ea_id::text) AS display_name,
                   p.rating,p.primary_position,p.rarity,p.card_image_url,
                   p.card_image_stored_url,p.player_image_url,m.current_bin,
                   count(DISTINCT gi.set_id)::int AS set_count,
                   sum(coalesce(gs.max_tokens,0))::int AS token_exposure,
                   min(gi.price)::bigint AS cheapest_solution_price
            FROM futgg_gallery_solution_items gi
            JOIN futgg_gallery_sets gs ON gs.set_id=gi.set_id
            LEFT JOIN futgg_players p ON p.source_card_id=gi.source_card_id
            LEFT JOIN futgg_market_snapshot m ON m.source_card_id=gi.source_card_id
            WHERE gs.game_year=27 AND gi.player_ea_id IS NOT NULL
            GROUP BY gi.player_ea_id,gi.source_card_id,p.source_card_id,m.current_bin
            ORDER BY set_count DESC,token_exposure DESC,current_bin ASC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
    return [dict(row) for row in rows]

