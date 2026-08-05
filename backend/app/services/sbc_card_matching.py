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

  1. sync_requirement_filters() - parses any sbc_challenges row whose
     requirement text has changed (or is new), using nation/league/club
     values that already exist in futgg_players, and records
     market_events.source/external_id alongside each parsed row for
     traceability. Detects a changed requirement by comparing the
     current requirements dict's text values against the raw_text
     values already stored for that challenge - a mismatch means the
     upstream requirement text changed (or was added/removed) since the
     last parse, and the old filter rows for that challenge are replaced
     wholesale rather than patched, so a stale filter can never survive
     alongside its replacement.

  2. sync_sbc_triggers() - for every currently-active SBC, resolves
     matching card_ids via sbc_requirement_filters (parsed AND NOT
     ambiguous rows only - an ambiguous or unparsed requirement line
     never triggers automatic promotion, see sbc_requirement_parser.py's
     module docstring) and ensures a priority_triggers row exists for
     each, with expires_at = the event's own ends_at and
     last_confirmed_at = now(). Idempotent: re-confirms (updates
     last_confirmed_at) an existing still-active trigger row for the
     same (card_id, source_event_id) rather than inserting duplicates.
     last_confirmed_at (migration 046) is what protects against a stale
     ends_at if the SBC silently disappears upstream without either
     writer proactively marking it ended - see that migration's header
     and priority_recompute.py's active-trigger queries, which require
     BOTH expires_at not passed AND last_confirmed_at within the
     configured grace period.

Both are read-then-write batch operations meant to run once per
priority_recompute.py pass, not per-request. Both are called from
run_classification_pass() inside their own try/except (see that
function) so a failure in either (a DB hiccup, an unexpected upstream
data shape) degrades to "no SBC updates this pass" rather than aborting
baseline/effective tier classification for every other card.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import asyncpg

from app.services.priority_config import PRIORITY_ENGINE_CONFIG
from app.services.sbc_requirement_parser import parse_requirement_text

log = logging.getLogger(__name__)


def _parse_requirements_jsonb(raw: Any) -> Dict[str, Any]:
    """asyncpg returns JSONB columns as raw JSON strings, not parsed
    dicts - no codec is registered anywhere in app/db.py (see
    market_events.py's own identical comment on this exact gotcha,
    which this module's first version missed - caught by running this
    function against a real Postgres instance, not just unit tests
    against mocked rows, during the production-readiness review)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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
    event) whose requirement text is new or has changed since it was
    last parsed. Returns the number of challenges (re)processed."""
    async with pool.acquire() as conn:
        known_nations, known_leagues, known_clubs = await _known_reference_values(conn)

        rows = await conn.fetch(
            """
            SELECT c.id AS challenge_id, c.requirements, e.source, e.external_id,
                   COALESCE(array_agg(f.raw_text) FILTER (WHERE f.raw_text IS NOT NULL), '{}') AS existing_raw_texts
            FROM sbc_challenges c
            JOIN market_events e ON e.id = c.event_id
            LEFT JOIN sbc_requirement_filters f ON f.challenge_id = c.id
            WHERE (e.ends_at IS NULL OR e.ends_at > now())
              AND e.source IN ('futbin', 'easysbc')
            GROUP BY c.id, c.requirements, e.source, e.external_id
            """
        )
        if not rows:
            return 0

        changed_challenge_ids = []
        insert_args = []
        for row in rows:
            requirements = _parse_requirements_jsonb(row["requirements"])
            if not isinstance(requirements, dict):
                continue
            current_texts = {
                t.strip() for t in requirements.values()
                if isinstance(t, str) and t.strip()
            }
            existing_texts = set(row["existing_raw_texts"] or [])
            if current_texts == existing_texts:
                continue  # unchanged since last parse - nothing to do

            changed_challenge_ids.append(row["challenge_id"])
            for raw_text in current_texts:
                parsed = parse_requirement_text(raw_text, known_nations, known_leagues, known_clubs)
                insert_args.append((
                    row["challenge_id"], row["source"], row["external_id"],
                    parsed.nation, parsed.league, parsed.club, parsed.rarity,
                    parsed.rating_min, parsed.rating_max, parsed.min_count,
                    parsed.parsed, parsed.ambiguous, raw_text,
                ))

        if not changed_challenge_ids:
            return 0

        # Replace wholesale, never patch in place - a stale filter row
        # must never survive alongside its replacement (see module
        # docstring). Same connection/no explicit transaction block: the
        # DELETE and the following INSERTs are two round trips, not one
        # atomic unit, but a partial failure here just means this
        # challenge gets re-detected as "changed" (current != existing)
        # and retried whole on the next pass - safe by construction, not
        # a state that needs rolling back.
        await conn.execute(
            "DELETE FROM sbc_requirement_filters WHERE challenge_id = ANY($1::bigint[])",
            changed_challenge_ids,
        )
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
            "sbc_requirement_filters: (re)parsed %d changed challenges, %d requirement rows (%d ambiguous)",
            len(changed_challenge_ids), len(insert_args), sum(1 for a in insert_args if a[11]),
        )
        return len(changed_challenge_ids)


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
        confirmed = 0
        for row in matches:
            # Re-confirm (bump last_confirmed_at) rather than a plain
            # existence check - this is the write that protects against
            # a stale ends_at if the SBC silently disappears upstream
            # later (see migration 046's header): as long as this match
            # keeps being found here, last_confirmed_at keeps moving
            # forward, so it never depends on expires_at alone.
            updated = await conn.fetchval(
                """
                UPDATE priority_triggers
                SET last_confirmed_at = now(),
                    expires_at = $3  -- picks up any ends_at correction from upstream
                WHERE card_id = $1 AND source_event_id = $2 AND trigger_type = 'sbc_match'
                  AND (expires_at IS NULL OR expires_at > now())
                RETURNING 1
                """,
                row["card_id"], row["event_id"], row["ends_at"],
            )
            if updated:
                confirmed += 1
                continue
            await conn.execute(
                """
                INSERT INTO priority_triggers
                    (card_id, trigger_type, trigger_reason, forced_min_tier, source_event_id, expires_at, last_confirmed_at)
                VALUES ($1, 'sbc_match', $2, $3, $4, $5, now())
                """,
                row["card_id"], f"Matches active SBC: {row['title']}", floor_tier,
                row["event_id"], row["ends_at"],
            )
            created += 1
        if created or confirmed:
            log.info("sbc triggers: created %d new, re-confirmed %d existing sbc_match trigger rows", created, confirmed)
        return created
