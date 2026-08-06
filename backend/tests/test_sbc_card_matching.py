# tests/test_sbc_card_matching.py
#
# DB-backed tests for the SBC relevance model revision - sync_sbc_
# resource_demand()/sync_sbc_identity_triggers() (app/services/sbc_card_
# matching.py) and the rebuild/cleanup command (scripts/rebuild_sbc_
# triggers.py), against a real throwaway Postgres database. Same
# convention as test_priority_production_safety.py.
#
# Needs a real Postgres reachable at TEST_ADMIN_DATABASE_URL (default
# postgresql://postgres:postgres@localhost:5432/postgres) - skipped
# outright if unreachable.
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.services.sbc_card_matching import (
    sync_requirement_filters, sync_sbc_identity_triggers, sync_sbc_resource_demand,
)
from scripts.rebuild_sbc_triggers import rebuild

ADMIN_DSN = os.getenv(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)

_SCHEMA_SQL = """
CREATE TABLE futgg_players (
    source_card_id BIGINT PRIMARY KEY,
    rating INT,
    rarity TEXT,
    nation TEXT,
    league TEXT,
    club TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_tradeable BOOLEAN,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE market_events (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sbc_details (
    event_id BIGINT PRIMARY KEY REFERENCES market_events(id) ON DELETE CASCADE,
    set_name TEXT,
    category TEXT,
    repeatable BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE sbc_challenges (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES market_events(id),
    challenge_name TEXT NOT NULL,
    requirements JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE sbc_requirement_filters (
    id BIGSERIAL PRIMARY KEY,
    challenge_id BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_challenge_external_id TEXT,
    nation TEXT, league TEXT, club TEXT, rarity TEXT,
    rating_min INT, rating_max INT, team_rating_min INT, min_count INT NOT NULL DEFAULT 1,
    parsed BOOLEAN NOT NULL DEFAULT FALSE,
    ambiguous BOOLEAN NOT NULL DEFAULT FALSE,
    raw_text TEXT NOT NULL,
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sbc_resource_demand (
    id BIGSERIAL PRIMARY KEY,
    source_event_id BIGINT NOT NULL REFERENCES market_events(id) ON DELETE CASCADE,
    challenge_id BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL,
    resource_value TEXT NOT NULL,
    required_count INT NOT NULL DEFAULT 1,
    event_started_at TIMESTAMPTZ,
    event_expires_at TIMESTAMPTZ,
    repeatable BOOLEAN NOT NULL DEFAULT FALSE,
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_provider TEXT NOT NULL,
    source_challenge_external_id TEXT,
    raw_text TEXT NOT NULL,
    ambiguity_status TEXT NOT NULL DEFAULT 'CONFIRMED',
    market_status TEXT NOT NULL DEFAULT 'UNCONFIRMED',
    decayed_weight DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (challenge_id, resource_type, resource_value)
);

CREATE TABLE priority_triggers (
    id BIGSERIAL PRIMARY KEY,
    card_id BIGINT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('sbc_match','sales_spike','price_spike','new_release','user_position','manual_override')),
    trigger_reason TEXT,
    forced_min_tier TEXT NOT NULL CHECK (forced_min_tier IN ('CRITICAL','HIGH','NORMAL','LOW','DORMANT')),
    source_event_id BIGINT REFERENCES market_events(id) ON DELETE SET NULL,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _run(coro):
    return asyncio.run(coro)


async def _postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


async def _make_db() -> str:
    name = f"tradehq_sbc_test_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    return name


async def _drop_db(name: str) -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


def _dsn_for(name: str) -> str:
    base = ADMIN_DSN.rsplit("/", 1)[0]
    return f"{base}/{name}"


@pytest.fixture
def dsn():
    if not _run(_postgres_reachable()):
        pytest.skip(f"no Postgres reachable at {ADMIN_DSN}")
    name = _run(_make_db())
    d = _dsn_for(name)

    async def _bootstrap():
        conn = await asyncpg.connect(d)
        try:
            await conn.execute(_SCHEMA_SQL)
        finally:
            await conn.close()

    _run(_bootstrap())
    try:
        yield d
    finally:
        _run(_drop_db(name))


async def _pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=3)


async def _seed_event(conn, *, title, starts_at, ends_at, repeatable=False, source="easysbc", external_id=None):
    event_id = await conn.fetchval(
        "INSERT INTO market_events (kind, source, external_id, title, starts_at, ends_at) VALUES ('sbc',$1,$2,$3,$4,$5) RETURNING id",
        source, external_id or title.lower().replace(" ", "-"), title, starts_at, ends_at,
    )
    await conn.execute("INSERT INTO sbc_details (event_id, repeatable) VALUES ($1, $2)", event_id, repeatable)
    return event_id


async def _seed_challenge(conn, event_id, name, requirements: dict):
    return await conn.fetchval(
        "INSERT INTO sbc_challenges (event_id, challenge_name, requirements) VALUES ($1,$2,$3) RETURNING id",
        event_id, name, json.dumps(requirements),
    )


async def _seed_players(conn, ids_and_fields):
    for card_id, fields in ids_and_fields:
        await conn.execute(
            """
            INSERT INTO futgg_players (source_card_id, rating, rarity, nation, league, club)
            VALUES ($1,$2,$3,$4,$5,$6)
            """,
            card_id, fields.get("rating", 70), fields.get("rarity", "gold_rare"),
            fields.get("nation"), fields.get("league"), fields.get("club"),
        )


# =========================================================================
# Root-cause regression: broad requirement no longer creates thousands
# of individual triggers
# =========================================================================

def test_broad_totw_requirement_creates_no_individual_triggers(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _seed_players(conn, [(1000 + n, {"rarity": "totw"}) for n in range(50)])
                event_id = await _seed_event(conn, title="Fernando Torres", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_id, "C1", {"req_0": "Min. 1 Players Any TOTW/TOTS/FOF"})

            await sync_requirement_filters(pool)
            resource_rows = await sync_sbc_resource_demand(pool)
            identity_created = await sync_sbc_identity_triggers(pool)

            assert identity_created == 0
            async with pool.acquire() as conn:
                total_triggers = await conn.fetchval("SELECT count(*) FROM priority_triggers")
            assert total_triggers == 0
            assert resource_rows >= 1
        finally:
            await pool.close()

    _run(_t())


def test_broad_team_rating_requirement_creates_resource_demand_not_individual_triggers(dsn):
    # Spec test 1.
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _seed_players(conn, [(2000 + n, {"rarity": "gold_rare", "rating": 88}) for n in range(80)])
                event_id = await _seed_event(conn, title="88 Squad SBC", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_id, "C1", {"req_0": "Team Rating: Min. 88"})

            await sync_requirement_filters(pool)
            await sync_sbc_resource_demand(pool)
            created = await sync_sbc_identity_triggers(pool)

            assert created == 0
            async with pool.acquire() as conn:
                demand = await conn.fetchrow("SELECT * FROM sbc_resource_demand WHERE resource_type = 'MIN_TEAM_RATING'")
            assert demand is not None
            assert demand["resource_value"] == "88"
        finally:
            await pool.close()

    _run(_t())


def test_small_constrained_pool_still_creates_individual_triggers(dsn):
    # Anderson Talisca's four-card pool - spec's own worked example.
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _seed_players(conn, [
                    (3001, {"rarity": "silver", "nation": "Saudi Arabia", "league": "Saudi Pro League"}),
                    (3002, {"rarity": "silver", "nation": "Saudi Arabia", "league": "Saudi Pro League"}),
                    (3003, {"rarity": "silver", "nation": "Saudi Arabia", "league": "Saudi Pro League"}),
                    (3004, {"rarity": "silver", "nation": "Saudi Arabia", "league": "Saudi Pro League"}),
                ])
                event_id = await _seed_event(conn, title="Anderson Talisca", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_id, "Saudi Squad", {"req_0": "Min. 3 Saudi Pro League Player"})

            await sync_requirement_filters(pool)
            created = await sync_sbc_identity_triggers(pool)

            assert created == 4
            async with pool.acquire() as conn:
                triggered = {r["card_id"] for r in await conn.fetch("SELECT card_id FROM priority_triggers WHERE trigger_type='sbc_match'")}
            assert triggered == {3001, 3002, 3003, 3004}
        finally:
            await pool.close()

    _run(_t())


def test_large_named_league_pool_creates_no_individual_triggers(dsn):
    # Spec test 9: "1 Premier League player from 1,000+ cards" must
    # never trigger any single one of them.
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _seed_players(conn, [(4000 + n, {"league": "Premier League"}) for n in range(200)])
                event_id = await _seed_event(conn, title="Huge League SBC", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_id, "C1", {"req_0": "Min. 1 Premier League Player"})

            await sync_requirement_filters(pool)
            created = await sync_sbc_identity_triggers(pool)

            assert created == 0
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# Challenge boundary preservation
# =========================================================================

def test_challenge_boundaries_are_preserved_across_two_challenges(dsn):
    # Spec's own worked example: Challenge A (87 rating + TOTW),
    # Challenge B (Spanish + 88 rating) must become 4 distinct signals,
    # not flattened into one union.
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _seed_players(conn, [(5000 + n, {"nation": "Spain"}) for n in range(500)])
                event_id = await _seed_event(conn, title="Two Challenge SBC", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                challenge_a = await _seed_challenge(conn, event_id, "Challenge A", {"req_0": "Team Rating: Min. 87", "req_1": "Min. 1 Players Any TOTW/TOTS/FOF"})
                challenge_b = await _seed_challenge(conn, event_id, "Challenge B", {"req_0": "Team Rating: Min. 88"})

            await sync_requirement_filters(pool)
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT challenge_id, team_rating_min, rarity FROM sbc_requirement_filters ORDER BY challenge_id")
            by_challenge = {}
            for r in rows:
                by_challenge.setdefault(r["challenge_id"], []).append(r)
            assert set(by_challenge) == {challenge_a, challenge_b}
            assert len(by_challenge[challenge_a]) == 2  # rating + TOTW, both retained separately
            assert len(by_challenge[challenge_b]) == 1
        finally:
            await pool.close()

    _run(_t())


def test_separate_challenge_constraints_are_not_incorrectly_combined(dsn):
    # Spec test 12: Challenge A requires nation=Spain, Challenge B
    # (same event) requires nation=France. A card must never be
    # triggered by a requirement that's only true if you combine BOTH
    # challenges' constraints - each challenge's pool is computed
    # independently.
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _seed_players(conn, [
                    (6001, {"nation": "Spain"}),
                    (6002, {"nation": "France"}),
                ])
                event_id = await _seed_event(conn, title="Mixed Nations SBC", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_id, "Challenge A", {"req_0": "Min. 1 Spain Player"})
                await _seed_challenge(conn, event_id, "Challenge B", {"req_0": "Min. 1 France Player"})

            await sync_requirement_filters(pool)
            await sync_sbc_identity_triggers(pool)

            async with pool.acquire() as conn:
                triggered = {r["card_id"] for r in await conn.fetch("SELECT card_id FROM priority_triggers WHERE trigger_type='sbc_match'")}
            # Both small pools (1 card each) are independently accepted -
            # both cards triggered, each for its OWN challenge's
            # constraint, not some fabricated Spain-AND-France combination.
            assert triggered == {6001, 6002}
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# Aggregation across active SBCs
# =========================================================================

def test_multiple_sbcs_requiring_totw_aggregate_into_one_segment(dsn):
    # Spec test 2.
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                event_1 = await _seed_event(conn, title="SBC One", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_1, "C1", {"req_0": "Min. 1 Players Any TOTW/TOTS/FOF"})
                event_2 = await _seed_event(conn, title="SBC Two", starts_at=now - timedelta(hours=5), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_2, "C1", {"req_0": "Min. 2 Players Any TOTW/TOTS/FOF"})

            await sync_requirement_filters(pool)
            await sync_sbc_resource_demand(pool)

            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT source_event_id, resource_type, required_count FROM sbc_resource_demand WHERE resource_type = 'TOTW'")
            assert len(rows) == 2
            assert {r["source_event_id"] for r in rows} == {event_1, event_2}
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# Cleanup/rebuild
# =========================================================================

def test_rebuild_is_idempotent(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _seed_players(conn, [
                    (7001, {"nation": "Saudi Arabia", "league": "Saudi Pro League"}),
                    (7002, {"nation": "Saudi Arabia", "league": "Saudi Pro League"}),
                ])
                event_id = await _seed_event(conn, title="Small Pool SBC", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await _seed_challenge(conn, event_id, "C1", {"req_0": "Min. 1 Saudi Pro League Player"})

            first = await rebuild(pool, apply=True)
            second = await rebuild(pool, apply=True)

            assert second["stale_sbc_match_rows_expired"] == 0
            assert second["identity_triggers_created_or_confirmed"] == 0  # already confirmed, not re-created
        finally:
            await pool.close()

    _run(_t())


def test_rebuild_expires_obsolete_broad_triggers_and_preserves_audit_row(dsn):
    # Spec test 16.
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                event_id = await _seed_event(conn, title="Old Broad SBC", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                # Simulate a pre-existing trigger from the OLD ungated
                # matcher - no matching requirement filter exists for it
                # under the new logic at all.
                trigger_id = await conn.fetchval(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, source_event_id, expires_at, last_confirmed_at)
                    VALUES (8001, 'sbc_match', 'HIGH', $1, $2, $3) RETURNING id
                    """,
                    event_id, now + timedelta(days=5), now - timedelta(hours=1),
                )

            summary = await rebuild(pool, apply=True)
            assert summary["stale_sbc_match_rows_expired"] == 1

            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM priority_triggers WHERE id = $1", trigger_id)
            assert row is not None  # preserved, not deleted
            assert row["expires_at"] <= now + timedelta(minutes=1)  # expired immediately, not waiting for grace period
        finally:
            await pool.close()

    _run(_t())


