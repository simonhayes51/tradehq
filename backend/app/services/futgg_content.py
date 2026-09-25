"""Read models for FUT.GG SBC solutions and FC27 Gallery."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import asyncpg

GAME_YEAR = 27
CONTENT_SYNC_LOCK_KEY = 2_702_701


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
            SELECT gs.set_id,gs.name,gs.category_name,gs.category_slug,
                   coalesce(actual.item_count,gs.item_count) AS item_count,
                   gs.best_grade,
                   coalesce(actual.total_cost_coins,gs.total_cost_coins) AS total_cost_coins,
                   gs.total_score,gs.max_tokens,gs.status,gs.solution_payload,
                   gs.source_updated_at,gs.updated_at
            FROM futgg_gallery_sets gs
            LEFT JOIN LATERAL (
                SELECT count(*)::int AS item_count,
                       coalesce(sum(price),0)::bigint AS total_cost_coins
                FROM futgg_gallery_solution_items gi
                WHERE gi.set_id=gs.set_id
            ) actual ON true
            WHERE gs.set_id=$1 AND gs.game_year=27
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
                   coalesce(p.rating,gi.overall) AS rating,
                   p.primary_position,p.rarity,p.club,p.league,p.nation,
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


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _fingerprint(sbc: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(value)
        for challenge in sbc.get("challenges", [])
        for value in (challenge.get("requirements") or {}).values()
    ).lower()
    tags: list[str] = []
    for needle, tag in (
        ("team of the week", "requires_totw"), ("totw", "requires_totw"),
        ("icon", "requires_icon"), ("hero", "requires_hero"),
        ("rare", "requires_rare"), ("chemistry", "requires_chemistry"),
    ):
        if needle in text and tag not in tags:
            tags.append(tag)
    cost = _int(sbc.get("cost_console"))
    if cost is not None:
        tags.append("high_cost" if cost >= 500_000 else "low_cost" if cost <= 50_000 else "mid_cost")
    return tags


async def ensure_write_schema(conn: asyncpg.Connection) -> None:
    """Deploy-order safety for the HTTPS ingestion bridge.

    Migration 100 remains canonical; these idempotent statements let the first
    collector delivery succeed even while the backend migration is catching up.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events (
            id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
            external_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
            starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
            fingerprint TEXT[] NOT NULL DEFAULT '{}', payload JSONB NOT NULL DEFAULT '{}',
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (kind, source, external_id)
        );
        CREATE TABLE IF NOT EXISTS sbc_details (
            event_id BIGINT PRIMARY KEY REFERENCES market_events(id) ON DELETE CASCADE,
            set_name TEXT NOT NULL, category TEXT, total_cost_coins BIGINT,
            repeatable BOOLEAN NOT NULL DEFAULT false, reward_card_id BIGINT,
            reward_description TEXT, expires_at TIMESTAMPTZ
        );
        CREATE TABLE IF NOT EXISTS sbc_challenges (
            id BIGSERIAL PRIMARY KEY, event_id BIGINT NOT NULL REFERENCES market_events(id) ON DELETE CASCADE,
            challenge_name TEXT NOT NULL, requirements JSONB NOT NULL DEFAULT '{}',
            estimated_cost_coins BIGINT, display_order INTEGER NOT NULL DEFAULT 0
        );
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS total_cost_pc BIGINT;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS repeat_count INTEGER;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS repeat_refresh_seconds INTEGER;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS image_url TEXT;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS futgg_url TEXT;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS detail_scraped_at TIMESTAMPTZ;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS external_challenge_id INTEGER;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS estimated_cost_pc BIGINT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS image_url TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS reward JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_console_uuid TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_pc_uuid TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_console_url TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_pc_url TEXT;
        CREATE TABLE IF NOT EXISTS sbc_solution_items (
            challenge_id BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,
            platform TEXT NOT NULL, squad_uuid TEXT NOT NULL, position_idx SMALLINT NOT NULL,
            player_ea_id BIGINT NOT NULL, source_card_id BIGINT,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (challenge_id, platform, position_idx)
        );
        CREATE TABLE IF NOT EXISTS futgg_gallery_tags (
            tag_id INTEGER PRIMARY KEY, game_year SMALLINT NOT NULL DEFAULT 27,
            name TEXT NOT NULL, colour TEXT, description TEXT, priority INTEGER,
            payload JSONB NOT NULL DEFAULT '{}', updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS futgg_gallery_sets (
            set_id INTEGER PRIMARY KEY, game_year SMALLINT NOT NULL DEFAULT 27,
            name TEXT NOT NULL, category_name TEXT, category_slug TEXT, item_count INTEGER,
            best_grade TEXT, total_cost_coins BIGINT, total_score BIGINT, max_tokens INTEGER,
            status TEXT, payload JSONB NOT NULL DEFAULT '{}', solution_payload JSONB NOT NULL DEFAULT '{}',
            source_updated_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS futgg_gallery_solution_items (
            set_id INTEGER NOT NULL REFERENCES futgg_gallery_sets(set_id) ON DELETE CASCADE,
            grade TEXT NOT NULL DEFAULT '', position_idx SMALLINT NOT NULL,
            card_ea_id BIGINT, player_ea_id BIGINT, source_card_id BIGINT,
            price BIGINT, score INTEGER, overall SMALLINT, club_ea_id INTEGER,
            nation_ea_id INTEGER, rarity_ea_id INTEGER,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (set_id, grade, position_idx)
        );
        """
    )


async def _resolve_card_id(
    conn: asyncpg.Connection,
    player_ea_id: int,
    game_year: int,
    *,
    overall: Optional[int] = None,
    club_ea_id: Optional[int] = None,
    nation_ea_id: Optional[int] = None,
) -> Optional[int]:
    try:
        value = await conn.fetchval(
            """
            SELECT source_card_id FROM futgg_players
            WHERE game_year=$1 AND is_active AND source_player_id=$2
            ORDER BY
              (rating=$3) DESC NULLS LAST,
              (club_source_id=$4) DESC NULLS LAST,
              (nation_source_id=$5) DESC NULLS LAST,
              is_tradeable DESC NULLS LAST,
              rating ASC NULLS LAST,
              source_card_id
            LIMIT 1
            """,
            game_year, player_ea_id, overall, club_ea_id, nation_ea_id,
        )
        return int(value) if value is not None else None
    except asyncpg.exceptions.UndefinedTableError:
        return None


async def _write_sbc(
    conn: asyncpg.Connection, sbc: dict[str, Any], game_year: int
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO market_events (
            kind,source,external_id,title,description,starts_at,ends_at,fingerprint,payload
        ) VALUES ('sbc','futgg',$1,$2,$3,$4,$5,$6,$7::jsonb)
        ON CONFLICT (kind,source,external_id) DO UPDATE SET
            title=EXCLUDED.title,description=EXCLUDED.description,
            starts_at=COALESCE(EXCLUDED.starts_at,market_events.starts_at),
            ends_at=EXCLUDED.ends_at,fingerprint=EXCLUDED.fingerprint,
            payload=EXCLUDED.payload,updated_at=now()
        RETURNING id
        """,
        str(sbc["external_id"]), str(sbc["name"]), sbc.get("description"),
        _timestamp(sbc.get("starts_at")), _timestamp(sbc.get("ends_at")),
        _fingerprint(sbc), json.dumps(sbc.get("raw") or {}, default=str),
    )
    event_id = int(row["id"])
    await conn.execute(
        """
        INSERT INTO sbc_details (
            event_id,set_name,category,total_cost_coins,total_cost_pc,repeatable,
            repeat_count,repeat_refresh_seconds,reward_card_id,reward_description,
            expires_at,image_url,futgg_url,detail_scraped_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NULL,$9,$10,$11,$12,now())
        ON CONFLICT (event_id) DO UPDATE SET
            set_name=EXCLUDED.set_name,category=EXCLUDED.category,
            total_cost_coins=EXCLUDED.total_cost_coins,total_cost_pc=EXCLUDED.total_cost_pc,
            repeatable=EXCLUDED.repeatable,repeat_count=EXCLUDED.repeat_count,
            repeat_refresh_seconds=EXCLUDED.repeat_refresh_seconds,
            reward_description=EXCLUDED.reward_description,expires_at=EXCLUDED.expires_at,
            image_url=EXCLUDED.image_url,futgg_url=EXCLUDED.futgg_url,detail_scraped_at=now()
        """,
        event_id, str(sbc["name"]), sbc.get("category"), _int(sbc.get("cost_console")),
        _int(sbc.get("cost_pc")), bool(sbc.get("repeatable")), _int(sbc.get("repeat_count")),
        _int(sbc.get("repeat_refresh_seconds")), sbc.get("reward_label"),
        _timestamp(sbc.get("ends_at")), sbc.get("image_url"), sbc.get("futgg_url"),
    )
    await conn.execute("DELETE FROM sbc_challenges WHERE event_id=$1", event_id)
    item_count = 0
    for order, challenge in enumerate(sbc.get("challenges") or []):
        challenge_id = await conn.fetchval(
            """
            INSERT INTO sbc_challenges (
                event_id,challenge_name,requirements,estimated_cost_coins,estimated_cost_pc,
                display_order,external_challenge_id,image_url,reward,solution_console_uuid,
                solution_pc_uuid,solution_console_url,solution_pc_url
            ) VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13)
            RETURNING id
            """,
            event_id, str(challenge.get("name") or "Challenge"),
            json.dumps(challenge.get("requirements") or {}), _int(challenge.get("cost_console")),
            _int(challenge.get("cost_pc")), _int(challenge.get("display_order")) or order,
            _int(challenge.get("external_challenge_id")), challenge.get("image_url"),
            json.dumps(challenge.get("reward") or []), challenge.get("solution_console_uuid"),
            challenge.get("solution_pc_uuid"), challenge.get("solution_console_url"),
            challenge.get("solution_pc_url"),
        )
        for platform in ("console", "pc"):
            uuid = challenge.get(f"solution_{platform}_uuid")
            if not uuid:
                continue
            for item in (sbc.get("squads") or {}).get(uuid, []):
                player_ea_id = _int(item.get("player_ea_id"))
                position_idx = _int(item.get("position_idx"))
                if player_ea_id is None or position_idx is None:
                    continue
                source_card_id = await _resolve_card_id(conn, player_ea_id, game_year)
                await conn.execute(
                    """
                    INSERT INTO sbc_solution_items (
                        challenge_id,platform,squad_uuid,position_idx,player_ea_id,source_card_id,captured_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,now())
                    """,
                    challenge_id, platform, uuid, position_idx, player_ea_id, source_card_id,
                )
                item_count += 1
    return item_count


async def _write_gallery(
    conn: asyncpg.Connection, gallery: dict[str, Any], game_year: int
) -> int:
    for tag in gallery.get("tags") or []:
        tag_id = _int(tag.get("id") or tag.get("tagId"))
        if tag_id is None:
            continue
        await conn.execute(
            """
            INSERT INTO futgg_gallery_tags (tag_id,game_year,name,colour,description,priority,payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
            ON CONFLICT (tag_id) DO UPDATE SET name=EXCLUDED.name,colour=EXCLUDED.colour,
                description=EXCLUDED.description,priority=EXCLUDED.priority,
                payload=EXCLUDED.payload,updated_at=now()
            """,
            tag_id, game_year, tag.get("name") or f"Tag {tag_id}",
            tag.get("color") or tag.get("colour"), tag.get("description"),
            _int(tag.get("priority")), json.dumps(tag, default=str),
        )
    count = 0
    for gallery_set in gallery.get("sets") or []:
        set_id = _int(gallery_set.get("set_id"))
        if set_id is None:
            continue
        count += 1
        await conn.execute(
            """
            INSERT INTO futgg_gallery_sets (
                set_id,game_year,name,category_name,category_slug,item_count,best_grade,
                total_cost_coins,total_score,max_tokens,status,payload,solution_payload,
                source_updated_at,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14,now())
            ON CONFLICT (set_id) DO UPDATE SET game_year=EXCLUDED.game_year,name=EXCLUDED.name,
                category_name=EXCLUDED.category_name,category_slug=EXCLUDED.category_slug,
                item_count=EXCLUDED.item_count,best_grade=EXCLUDED.best_grade,
                total_cost_coins=EXCLUDED.total_cost_coins,total_score=EXCLUDED.total_score,
                max_tokens=EXCLUDED.max_tokens,status=EXCLUDED.status,payload=EXCLUDED.payload,
                solution_payload=EXCLUDED.solution_payload,source_updated_at=EXCLUDED.source_updated_at,
                updated_at=now()
            """,
            set_id, game_year, str(gallery_set.get("name") or f"Gallery set {set_id}"),
            gallery_set.get("category_name"), gallery_set.get("category_slug"),
            _int(gallery_set.get("item_count")), gallery_set.get("best_grade"),
            _int(gallery_set.get("total_cost_coins")), _int(gallery_set.get("total_score")),
            _int(gallery_set.get("max_tokens")), gallery_set.get("status"),
            json.dumps(gallery.get("base") or {}, default=str),
            json.dumps(gallery_set.get("raw") or {}, default=str),
            _timestamp(gallery.get("captured_at")),
        )
        await conn.execute("DELETE FROM futgg_gallery_solution_items WHERE set_id=$1", set_id)
        grade = str(gallery_set.get("best_grade") or "")
        for fallback, item in enumerate(gallery_set.get("items") or []):
            # `eaId` is EA's card-resource identifier, not FUT.GG's card ID.
            # FUT.GG player URLs use playerEaId, so matching eaId directly can
            # silently attach an unrelated historical special card whose FUT.GG
            # ID happens to have the same number.
            player_ea_id = _int(item.get("player_ea_id"))
            source_card_id = (
                await _resolve_card_id(
                    conn,
                    player_ea_id,
                    game_year,
                    overall=_int(item.get("overall")),
                    club_ea_id=_int(item.get("club_ea_id")),
                    nation_ea_id=_int(item.get("nation_ea_id")),
                )
                if player_ea_id else None
            )
            await conn.execute(
                """
                INSERT INTO futgg_gallery_solution_items (
                    set_id,grade,position_idx,card_ea_id,player_ea_id,source_card_id,
                    price,score,overall,club_ea_id,nation_ea_id,rarity_ea_id,captured_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,now())
                """,
                set_id, grade, _int(item.get("position_idx")) or fallback,
                _int(item.get("card_ea_id")), _int(item.get("player_ea_id")), source_card_id,
                _int(item.get("price")), _int(item.get("score")), _int(item.get("overall")),
                _int(item.get("club_ea_id")), _int(item.get("nation_ea_id")),
                _int(item.get("rarity_ea_id")),
            )
    return count


async def ingest_content_payload(
    pool: asyncpg.Pool, payload: dict[str, Any]
) -> dict[str, int]:
    game_year = _int(payload.get("game_year")) or GAME_YEAR
    if game_year != GAME_YEAR:
        raise ValueError("unsupported game year")
    sbcs = payload.get("sbcs")
    gallery = payload.get("gallery")
    if not isinstance(sbcs, list) or not 1 <= len(sbcs) <= 250:
        raise ValueError("SBC payload must contain 1-250 sets")
    if not isinstance(gallery, dict) or not isinstance(gallery.get("sets"), list):
        raise ValueError("Gallery payload is missing sets")
    if not 1 <= len(gallery["sets"]) <= 1000:
        raise ValueError("Gallery payload must contain 1-1000 sets")

    async with pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock($1)", CONTENT_SYNC_LOCK_KEY
            )
            if not locked:
                raise RuntimeError("another FUT.GG content ingest is running")
            await ensure_write_schema(conn)
            solution_items = 0
            for sbc in sbcs:
                if not isinstance(sbc, dict) or not sbc.get("external_id") or not sbc.get("name"):
                    raise ValueError("invalid SBC set")
                solution_items += await _write_sbc(conn, sbc, game_year)
            await conn.execute(
                """
                UPDATE market_events SET ends_at=now(),updated_at=now()
                WHERE kind='sbc' AND source='futgg'
                  AND NOT (external_id=ANY($1::text[]))
                  AND (ends_at IS NULL OR ends_at>now())
                """,
                [str(sbc["external_id"]) for sbc in sbcs],
            )
            gallery_sets = await _write_gallery(conn, gallery, game_year)
    return {
        "sbcs": len(sbcs),
        "solution_items": solution_items,
        "gallery_sets": gallery_sets,
        "gallery_tags": len(gallery.get("tags") or []),
    }
