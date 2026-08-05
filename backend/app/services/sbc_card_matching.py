# app/services/sbc_card_matching.py
"""
Resolves "which cards does this active SBC need" from the structured
filters sbc_requirement_parser.py produces, and keeps
sbc_requirement_filters in sync with sbc_challenges as new SBCs appear.

Two responsibilities, both read/write the same core-DB pool (see
migration 043's header - single-DB deployment, so a plain SQL join
across market_events/sbc_challenges and futgg_players/
futgg_market_snapshot works without any cross-database plumbing):

  1. sync_requirement_filters() - parses any sbc_challenges row that
     doesn't have filter rows yet, using nation/league/club values that
     already exist in futgg_players (so a parsed filter can only ever
     reference a value real cards actually have).

  2. get_active_sbc_matched_card_ids() - for every currently-active SBC
     (market_events.kind='sbc' AND not yet ended), resolves matching
     card_ids via sbc_requirement_filters. This is what
     priority_recompute.py calls to force event_component=1.0 on
     matching cards - see that module for how the override is applied.

Both are read-then-write batch operations meant to run once per
priority_recompute.py pass, not per-request - a live API endpoint should
never call sync_requirement_filters() directly.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Set

import asyncpg

from app.services.sbc_requirement_parser import parse_requirement_text

log = logging.getLogger(__name__)


async def _known_reference_values(conn: asyncpg.Connection) -> tuple[List[str], List[str], List[str]]:
    nations = [r["nation"] for r in await conn.fetch(
        "SELECT DISTINCT nation FROM futgg_players WHERE nation IS NOT NULL"
    )]
    leagues = [r["league"] for r in await conn.fetch(
        "SELECT DISTINCT league FROM futgg_players WHERE league IS NOT NULL"
    )]
    clubs = [r["club"] for r in await conn.fetch(
        "SELECT DISTINCT club FROM futgg_players WHERE club IS NOT NULL"
    )]
    return nations, leagues, clubs


async def sync_requirement_filters(pool: asyncpg.Pool) -> int:
    """Parses every sbc_challenges row that has no sbc_requirement_filters
    rows yet. Returns the number of challenges processed. Idempotent per
    challenge (re-parses only challenges with zero existing filter rows,
    so this is safe to call every recompute pass without re-doing work
    for SBCs that were already parsed - see the DELETE-then-INSERT below
    for why a challenge is only ever fully re-parsed, never partially
    patched)."""
    async with pool.acquire() as conn:
        known_nations, known_leagues, known_clubs = await _known_reference_values(conn)

        rows = await conn.fetch(
            """
            SELECT c.id AS challenge_id, c.requirements
            FROM sbc_challenges c
            JOIN market_events e ON e.id = c.event_id
            WHERE (e.ends_at IS NULL OR e.ends_at > now())
              AND NOT EXISTS (
                  SELECT 1 FROM sbc_requirement_filters f WHERE f.challenge_id = c.id
              )
            """
        )
        if not rows:
            return 0

        insert_args = []
        for row in rows:
            requirements = row["requirements"] or {}
            if not isinstance(requirements, dict):
                continue
            for raw_text in requirements.values():
                if not isinstance(raw_text, str) or not raw_text.strip():
                    continue
                parsed = parse_requirement_text(raw_text, known_nations, known_leagues, known_clubs)
                insert_args.append((
                    row["challenge_id"], parsed.nation, parsed.league, parsed.club,
                    parsed.rarity, parsed.rating_min, parsed.rating_max,
                    parsed.min_count, parsed.parsed, raw_text,
                ))

        if insert_args:
            await conn.executemany(
                """
                INSERT INTO sbc_requirement_filters
                    (challenge_id, nation, league, club, rarity, rating_min, rating_max, min_count, parsed, raw_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                insert_args,
            )
        log.info("sbc_requirement_filters: parsed %d challenges, %d requirement rows", len(rows), len(insert_args))
        return len(rows)


async def get_active_sbc_matched_card_ids(pool: asyncpg.Pool) -> Dict[int, int]:
    """Returns {card_id: event_id} for every card matching at least one
    parsed requirement filter of a currently-active SBC. Only `parsed`
    filter rows are used - unparsed requirement lines never match
    anything (see sbc_requirement_parser.py's module docstring on why
    that's a deliberate choice, not a gap).

    event_id (not challenge_id) is returned since that's what
    card_priority.forced_by_event_id references - a card matching any
    challenge within an active event is enough to promote it, the
    specific challenge isn't meaningful downstream."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (p.source_card_id)
                p.source_card_id AS card_id, c.event_id
            FROM sbc_requirement_filters f
            JOIN sbc_challenges c ON c.id = f.challenge_id
            JOIN market_events e ON e.id = c.event_id
            JOIN futgg_players p ON
                (f.nation IS NULL OR p.nation = f.nation) AND
                (f.league IS NULL OR p.league = f.league) AND
                (f.club IS NULL OR p.club = f.club) AND
                (f.rarity IS NULL OR p.rarity ILIKE '%' || f.rarity || '%') AND
                (f.rating_min IS NULL OR p.rating >= f.rating_min) AND
                (f.rating_max IS NULL OR p.rating <= f.rating_max)
            WHERE f.parsed
              AND (e.ends_at IS NULL OR e.ends_at > now())
              AND p.is_active
            ORDER BY p.source_card_id, e.ends_at DESC NULLS LAST
            """
        )
    return {r["card_id"]: r["event_id"] for r in rows}