def test_rebuild_never_touches_non_sbc_triggers(dsn):
    # Spec test 17.
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                manual_id = await conn.fetchval(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, expires_at, last_confirmed_at)
                    VALUES (9001, 'manual_override', 'CRITICAL', NULL, now()) RETURNING id
                    """
                )

            await rebuild(pool, apply=True)

            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM priority_triggers WHERE id = $1", manual_id)
            assert row["expires_at"] is None  # completely untouched
        finally:
            await pool.close()

    _run(_t())


def test_rebuild_preview_mode_does_not_expire_stale_rows(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                event_id = await _seed_event(conn, title="Old Broad SBC", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=5))
                await conn.execute(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, source_event_id, expires_at, last_confirmed_at)
                    VALUES (8002, 'sbc_match', 'HIGH', $1, $2, $3)
                    """,
                    event_id, now + timedelta(days=5), now - timedelta(hours=1),
                )

            preview = await rebuild(pool, apply=False)
            assert preview["stale_sbc_match_rows_found"] == 1
            assert preview["stale_sbc_match_rows_expired"] == 0

            async with pool.acquire() as conn:
                still_active = await conn.fetchval(
                    "SELECT count(*) FROM priority_triggers WHERE card_id = 8002 AND (expires_at IS NULL OR expires_at > now())"
                )
            assert still_active == 1
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# Resource demand never bypasses trade-quality gates (structural check)
# =========================================================================

def test_evaluate_card_never_reads_sbc_resource_demand():
    # Spec test 14. futgg_intelligence.py's evaluate_card() (the strict
    # opportunity-quality gate Trade Finder/Opportunities use) must have
    # zero references to the resource-demand table/model - resource
    # demand can inform scrape priority/observation frequency, never
    # bypass liquidity/sales/freshness/dispersion/margin checks.
    import inspect

    from app.services import futgg_intelligence
    source = inspect.getsource(futgg_intelligence)
    assert "sbc_resource_demand" not in source
    assert "sbc_demand_classifier" not in source
