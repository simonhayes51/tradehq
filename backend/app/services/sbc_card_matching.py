# app/services/sbc_card_matching.py
"""
Resolves "what does this active SBC actually mean for the catalogue"
from the structured filters sbc_requirement_parser.py produces.

Three responsibilities, all against the core-DB pool (single-DB
deployment - see migration 045's header):

  1. sync_requirement_filters() - parses any sbc_challenges row whose
     requirement text has changed (or is new). Unchanged in shape from
     the original version; only the field list grew (team_rating_min,
     migration 048).

  2. sync_sbc_resource_demand() - SBC relevance model revision
     (production-readiness review). Every requirement line with no
     named identity dimension (nation/league/club) - "Min. 1 TOTW
     Player", "83 Rated Gold Rares", "Team Rating: Min. 89" - describes
     demand for a broad, reusable market segment, not one card.
     Classified via app/services/sbc_demand_classifier.py and recorded
     as sbc_resource_demand rows (migration 049), one per (challenge,
     resource_type, resource_value), never as individual
     priority_triggers.

  3. sync_sbc_identity_triggers() - the ORIGINAL sync_sbc_triggers(),
     narrowed. Only requirement lines that DO name a nation/league/club
     (optionally + rarity/rating) are considered for individual-card
     priority_triggers, and only when app/services/sbc_demand_
     classifier.py's pool-size/concentration gate accepts the pool -
     "6 Saudi League silver players from a pool of 40" still triggers
     those cards; "1 Premier League player from 1,000+ cards" does not
     trigger any of them. This is the fix for the confirmed root cause:
     the original version matched every requirement LINE independently
     (no identity gate, no pool-size gate at all) and unioned the
     results across every challenge/event - a single bare-rarity line
     like "Min. 1 TOTW Player" promoted every TOTW card in the whole
     catalogue, which is how one player's SBC (Fernando Torres) reached
     1,687 individually-triggered cards.

All three are read-then-write batch operations meant to run once per
priority_recompute.py pass, not per-request, and are called from
run_classification_pass() inside their own try/except (see that
function) so a failure in any one degrades to "no SBC updates this
pass" rather than aborting baseline/effective tier classification for
every other card.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from app.services.priority_config import PRIORITY_ENGINE_CONFIG, PriorityEngineConfig
from app.services.sbc_demand_classifier import (
    classify_resource_signal,
    compute_demand_weight,
    evaluate_identity_pool,
    is_identity_requirement,
)
from app.services.sbc_requirement_parser import ParsedRequirement, parse_requirement_text

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
                    parsed.rating_min, parsed.rating_max, parsed.team_rating_min,
                    parsed.min_count, parsed.parsed, parsed.ambiguous, raw_text,
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
                    nation, league, club, rarity, rating_min, rating_max, team_rating_min,
                    min_count, parsed, ambiguous, raw_text
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                insert_args,
            )
        log.info(
            "sbc_requirement_filters: (re)parsed %d changed challenges, %d requirement rows (%d ambiguous)",
            len(changed_challenge_ids), len(insert_args), sum(1 for a in insert_args if a[12]),
        )
        return len(changed_challenge_ids)


# Rows shared by both sync_sbc_resource_demand() and
# sync_sbc_identity_triggers() - active-event, parsed requirement filters
# joined to their event/challenge context. NOT gated on `ambiguous` here
# (both callers apply that filter themselves, differently: resource
# demand stores ambiguous rows with ambiguity_status='AMBIGUOUS' rather
# than skipping them; identity triggers require NOT ambiguous, unchanged
# from the original behaviour).
_ACTIVE_FILTER_ROWS_QUERY = """
    SELECT f.id AS filter_id, f.challenge_id, f.source AS filter_source,
           f.source_challenge_external_id, f.nation, f.league, f.club, f.rarity,
           f.rating_min, f.rating_max, f.team_rating_min, f.min_count,
           f.ambiguous, f.raw_text,
           c.event_id, e.title, e.starts_at, e.first_seen_at, e.ends_at,
           COALESCE(d.repeatable, FALSE) AS repeatable
    FROM sbc_requirement_filters f
    JOIN sbc_challenges c ON c.id = f.challenge_id
    JOIN market_events e ON e.id = c.event_id
    LEFT JOIN sbc_details d ON d.event_id = e.id
    WHERE f.parsed
      AND (e.ends_at IS NULL OR e.ends_at > now())
