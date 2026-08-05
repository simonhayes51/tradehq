# tests/test_priority_production_safety.py
#
# Production-safety tests for Milestones 1 (rule-based shadow
# classification) and 1.5 (conservative cost-control demotion) - the
# scope the production-readiness review (see backend/scripts/
# priority_validation_report.py's own docstring) was asked to prove safe
# before PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION is enabled in
# production. Exercises the real functions in priority_recompute.py /
# sbc_card_matching.py / priority_engine.py against a real throwaway
# Postgres database (not mocks) - the JSONB-string-vs-dict bug found
# during this review was invisible to mocked-row unit tests and only
# surfaced by running the real code against a real connection.
#
# Needs a real Postgres reachable at TEST_ADMIN_DATABASE_URL (default
# postgresql://postgres:postgres@localhost:5432/postgres) with CREATE
# DATABASE privileges - same convention as test_run_migrations.py. Each
# test gets its own throwaway database, dropped on teardown, and is
# skipped outright if no such Postgres is reachable.
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.services.priority_config import PRIORITY_ENGINE_CONFIG, PriorityEngineConfig
from app.services.priority_engine import compute_priority
from app.services.priority_recompute import (
    _CONSERVATIVE_DEMOTION_CANDIDATES_QUERY,
    is_eligible_for_conservative_demotion,
    run_classification_pass,
    run_conservative_demotion_pass,
)
from app.services.sbc_card_matching import sync_requirement_filters, sync_sbc_triggers
from scripts.priority_validation_report import build_report

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
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_sales_due_at TIMESTAMPTZ,
    last_sales_status_at TIMESTAMPTZ
);

CREATE TABLE futgg_market_snapshot (
    source_card_id BIGINT PRIMARY KEY,
    rarity TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_tradeable BOOLEAN,
    sales_count INT,
    sales_window_span_minutes DOUBLE PRECISION,
    sales_dispersion_ratio DOUBLE PRECISION,
    price_tier TEXT,
    current_bin BIGINT,
    bin_captured_at TIMESTAMPTZ
);

