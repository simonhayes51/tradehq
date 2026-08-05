# app/services/sbc_card_matching.py
"""
Resolves "which cards does this active SBC need" from the structured
filters sbc_requirement_parser.py produces, and writes priority_triggers
rows so that relevance persists for the SBC's own lifetime (tied to
market_events.ends_at) rather than being re-derived as a same-pass score
override every recompute cycle - see migration 044's header for why
triggers are now their own table with activation/expiry instead.

Two responsibilities, both against the core-DB pool (single-DB
deployment - see migration 045's header):

  1. sync_requirement_filters() - parses any sbc_challenges row that
     doesn't have filter rows yet, using nation/league/club values that
     already exist in futgg_players, and records market_events.source/
     external_id alongside each parsed row for traceability.

  2. sync_sbc_triggers() - for every currently-active SBC, resolves
     matching card_ids via sbc_requirement_filters (parsed AND NOT
     ambiguous rows only - an ambiguous or unparsed requirement line
     never triggers automatic promotion, see sbc_requirement_parser.py's
     module docstring) and ensures a priority_triggers row exists for
     each, with expires_at = the event's own ends_at. Idempotent: reuses
     an existing still-active trigger row for the same (card_id,
     source_event_id) rather than inserting duplicates every pass.

Both are read-then-write batch operations meant to run once per
priority_recompute.py pass, not per-request.
"""
from __future__ import annotations

import logging
from typing import List

import asyncpg

from app.services.priority_config import PRIORITY_ENGINE_CONFIG
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
    """Parses every sbc_challenges row (belonging to a currently-active
    event) that has no sbc_requirement_filters rows yet. Returns the
    number of challenges processed. Idempotent per challenge - only
    challenges with zero existing filter rows are (re)parsed."""
    async with pool.acquire() as conn:
        known_nations, known_leagues, known_clubs = await _known_reference_values(conn)

        rows = await conn.fetch(
            """
            SELECT c.id AS challenge_id, c.requirements, e.source, e.external_id
            FROM sbc_challenges c
            JOIN market_events e ON e.id = c.event_id
            WHERE (e.ends_at IS NULL OR e.ends_at > now())
              AND e.source IN ('futbin', 'easysbc')
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
                    row["challenge_id"], row["source"], row["external_id"],
                    parsed.nation, parsed.league, parsed.club, parsed.rarity,
                    parsed.rating_min, parsed.rating_max, parsed.min_count,
                    parsed.parsed, parsed.ambiguous, raw_text,
                ))

        if insert_args:
            await conn.executemany(
                """
                INSERT INTO sbc_requirement_filters (
                    challenge_id, source, source_challenge_external_id,
                    nation, league, club, rarity, rating_min, rating_max,
                    min_count, parsed, ambiguous, raw_text
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                """,
                insert_args,
            )
        log.info(
            "sbc_requirement_filters: parsed %d challenges, %d requirement rows (%d ambiguous)",
            len(rows), len(insert_args), sum(1 for a in insert_args if a[11]),
        )
        return len(rows)


async def sync_sbc_triggers(pool: asyncpg.Pool) -> int:
    """Ensures an active priority_triggers row exists for every card
    matching a currently-active, unambiguous, parsed SBC requirement.
    Returns the number of trigger rows created this pass (existing
    still-active triggers for the same card+event are left untouched,
    not duplicated)."""
    floor_tier = PRIORITY_ENGINE_CONFIG.event_match_floor_tier
    async with pool.acquire() as conn:
        matches = await conn.fetch(
            """
            SELECT DISTINCT p.source_card_id AS card_id, c.event_id, e.ends_at, e.title
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
            WHERE f.parsed AND NOT f.ambiguous
              AND (e.ends_at IS NULL OR e.ends_at > now())
              AND p.is_active
            """
        )
        if not matches:
            return 0

        created = 0
        for row in matches:
            existing = await conn.fetchval(
                """
                SELECT 1 FROM priority_triggers
                WHERE card_id = $1 AND source_event_id = $2 AND trigger_type = 'sbc_match'
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                row["card_id"], row["event_id"],
            )
            if existing:
                continue
            await conn.execute(
                """
                INSERT INTO priority_triggers
                    (card_id, trigger_type, trigger_reason, forced_min_tier, source_event_id, expires_at)
                VALUES ($1, 'sbc_match', $2, $3, $4, $5)
                """,
                row["card_id"], f"Matches active SBC: {row['title']}", floor_tier,
                row["event_id"], row["ends_at"],
            )
            created += 1
        if created:
            log.info("sbc triggers: created %d new sbc_match trigger rows", created)
        return created