"""


def _row_to_parsed_requirement(row: asyncpg.Record) -> ParsedRequirement:
    return ParsedRequirement(
        nation=row["nation"], league=row["league"], club=row["club"], rarity=row["rarity"],
        rating_min=row["rating_min"], rating_max=row["rating_max"], team_rating_min=row["team_rating_min"],
        min_count=row["min_count"], parsed=True, ambiguous=row["ambiguous"],
    )


async def sync_sbc_resource_demand(pool: asyncpg.Pool, *, config: PriorityEngineConfig = PRIORITY_ENGINE_CONFIG) -> int:
    """Upserts sbc_resource_demand rows for every currently-active,
    parsed requirement line with no named identity dimension (see
    sbc_demand_classifier.classify_resource_signal). Returns the number
    of rows upserted (created or refreshed) this pass."""
    as_of = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        rows = await conn.fetch(_ACTIVE_FILTER_ROWS_QUERY)
        if not rows:
            return 0

        upsert_args = []
        for row in rows:
            req = _row_to_parsed_requirement(row)
            signal = classify_resource_signal(req)
            if signal is None:
                continue  # identity requirement, or no concrete resource dimension

            event_started_at = row["starts_at"] or row["first_seen_at"]
            weight = compute_demand_weight(
                event_started_at=event_started_at, event_expires_at=row["ends_at"],
                as_of=as_of, config=config,
            )
            ambiguity_status = "AMBIGUOUS" if row["ambiguous"] else "CONFIRMED"
            upsert_args.append((
                row["event_id"], row["challenge_id"], signal.resource_type, signal.resource_value,
                row["min_count"], event_started_at, row["ends_at"], row["repeatable"],
                row["filter_source"], row["source_challenge_external_id"], row["raw_text"],
                ambiguity_status, weight,
            ))

        if not upsert_args:
            return 0

        await conn.executemany(
            """
            INSERT INTO sbc_resource_demand (
                source_event_id, challenge_id, resource_type, resource_value,
                required_count, event_started_at, event_expires_at, repeatable,
                source_provider, source_challenge_external_id, raw_text,
                ambiguity_status, decayed_weight, last_confirmed_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,now())
            ON CONFLICT (challenge_id, resource_type, resource_value) DO UPDATE SET
                required_count = EXCLUDED.required_count,
                event_started_at = EXCLUDED.event_started_at,
                event_expires_at = EXCLUDED.event_expires_at,
                repeatable = EXCLUDED.repeatable,
                raw_text = EXCLUDED.raw_text,
                ambiguity_status = EXCLUDED.ambiguity_status,
                decayed_weight = EXCLUDED.decayed_weight,
                last_confirmed_at = now(),
                updated_at = now()
            """,
            upsert_args,
        )
        log.info("sbc_resource_demand: upserted %d resource-demand rows this pass", len(upsert_args))
        return len(upsert_args)


async def _eligible_pool_card_ids(conn: asyncpg.Connection, req: ParsedRequirement) -> List[int]:
    rows = await conn.fetch(
        """
        SELECT source_card_id FROM futgg_players p
        WHERE (($1::text IS NULL) OR p.nation = $1) AND
              (($2::text IS NULL) OR p.league = $2) AND
              (($3::text IS NULL) OR p.club = $3) AND
              (($4::text IS NULL) OR p.rarity ILIKE '%' || $4 || '%') AND
              (($5::int IS NULL) OR p.rating >= $5) AND
              (($6::int IS NULL) OR p.rating <= $6) AND
              p.is_active
        """,
        req.nation, req.league, req.club, req.rarity, req.rating_min, req.rating_max,
    )
    return [int(r["source_card_id"]) for r in rows]


async def sync_sbc_identity_triggers(pool: asyncpg.Pool, *, config: PriorityEngineConfig = PRIORITY_ENGINE_CONFIG) -> int:
    """Ensures an active priority_triggers row exists for every card in
    a sufficiently small/concentrated named-identity pool (nation/
    league/club, optionally + rarity/rating) belonging to a currently-
    active, unambiguous, parsed SBC requirement. Returns the number of
    trigger rows CREATED this pass (re-confirmations of already-active
    rows are not counted, matching the original function's contract)."""
    floor_tier = config.event_match_floor_tier
    as_of = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        rows = await conn.fetch(_ACTIVE_FILTER_ROWS_QUERY)
        identity_rows = [r for r in rows if not r["ambiguous"] and is_identity_requirement(_row_to_parsed_requirement(r))]
        if not identity_rows:
            return 0

        created = 0
        confirmed = 0
        for row in identity_rows:
            req = _row_to_parsed_requirement(row)
            pool_ids = await _eligible_pool_card_ids(conn, req)
            pool_size = len(pool_ids)
            if pool_size == 0:
                continue

            already_active = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM priority_triggers
                    WHERE card_id = ANY($1::bigint[]) AND source_event_id = $2
                      AND trigger_type = 'sbc_match'
                      AND (expires_at IS NULL OR expires_at > now())
                )
                """,
                pool_ids, row["event_id"],
            )
            event_started_at = row["starts_at"] or row["first_seen_at"]
            gate = evaluate_identity_pool(
                required_count=row["min_count"], pool_size=pool_size,
                is_new_trigger=not already_active, event_started_at=event_started_at,
                as_of=as_of, config=config,
            )
            if not gate.accepted:
                log.info(
                    "sbc identity trigger REJECTED: challenge_id=%s event_id=%s pool_size=%d "
                    "required_count=%d reason=%s raw_text=%r",
                    row["challenge_id"], row["event_id"], pool_size, row["min_count"],
                    gate.reason, row["raw_text"],
                )
                continue

            for card_id in pool_ids:
                updated = await conn.fetchval(
                    """
                    UPDATE priority_triggers
                    SET last_confirmed_at = now(),
                        expires_at = $3
                    WHERE card_id = $1 AND source_event_id = $2 AND trigger_type = 'sbc_match'
                      AND (expires_at IS NULL OR expires_at > now())
                    RETURNING 1
                    """,
                    card_id, row["event_id"], row["ends_at"],
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
                    card_id, f"Matches active SBC: {row['title']}", floor_tier,
                    row["event_id"], row["ends_at"],
                )
                created += 1

        if created or confirmed:
            log.info("sbc identity triggers: created %d new, re-confirmed %d existing sbc_match trigger rows", created, confirmed)
        return created


__all__ = [
    "sync_requirement_filters",
    "sync_sbc_resource_demand",
    "sync_sbc_identity_triggers",
]