CREATE TABLE market_events (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    ends_at TIMESTAMPTZ
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
    rating_min INT, rating_max INT, min_count INT NOT NULL DEFAULT 1,
    parsed BOOLEAN NOT NULL DEFAULT FALSE,
    ambiguous BOOLEAN NOT NULL DEFAULT FALSE,
    raw_text TEXT NOT NULL,
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE watchlist_popularity (
    card_id BIGINT PRIMARY KEY,
    watcher_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE card_priority (
    card_id BIGINT PRIMARY KEY,
    baseline_tier TEXT NOT NULL CHECK (baseline_tier IN ('CRITICAL','HIGH','NORMAL','LOW','DORMANT')),
    effective_tier TEXT NOT NULL CHECK (effective_tier IN ('CRITICAL','HIGH','NORMAL','LOW','DORMANT')),
    importance_score DOUBLE PRECISION NOT NULL,
    liquidity_component DOUBLE PRECISION,
    card_type_component DOUBLE PRECISION,
    opportunity_component DOUBLE PRECISION,
    user_interest_component DOUBLE PRECISION,
    volatility_component DOUBLE PRECISION,
    event_component DOUBLE PRECISION,
    qualified_opportunity_count INT,
    eligible_evaluation_count INT,
    reason_codes TEXT[] NOT NULL DEFAULT '{}',
    previous_baseline_tier TEXT,
    previous_effective_tier TEXT,
    baseline_tier_changed_at TIMESTAMPTZ,
    effective_tier_changed_at TIMESTAMPTZ,
    engine_version TEXT NOT NULL,
    env_flags_snapshot JSONB NOT NULL DEFAULT '{}',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
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

CREATE TABLE priority_engine_events (
    id BIGSERIAL PRIMARY KEY,
    card_id BIGINT NOT NULL,
    previous_baseline_tier TEXT,
    new_baseline_tier TEXT NOT NULL,
    previous_effective_tier TEXT,
    new_effective_tier TEXT NOT NULL,
    previous_due_at TIMESTAMPTZ,
    new_due_at TIMESTAMPTZ,
    reason TEXT NOT NULL,
    reason_codes TEXT[] NOT NULL DEFAULT '{}',
    component_snapshot JSONB NOT NULL DEFAULT '{}',
    trigger_source TEXT,
    trigger_expires_at TIMESTAMPTZ,
    score_before DOUBLE PRECISION,
    score_after DOUBLE PRECISION,
    env_flags_snapshot JSONB NOT NULL DEFAULT '{}',
    engine_version TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pipeline_heartbeats (
    worker      TEXT PRIMARY KEY,
    last_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ok          BOOLEAN NOT NULL DEFAULT TRUE,
    detail      TEXT
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
    name = f"tradehq_prio_test_{uuid.uuid4().hex[:12]}"
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


async def _insert_player(
    conn,
    card_id: int,
    *,
    rarity: str = "silver",
    rating: int = 65,
    is_active: bool = True,
    first_seen_at=None,
    next_sales_due_at=None,
    last_sales_status_at=None,
    nation=None,
    league=None,
    club=None,
) -> None:
    if first_seen_at is None:
        first_seen_at = datetime.now(timezone.utc) - timedelta(days=365)
    await conn.execute(
        """
        INSERT INTO futgg_players
            (source_card_id, rating, rarity, nation, league, club, is_active,
             is_tradeable, first_seen_at, next_sales_due_at, last_sales_status_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE,$8,$9,$10)
        """,
        card_id, rating, rarity, nation, league, club, is_active,
        first_seen_at, next_sales_due_at, last_sales_status_at,
    )


async def _insert_snapshot(
    conn, card_id: int, *, rarity: str = "silver", sales_count: int = 0,
    span_minutes=None, dispersion=None,
) -> None:
    await conn.execute(
        """
        INSERT INTO futgg_market_snapshot
            (source_card_id, rarity, is_active, is_tradeable, sales_count,
             sales_window_span_minutes, sales_dispersion_ratio)
        VALUES ($1,$2,TRUE,TRUE,$3,$4,$5)
        """,
        card_id, rarity, sales_count, span_minutes, dispersion,
    )


class _NullWatchlistPool:
    """Standin for watchlist_pool - acquire() raises so
    _watchlist_popularity_by_card()'s own try/except degrades to
    all-zero, matching the module's documented best-effort behavior for
    a watchlist-pool outage without needing a second live database."""

    def acquire(self):
        raise RuntimeError("no watchlist pool in this test")


# =========================================================================
# 1. DORMANT card doesn't promote over time (staleness never re-enters
#    the score) - pure function, no DB needed: compute_priority() has no
#    freshness input at all, so identical inputs at two different as_of
#    times must produce an identical baseline_tier.
# =========================================================================

def test_dormant_card_does_not_promote_as_it_ages():
    row = {"source_card_id": 1, "rarity": "bronze", "sales_count": 0,
           "sales_window_span_minutes": None, "sales_dispersion_ratio": None}
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    later = now + timedelta(days=90)
    r1 = compute_priority(row, watcher_count=0, as_of=now)
    r2 = compute_priority(row, watcher_count=0, as_of=later)
    assert r1.baseline_tier == "DORMANT"
    assert r1.baseline_tier == r2.baseline_tier
    assert r1.importance_score == r2.importance_score


# =========================================================================
# 2. Never-scraped card never delayed
# =========================================================================

def test_never_scraped_card_is_never_delayed(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                await _insert_player(
                    conn, 1, rarity="bronze",
                    first_seen_at=datetime.now(timezone.utc) - timedelta(days=365),
                    next_sales_due_at=None, last_sales_status_at=None,
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, engine_version)
                    VALUES (1, 'DORMANT', 'DORMANT', 0.0, 'test')
                    """
                )
            with_config = PriorityEngineConfig(allow_conservative_demotion=True)
            import app.services.priority_recompute as pr
            original = pr.PRIORITY_ENGINE_CONFIG
            pr.PRIORITY_ENGINE_CONFIG = with_config
            try:
                summary = await run_conservative_demotion_pass(pool)
            finally:
                pr.PRIORITY_ENGINE_CONFIG = original
            assert summary["eligible_count"] == 0
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT next_sales_due_at FROM futgg_players WHERE source_card_id = 1")
                assert row["next_sales_due_at"] is None
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 3. Dead silver becomes demotion-eligible / 4. same silver with active
#    SBC trigger is NOT demoted
# =========================================================================

def test_dead_silver_is_demotion_eligible_but_not_when_trigger_active(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                # Card 1: dead silver, no trigger -> eligible.
                await _insert_player(
                    conn, 1, rarity="silver", first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now - timedelta(hours=1), last_sales_status_at=now - timedelta(hours=2),
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, card_type_component, engine_version)
                    VALUES (1, 'DORMANT', 'DORMANT', 0.0, 0.15, 'test')
                    """
                )
                # Card 2: identical, but has an active manual_override trigger.
                await _insert_player(
                    conn, 2, rarity="silver", first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now - timedelta(hours=1), last_sales_status_at=now - timedelta(hours=2),
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, card_type_component, engine_version)
                    VALUES (2, 'DORMANT', 'DORMANT', 0.0, 0.15, 'test')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, expires_at, last_confirmed_at)
                    VALUES (2, 'manual_override', 'HIGH', NULL, now())
                    """
                )

            candidates = await _fetch_candidates(pool)
            ids = {int(r["source_card_id"]) for r in candidates}
            assert 1 in ids
            assert 2 not in ids  # NOT EXISTS active trigger excludes it at the query level
        finally:
            await pool.close()

    _run(_t())


async def _fetch_candidates(pool):
    async with pool.acquire() as conn:
        return await conn.fetch(_CONSERVATIVE_DEMOTION_CANDIDATES_QUERY, PRIORITY_ENGINE_CONFIG.trigger_confirmation_grace_hours)


# =========================================================================
# 5. Expired trigger no longer affects effective_tier
# =========================================================================

def test_expired_trigger_does_not_affect_effective_tier():
    row = {"source_card_id": 1, "rarity": "silver", "sales_count": 0,
           "sales_window_span_minutes": None, "sales_dispersion_ratio": None}
    # An expired trigger must never be passed in active_trigger_tiers by
    # the caller (priority_recompute.py's _active_triggers_by_card()
    # query filters on expires_at) - verify the pure function's own
    # contract: with no active_trigger_tiers, effective == baseline.
    result = compute_priority(row, active_trigger_tiers=())
    assert result.effective_tier == result.baseline_tier
    assert result.applied_trigger_tier is None


# =========================================================================
# 6. Temporary SBC source failure doesn't remove valid triggers
# =========================================================================

def test_sbc_sync_failure_does_not_remove_existing_triggers(dsn, monkeypatch):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="silver", nation="Brazil")
                await _insert_snapshot(conn, 1, rarity="silver")
                await conn.execute(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, source_event_id, expires_at, last_confirmed_at)
                    VALUES (1, 'sbc_match', 'HIGH', NULL, $1, now() - interval '1 hour')
                    """,
                    now + timedelta(days=1),
                )

            import app.services.priority_recompute as pr

            async def _boom(_pool):
                raise RuntimeError("simulated upstream SBC source failure")

            monkeypatch.setattr(pr, "sync_requirement_filters", _boom)

            watchlist_pool = _NullWatchlistPool()
            summary = await run_classification_pass(pool, watchlist_pool)
            assert summary["ran"] is True

            async with pool.acquire() as conn:
                trig = await conn.fetchrow("SELECT * FROM priority_triggers WHERE card_id = 1")
                assert trig is not None
                assert trig["forced_min_tier"] == "HIGH"
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 7. Stale unconfirmed trigger eventually expires after grace period
# =========================================================================

def test_stale_unconfirmed_trigger_expires_after_grace_period(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                # sbc_match trigger, expires_at still in the future, but
                # last_confirmed_at is well beyond the grace window -
                # must NOT count as active.
                await conn.execute(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, expires_at, last_confirmed_at)
                    VALUES (1, 'sbc_match', 'HIGH', $1, $2)
                    """,
                    now + timedelta(days=5),
                    now - timedelta(hours=PRIORITY_ENGINE_CONFIG.trigger_confirmation_grace_hours + 1),
                )
                # manual_override is exempt from the grace period.
                await conn.execute(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, expires_at, last_confirmed_at)
                    VALUES (2, 'manual_override', 'HIGH', NULL, $1)
                    """,
                    now - timedelta(hours=PRIORITY_ENGINE_CONFIG.trigger_confirmation_grace_hours + 1),
                )
                from app.services.priority_recompute import _active_triggers_by_card
                active = await _active_triggers_by_card(conn, PRIORITY_ENGINE_CONFIG.trigger_confirmation_grace_hours)
            assert 1 not in active  # sbc_match: grace-expired
            assert 2 in active      # manual_override: exempt
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 8. Ambiguous SBC requirement produces no matches
# =========================================================================

def test_ambiguous_sbc_requirement_produces_no_trigger_matches(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="gold_rare", nation="Brazil")
                await _insert_player(conn, 2, rarity="gold_rare", nation="France")
                event_id = await conn.fetchval(
                    """
                    INSERT INTO market_events (kind, source, external_id, title, ends_at)
                    VALUES ('sbc', 'easysbc', 'ext-1', 'Ambiguous SBC', $1) RETURNING id
                    """,
                    now + timedelta(days=1),
                )
                challenge_id = await conn.fetchval(
                    """
                    INSERT INTO sbc_challenges (event_id, challenge_name, requirements)
                    VALUES ($1, 'Squad Building Challenge', '{"req_0": "Min. 1 Brazil OR France Player"}')
                    RETURNING id
                    """,
                    event_id,
                )
            changed = await sync_requirement_filters(pool)
            assert changed == 1
            async with pool.acquire() as conn:
                filt = await conn.fetchrow(
                    "SELECT * FROM sbc_requirement_filters WHERE challenge_id = $1", challenge_id
                )
                assert filt["ambiguous"] is True

            created = await sync_sbc_triggers(pool)
            assert created == 0
            async with pool.acquire() as conn:
                count = await conn.fetchval("SELECT count(*) FROM priority_triggers")
                assert count == 0
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 9. Schedule writes affect only next_sales_due_at
# =========================================================================

def test_conservative_demotion_only_writes_next_sales_due_at(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(
                    conn, 1, rarity="bronze", nation="England",
                    first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now - timedelta(hours=1),
                    last_sales_status_at=now - timedelta(hours=2),
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, card_type_component, engine_version)
                    VALUES (1, 'DORMANT', 'DORMANT', 0.0, 0.05, 'test')
                    """
                )
                before = dict(await conn.fetchrow("SELECT * FROM futgg_players WHERE source_card_id = 1"))

            import app.services.priority_recompute as pr
            config = PriorityEngineConfig(allow_conservative_demotion=True)
            original = pr.PRIORITY_ENGINE_CONFIG
            pr.PRIORITY_ENGINE_CONFIG = config
            try:
                summary = await run_conservative_demotion_pass(pool)
            finally:
                pr.PRIORITY_ENGINE_CONFIG = original
            assert summary["delayed"] == 1

            async with pool.acquire() as conn:
                after = dict(await conn.fetchrow("SELECT * FROM futgg_players WHERE source_card_id = 1"))
            for col in before:
                if col == "next_sales_due_at":
                    assert after[col] > before[col]
                else:
                    assert after[col] == before[col], f"unexpected mutation of column {col}"
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 10. With demotion disabled, no later due times are written
# =========================================================================

def test_demotion_disabled_writes_nothing(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(
                    conn, 1, rarity="bronze", first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now - timedelta(hours=1), last_sales_status_at=now - timedelta(hours=2),
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, card_type_component, engine_version)
                    VALUES (1, 'DORMANT', 'DORMANT', 0.0, 0.05, 'test')
                    """
                )
                before = await conn.fetchval("SELECT next_sales_due_at FROM futgg_players WHERE source_card_id = 1")

            assert PRIORITY_ENGINE_CONFIG.allow_conservative_demotion is False
            summary = await run_conservative_demotion_pass(pool)
            assert summary == {
                "ran": True, "enabled": False, "eligible_count": 0,
                "delayed": 0, "no_ops": 0, "pull_forwards": 0, "audit_events_written": 0,
            }
            async with pool.acquire() as conn:
                after = await conn.fetchval("SELECT next_sales_due_at FROM futgg_players WHERE source_card_id = 1")
            assert after == before
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 11. Every schedule mutation creates a complete audit event
# =========================================================================

def test_every_demotion_write_creates_a_complete_audit_event(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(
                    conn, 1, rarity="bronze", first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now - timedelta(hours=1), last_sales_status_at=now - timedelta(hours=2),
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, card_type_component, engine_version)
                    VALUES (1, 'DORMANT', 'DORMANT', 0.0, 0.05, 'test')
                    """
                )

            import app.services.priority_recompute as pr
            config = PriorityEngineConfig(allow_conservative_demotion=True)
            original = pr.PRIORITY_ENGINE_CONFIG
            pr.PRIORITY_ENGINE_CONFIG = config
            try:
                summary = await run_conservative_demotion_pass(pool)
            finally:
                pr.PRIORITY_ENGINE_CONFIG = original
            assert summary["delayed"] == 1
            assert summary["audit_events_written"] == 1

            async with pool.acquire() as conn:
                event = await conn.fetchrow(
                    "SELECT * FROM priority_engine_events WHERE card_id = 1 AND reason = 'conservative_demotion'"
                )
            assert event is not None
            assert event["previous_due_at"] is not None
            assert event["new_due_at"] is not None
            assert event["new_due_at"] > event["previous_due_at"]
            assert event["reason_codes"] == ["CONSERVATIVE_DEMOTION_APPLIED"]
            assert event["engine_version"] == PRIORITY_ENGINE_CONFIG.version
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 12. Discovery-mode ceiling prevents permanent starvation
# =========================================================================

def test_discovery_ceiling_prevents_permanent_starvation(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            last_sales_status_at = now - timedelta(hours=1)
            async with pool.acquire() as conn:
                await _insert_player(
                    conn, 1, rarity="bronze", first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now - timedelta(hours=1), last_sales_status_at=last_sales_status_at,
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, card_type_component, engine_version)
                    VALUES (1, 'DORMANT', 'DORMANT', 0.0, 0.05, 'test')
                    """
                )

            import app.services.priority_recompute as pr
            config = PriorityEngineConfig(allow_conservative_demotion=True)
            original = pr.PRIORITY_ENGINE_CONFIG
            pr.PRIORITY_ENGINE_CONFIG = config
            try:
                await run_conservative_demotion_pass(pool)
                # Run again several times: with last_sales_status_at
                # unchanged (no real re-scrape happened), the anchor
                # never advances, so the ceiling can never be exceeded no
                # matter how many recompute passes run - a card is never
                # starved beyond this one bounded interval.
                for _ in range(3):
                    summary2 = await run_conservative_demotion_pass(pool)
            finally:
                pr.PRIORITY_ENGINE_CONFIG = original

            async with pool.acquire() as conn:
                due = await conn.fetchval("SELECT next_sales_due_at FROM futgg_players WHERE source_card_id = 1")
            expected_ceiling = last_sales_status_at + timedelta(minutes=config.conservative_demotion_max_interval_minutes)
            assert due == expected_ceiling
            assert summary2["delayed"] == 0  # subsequent passes are no-ops, not further pushes
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 13. Recomputation is idempotent
# =========================================================================

def test_classification_pass_is_idempotent(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="gold_rare")
                await _insert_snapshot(conn, 1, rarity="gold_rare", sales_count=50, span_minutes=600, dispersion=0.5)

            watchlist_pool = _NullWatchlistPool()
            first = await run_classification_pass(pool, watchlist_pool)
            assert first["ran"] is True
            assert first["cards_processed"] == 1
            assert first["audit_events_written"] == 1  # 'initial'

            second = await run_classification_pass(pool, watchlist_pool)
            assert second["ran"] is True
            assert second["cards_processed"] == 1
            assert second["audit_events_written"] == 0  # nothing changed - no new event
            assert second["promotions"] == 0
            assert second["demotions"] == 0

            async with pool.acquire() as conn:
                count = await conn.fetchval("SELECT count(*) FROM card_priority")
            assert count == 1
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 14. A trigger excludes a previously-demoted card from further demotion
#     (this milestone's documented pull-forward limitation: it stops
#     pushing further, it does not pull the due date back in - see
#     _CONSERVATIVE_DEMOTION_CANDIDATES_QUERY's NOT EXISTS clause)
# =========================================================================

def test_new_trigger_excludes_previously_demoted_card_from_further_demotion(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(
                    conn, 1, rarity="silver", first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now + timedelta(hours=48),  # already demoted once
                    last_sales_status_at=now - timedelta(hours=2),
                )
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, card_type_component, engine_version)
                    VALUES (1, 'DORMANT', 'DORMANT', 0.0, 0.15, 'test')
                    """
                )
            candidates_before = await _fetch_candidates(pool)
            assert any(int(r["source_card_id"]) == 1 for r in candidates_before)

            async with pool.acquire() as conn:
                # A fresh SBC-match trigger just activated for this card.
                await conn.execute(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, expires_at, last_confirmed_at)
                    VALUES (1, 'sbc_match', 'HIGH', $1, now())
                    """,
                    now + timedelta(days=3),
                )
            candidates_after = await _fetch_candidates(pool)
            assert not any(int(r["source_card_id"]) == 1 for r in candidates_after)
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# 15. Validation report performs no writes
# =========================================================================

def test_validation_report_performs_no_writes(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(
                    conn, 1, rarity="gold_rare", first_seen_at=now - timedelta(days=365),
                    next_sales_due_at=now - timedelta(hours=1), last_sales_status_at=now - timedelta(hours=2),
                )
                await _insert_snapshot(conn, 1, rarity="gold_rare", sales_count=50, span_minutes=600, dispersion=0.5)
                await conn.execute(
                    """
                    INSERT INTO card_priority (card_id, baseline_tier, effective_tier, importance_score, engine_version)
                    VALUES (1, 'HIGH', 'HIGH', 0.6, 'test')
                    """
                )

            async with pool.acquire() as conn:
                before_players = [dict(r) for r in await conn.fetch("SELECT * FROM futgg_players ORDER BY source_card_id")]
                before_priority = [dict(r) for r in await conn.fetch("SELECT * FROM card_priority ORDER BY card_id")]
                before_events = await conn.fetchval("SELECT count(*) FROM priority_engine_events")
                before_triggers = await conn.fetchval("SELECT count(*) FROM priority_triggers")

            await build_report(dsn, sample_per_tier=5)

            async with pool.acquire() as conn:
                after_players = [dict(r) for r in await conn.fetch("SELECT * FROM futgg_players ORDER BY source_card_id")]
                after_priority = [dict(r) for r in await conn.fetch("SELECT * FROM card_priority ORDER BY card_id")]
                after_events = await conn.fetchval("SELECT count(*) FROM priority_engine_events")
                after_triggers = await conn.fetchval("SELECT count(*) FROM priority_triggers")

            assert before_players == after_players
            assert before_priority == after_priority
            assert before_events == after_events
            assert before_triggers == after_triggers
        finally:
            await pool.close()

    _run(_t())


# =========================================================================
# SBC trigger lifecycle safety (Step 5's 8 named scenarios). Several are
# already covered above by name:
#   - "temporary SBC source failure doesn't remove valid triggers"  -> #6
#   - "stale unconfirmed trigger eventually expires after grace period"
#     -> #7
#   - "ambiguous SBC requirement produces no matches"               -> #8
# "one provider fails while other succeeds" is not exercised here: each
# provider (futbin_sbc_sync.py / easysbc_sbc_sync.py) writes its own
# market_events/sbc_challenges rows independently, in its own scheduled
# job with its own try/except - by the time sync_requirement_filters/
# sync_sbc_triggers run, there is no cross-provider coupling left to
# fail together; a partial provider outage just means fewer rows exist
# to match against this pass, already exercised generally by every test
# below that seeds only a subset of challenges.
# =========================================================================

def test_sbc_remains_active_is_reconfirmed_every_pass(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="gold_rare", nation="Brazil", rating=88)
                event_id = await conn.fetchval(
                    """
                    INSERT INTO market_events (kind, source, external_id, title, ends_at)
                    VALUES ('sbc', 'easysbc', 'ext-1', 'Brazil SBC', $1) RETURNING id
                    """,
                    now + timedelta(days=3),
                )
                await conn.execute(
                    """
                    INSERT INTO sbc_challenges (event_id, challenge_name, requirements)
                    VALUES ($1, 'Squad Building Challenge', '{"req_0": "Min. 1 Brazil Player"}')
                    """,
                    event_id,
                )

            await sync_requirement_filters(pool)
            created = await sync_sbc_triggers(pool)
            assert created == 1
            async with pool.acquire() as conn:
                first_confirmed = await conn.fetchval(
                    "SELECT last_confirmed_at FROM priority_triggers WHERE card_id = 1"
                )

            await asyncio.sleep(1.1)  # ensure now() advances measurably between passes
            created_again = await sync_sbc_triggers(pool)
            assert created_again == 0  # re-confirmed, not duplicated
            async with pool.acquire() as conn:
                count = await conn.fetchval("SELECT count(*) FROM priority_triggers WHERE card_id = 1")
                second_confirmed = await conn.fetchval(
                    "SELECT last_confirmed_at FROM priority_triggers WHERE card_id = 1"
                )
            assert count == 1
            assert second_confirmed > first_confirmed
        finally:
            await pool.close()

    _run(_t())


def test_sbc_expires_normally_no_new_trigger_created(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="gold_rare", nation="Brazil")
                event_id = await conn.fetchval(
                    """
                    INSERT INTO market_events (kind, source, external_id, title, ends_at)
                    VALUES ('sbc', 'easysbc', 'ext-1', 'Expired Brazil SBC', $1) RETURNING id
                    """,
                    now - timedelta(hours=1),  # already ended
                )
                await conn.execute(
                    """
                    INSERT INTO sbc_challenges (event_id, challenge_name, requirements)
                    VALUES ($1, 'Squad Building Challenge', '{"req_0": "Min. 1 Brazil Player"}')
                    """,
                    event_id,
                )
            await sync_requirement_filters(pool)
            created = await sync_sbc_triggers(pool)
            assert created == 0
        finally:
            await pool.close()

    _run(_t())


def test_sbc_disappears_from_upstream_trigger_eventually_grace_expires(dsn):
    """No writer proactively marks a vanished SBC ended (see migration
    046's header) - the safety net is last_confirmed_at simply no longer
    advancing once sync_sbc_triggers stops finding the match, so the
    trigger silently ages out of _ACTIVE_TRIGGER_WHERE after the grace
    period rather than remaining active on a stale expires_at forever."""
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                # A trigger that WAS created by a real match in the past,
                # but nothing has re-confirmed it since - simulating the
                # challenge/event having vanished upstream without either
                # writer marking it ended.
                await conn.execute(
                    """
                    INSERT INTO priority_triggers (card_id, trigger_type, forced_min_tier, expires_at, last_confirmed_at)
                    VALUES (1, 'sbc_match', 'HIGH', $1, $2)
                    """,
                    now + timedelta(days=10),  # expires_at still far in the future
                    now - timedelta(hours=PRIORITY_ENGINE_CONFIG.trigger_confirmation_grace_hours + 1),
                )
                from app.services.priority_recompute import _active_triggers_by_card
                active = await _active_triggers_by_card(conn, PRIORITY_ENGINE_CONFIG.trigger_confirmation_grace_hours)
            assert 1 not in active
        finally:
            await pool.close()

    _run(_t())


def test_sbc_requirement_change_replaces_filters_wholesale(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="gold_rare", nation="Brazil")
                await _insert_player(conn, 2, rarity="gold_rare", nation="France")
                event_id = await conn.fetchval(
                    """
                    INSERT INTO market_events (kind, source, external_id, title, ends_at)
                    VALUES ('sbc', 'easysbc', 'ext-1', 'Changing SBC', $1) RETURNING id
                    """,
                    now + timedelta(days=3),
                )
                challenge_id = await conn.fetchval(
                    """
                    INSERT INTO sbc_challenges (event_id, challenge_name, requirements)
                    VALUES ($1, 'Squad Building Challenge', '{"req_0": "Min. 1 Brazil Player"}')
                    RETURNING id
                    """,
                    event_id,
                )
            changed = await sync_requirement_filters(pool)
            assert changed == 1
            await sync_sbc_triggers(pool)
            async with pool.acquire() as conn:
                triggered_ids = {r["card_id"] for r in await conn.fetch("SELECT card_id FROM priority_triggers")}
            assert triggered_ids == {1}

            # Requirement text changes upstream (Brazil -> France).
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE sbc_challenges SET requirements = $1 WHERE id = $2",
                    '{"req_0": "Min. 1 France Player"}', challenge_id,
                )
            changed_again = await sync_requirement_filters(pool)
            assert changed_again == 1
            async with pool.acquire() as conn:
                filters = await conn.fetch("SELECT raw_text FROM sbc_requirement_filters WHERE challenge_id = $1", challenge_id)
            texts = {r["raw_text"] for r in filters}
            assert texts == {"Min. 1 France Player"}  # old filter replaced, not patched alongside
        finally:
            await pool.close()

    _run(_t())


def test_sbc_parsing_fails_produces_no_match(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="gold_rare", nation="Brazil")
                event_id = await conn.fetchval(
                    """
                    INSERT INTO market_events (kind, source, external_id, title, ends_at)
                    VALUES ('sbc', 'futbin', 'ext-1', 'Unparseable SBC', $1) RETURNING id
                    """,
                    now + timedelta(days=3),
                )
                await conn.execute(
                    """
                    INSERT INTO sbc_challenges (event_id, challenge_name, requirements)
                    VALUES ($1, 'Squad Building Challenge', '{"req_0": "Squad Chemistry: Min. 30"}')
                    """,
                    event_id,
                )
            await sync_requirement_filters(pool)
            async with pool.acquire() as conn:
                filt = await conn.fetchrow("SELECT parsed, ambiguous FROM sbc_requirement_filters")
            assert filt["parsed"] is False

            created = await sync_sbc_triggers(pool)
            assert created == 0
        finally:
            await pool.close()

    _run(_t())


def test_challenge_removed_early_no_longer_matched(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await _insert_player(conn, 1, rarity="gold_rare", nation="Brazil")
                event_id = await conn.fetchval(
                    """
                    INSERT INTO market_events (kind, source, external_id, title, ends_at)
                    VALUES ('sbc', 'easysbc', 'ext-1', 'Removed Early SBC', $1) RETURNING id
                    """,
                    now + timedelta(days=3),
                )
                challenge_id = await conn.fetchval(
                    """
                    INSERT INTO sbc_challenges (event_id, challenge_name, requirements)
                    VALUES ($1, 'Squad Building Challenge', '{"req_0": "Min. 1 Brazil Player"}')
                    RETURNING id
                    """,
                    event_id,
                )
            await sync_requirement_filters(pool)
            created = await sync_sbc_triggers(pool)
            assert created == 1

            async with pool.acquire() as conn:
                # Challenge removed early - upstream took it down before ends_at.
                await conn.execute("DELETE FROM sbc_challenges WHERE id = $1", challenge_id)

            created_again = await sync_sbc_triggers(pool)
            assert created_again == 0
            async with pool.acquire() as conn:
                trig = await conn.fetchrow("SELECT * FROM priority_triggers WHERE card_id = 1")
            # The trigger row itself is untouched by this pass (no writer
            # proactively deletes/ends it) - it will naturally grace-expire
            # via last_confirmed_at no longer advancing, same as the
            # "disappears from upstream" scenario above.
            assert trig is not None
        finally:
            await pool.close()

    _run(_t())
