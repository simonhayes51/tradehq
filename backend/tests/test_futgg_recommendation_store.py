# tests/test_futgg_recommendation_store.py
#
# DB-backed regression test for the /track-record interval-binding bug:
# track_record() built `interval = f"{window_days} days"` (a plain str)
# and bound it against a query parameter cast with an explicit
# `$2::interval`. asyncpg encodes an explicitly-`::interval`-cast
# parameter with its interval codec, which requires a real timedelta (it
# reads `.days` off the value) - the string blew up in asyncpg's bind
# encoding on every single call in production, which the router's
# `except Exception: pass` swallowed, making every /track-record request
# silently fall back to the legacy ml_labels path regardless of how much
# real futgg_recommendation_outcomes data existed.
#
# Same "real throwaway Postgres" convention as test_sbc_card_matching.py.
# Needs TEST_ADMIN_DATABASE_URL (default
# postgresql://postgres:postgres@localhost:5432/postgres) - skipped
# outright if unreachable.
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from app.services.futgg_recommendation_store import track_record

ADMIN_DSN = os.getenv(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)

_SCHEMA_SQL = """
CREATE TABLE futgg_recommendation_snapshots (
    id                        BIGSERIAL PRIMARY KEY,
    source_card_id            BIGINT NOT NULL,
    evaluated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    current_bin               INTEGER,
    bin_captured_at           TIMESTAMPTZ,
    price_age_minutes         INTEGER,
    sales_median              NUMERIC,
    sales_trimmed_mean        NUMERIC,
    sales_count               INTEGER NOT NULL DEFAULT 0,
    sales_window_earliest_at  TIMESTAMPTZ,
    sales_window_latest_at    TIMESTAMPTZ,
    sales_window_span_minutes NUMERIC,
    sales_dispersion_ratio    NUMERIC,
    latest_sale_price         INTEGER,
    price_tier                TEXT,
    rating                    INTEGER,
    rarity                    TEXT,
    fair_value                NUMERIC,
    theoretical_max_buy       NUMERIC,
    recommended_buy_max       NUMERIC,
    current_executable_buy    NUMERIC,
    break_even_price          NUMERIC,
    recommended_sell_target   NUMERIC,
    buy_below                 NUMERIC,
    expected_profit_after_tax NUMERIC,
    expected_roi              NUMERIC,
    confidence_score          NUMERIC,
    liquidity_score           NUMERIC,
    risk_level                TEXT,
    signal                    TEXT NOT NULL,
    status                    TEXT NOT NULL,
    trend_state               TEXT,
    trend_features            JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason_codes              TEXT[] NOT NULL DEFAULT '{}',
    blocking_codes            TEXT[] NOT NULL DEFAULT '{}',
    reasons                   JSONB NOT NULL DEFAULT '[]'::jsonb,
    engine_version            TEXT NOT NULL,
    trend_version             TEXT NOT NULL,
    engine_config             JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at                TIMESTAMPTZ,
    expiry_minutes            INTEGER,
    invalidated_at            TIMESTAMPTZ,
    invalidated_reason        TEXT
);

CREATE TABLE futgg_recommendation_outcomes (
    id                     BIGSERIAL PRIMARY KEY,
    snapshot_id            BIGINT NOT NULL REFERENCES futgg_recommendation_snapshots(id) ON DELETE CASCADE,
    source_card_id         BIGINT NOT NULL,
    horizon                TEXT NOT NULL CHECK (horizon IN ('24h', '48h', '7d')),
    graded_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_start           TIMESTAMPTZ NOT NULL,
    window_end             TIMESTAMPTZ NOT NULL,
    observation_count      INTEGER NOT NULL DEFAULT 0,
    entry_achieved         BOOLEAN NOT NULL DEFAULT FALSE,
    entry_at               TIMESTAMPTZ,
    entry_price            INTEGER,
    exit_achieved          BOOLEAN NOT NULL DEFAULT FALSE,
    exit_at                TIMESTAMPTZ,
    realised_sell_price    INTEGER,
    net_profit_after_tax   NUMERIC,
    realised_roi           NUMERIC,
    max_favourable_excursion NUMERIC,
    max_adverse_excursion    NUMERIC,
    target_hit             BOOLEAN NOT NULL DEFAULT FALSE,
    downside_hit           BOOLEAN NOT NULL DEFAULT FALSE,
    minutes_to_target      INTEGER,
    outcome_status         TEXT NOT NULL,
    grader_version         TEXT NOT NULL,
    UNIQUE (snapshot_id, horizon)
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
    name = f"tradehq_track_record_test_{uuid.uuid4().hex[:12]}"
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


async def _seed_one(conn, *, source_card_id, horizon, outcome_status, target_hit, realised_roi):
    snapshot_id = await conn.fetchval(
        """
        INSERT INTO futgg_recommendation_snapshots
            (source_card_id, evaluated_at, signal, status, engine_version, trend_version)
        VALUES ($1, now() - interval '2 days', 'buy', 'BUY', 'futgg-v1', 'trend-v1')
        RETURNING id
        """,
        source_card_id,
    )
    await conn.execute(
        """
        INSERT INTO futgg_recommendation_outcomes
            (snapshot_id, source_card_id, horizon, window_start, window_end, observation_count,
             entry_achieved, entry_at, entry_price, target_hit, realised_roi, outcome_status, grader_version)
        VALUES ($1, $2, $3, now() - interval '2 days', now() - interval '1 day', 10,
                true, now() - interval '2 days', 50000, $4, $5, $6, 'grader-v1')
        """,
        snapshot_id, source_card_id, horizon, target_hit, realised_roi, outcome_status,
    )


def test_track_record_does_not_raise_and_reflects_real_outcome_data(dsn):
    async def _scenario():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            async with pool.acquire() as conn:
                await _seed_one(
                    conn, source_card_id=1, horizon="24h",
                    outcome_status="target_hit", target_hit=True, realised_roi=0.12,
                )
                await _seed_one(
                    conn, source_card_id=2, horizon="24h",
                    outcome_status="loss_unrealised", target_hit=False, realised_roi=-0.05,
                )
            # This is the exact call app/routers/v2/recommendations.py's
            # /track-record endpoint makes. Before the fix this raised
            # asyncpg.exceptions.DataError inside track_record() itself.
            return await track_record(pool, horizon="24h", window_days=90, min_sample=1)
        finally:
            await pool.close()

    result = _run(_scenario())
    assert result["headline"]["total_recommendations"] == 2
    assert result["headline"]["entered"] == 2
    assert result["has_enough_data"] is True
