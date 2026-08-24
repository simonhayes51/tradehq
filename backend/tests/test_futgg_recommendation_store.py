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

from app.services.futgg_recommendation_store import recent_outcomes, track_record

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

-- Real deployments use the migration-038/041/065 materialized view;
-- recent_outcomes() only ever LEFT JOINs it for display (name/image), so
-- a plain table with the same columns is a faithful stand-in here.
CREATE TABLE futgg_market_snapshot (
    source_card_id      BIGINT PRIMARY KEY,
    name                 TEXT,
    display_name         TEXT,
    player_image_url     TEXT,
    card_image_url        TEXT,
    card_image_stored_url TEXT
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


async def _seed_one(
    conn, *, source_card_id, horizon, outcome_status, target_hit, realised_roi,
    entry_achieved=True, evaluated_days_ago=2, player_name=None, entry_price=50000,
):
    snapshot_id = await conn.fetchval(
        """
        INSERT INTO futgg_recommendation_snapshots
            (source_card_id, evaluated_at, signal, status, engine_version, trend_version,
             recommended_buy_max, recommended_sell_target, rating, rarity)
        VALUES ($1, now() - ($2 || ' days')::interval, 'buy', 'BUY', 'futgg-v1', 'trend-v1',
                50000, 55000, 91, 'gold_rare')
        RETURNING id
        """,
        source_card_id, str(evaluated_days_ago),
    )
    await conn.execute(
        """
        INSERT INTO futgg_recommendation_outcomes
            (snapshot_id, source_card_id, horizon, window_start, window_end, observation_count,
             entry_achieved, entry_at, entry_price, target_hit, realised_roi, outcome_status, grader_version)
        VALUES ($1, $2, $3, now() - interval '2 days', now() - interval '1 day', 10,
                $4, CASE WHEN $4 THEN now() - interval '2 days' ELSE NULL END,
                CASE WHEN $4 THEN $8::integer ELSE NULL END, $5, $6, $7, 'grader-v1')
        """,
        snapshot_id, source_card_id, horizon, entry_achieved, target_hit, realised_roi, outcome_status,
        entry_price,
    )
    if player_name:
        await conn.execute(
            """
            INSERT INTO futgg_market_snapshot (source_card_id, name, display_name)
            VALUES ($1, $2, $2)
            ON CONFLICT (source_card_id) DO UPDATE SET name = EXCLUDED.name
            """,
            source_card_id, player_name,
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


def test_recent_outcomes_returns_wins_losses_and_no_entry_correctly(dsn):
    async def _scenario():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            async with pool.acquire() as conn:
                await _seed_one(conn, source_card_id=10, horizon="24h",
                                 outcome_status="target_hit", target_hit=True,
                                 realised_roi=0.12, player_name="Winner Card")
                await _seed_one(conn, source_card_id=11, horizon="24h",
                                 outcome_status="downside_hit", target_hit=False,
                                 realised_roi=-0.06, player_name="Loser Card")
                await _seed_one(conn, source_card_id=12, horizon="24h",
                                 outcome_status="no_entry", target_hit=False,
                                 realised_roi=None, entry_achieved=False,
                                 player_name="Never Entered Card")
            all_rows = await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="all", limit=40)
            wins = await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="wins", limit=40)
            losses = await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="losses", limit=40)
            no_entry = await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="no_entry", limit=40)
            return all_rows, wins, losses, no_entry
        finally:
            await pool.close()

    all_rows, wins, losses, no_entry = _run(_scenario())

    assert len(all_rows) == 3
    assert {r["player_name"] for r in all_rows} == {"Winner Card", "Loser Card", "Never Entered Card"}

    assert len(wins) == 1
    assert wins[0]["player_name"] == "Winner Card"
    assert wins[0]["result"] == "win"
    assert wins[0]["entry_achieved"] is True

    assert len(losses) == 1
    assert losses[0]["player_name"] == "Loser Card"
    assert losses[0]["result"] == "loss"

    assert len(no_entry) == 1
    assert no_entry[0]["player_name"] == "Never Entered Card"
    assert no_entry[0]["result"] == "no_entry"
    assert no_entry[0]["entry_achieved"] is False
    # No entry -> no fabricated entry price/time.
    assert no_entry[0]["entry_price"] is None
    assert no_entry[0]["entry_at"] is None


def test_recent_outcomes_respects_window_days_boundary(dsn):
    async def _scenario():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            async with pool.acquire() as conn:
                await _seed_one(conn, source_card_id=20, horizon="24h",
                                 outcome_status="target_hit", target_hit=True,
                                 realised_roi=0.10, evaluated_days_ago=5,
                                 player_name="Recent Card")
                await _seed_one(conn, source_card_id=21, horizon="24h",
                                 outcome_status="target_hit", target_hit=True,
                                 realised_roi=0.10, evaluated_days_ago=60,
                                 player_name="Old Card")
            within_7d = await recent_outcomes(pool, horizon="24h", window_days=7, result_filter="all", limit=40)
            within_90d = await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="all", limit=40)
            return within_7d, within_90d
        finally:
            await pool.close()

    within_7d, within_90d = _run(_scenario())
    assert {r["player_name"] for r in within_7d} == {"Recent Card"}
    assert {r["player_name"] for r in within_90d} == {"Recent Card", "Old Card"}


def test_recent_outcomes_limit_is_capped(dsn):
    async def _scenario():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            async with pool.acquire() as conn:
                for i in range(5):
                    await _seed_one(conn, source_card_id=30 + i, horizon="24h",
                                     outcome_status="target_hit", target_hit=True,
                                     realised_roi=0.10, player_name=f"Card {i}")
            return await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="all", limit=2)
        finally:
            await pool.close()

    rows = _run(_scenario())
    assert len(rows) == 2


def test_recent_outcomes_rejects_unknown_filter(dsn):
    async def _scenario():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            with pytest.raises(ValueError):
                await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="not_a_real_filter")
        finally:
            await pool.close()

    _run(_scenario())


def test_track_record_excludes_sub_minimum_entry_price_from_averages(dsn):
    # Production incident: a handful of graded rows with a glitched/bogus
    # entry_price below EA's real 200-coin minimum produced realised_roi
    # in the thousands of percent, dragging the reported average ROI to
    # ~212% while the median stayed a sane ~22%. futgg_outcome_grader.py
    # no longer selects such a price as an entry going forward, but this
    # is the read-side guard for rows already poisoned before that fix.
    async def _scenario():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            async with pool.acquire() as conn:
                # Five normal, sane outcomes.
                for i in range(5):
                    await _seed_one(
                        conn, source_card_id=100 + i, horizon="24h",
                        outcome_status="target_hit", target_hit=True,
                        realised_roi=0.20, entry_price=50000,
                    )
                # One poisoned row: entry_price=1, realised_roi computed
                # against it comes out absurd (this mirrors what the old,
                # unguarded grader could have produced/what a pre-fix row
                # already sitting in the table would look like).
                await _seed_one(
                    conn, source_card_id=200, horizon="24h",
                    outcome_status="target_hit", target_hit=True,
                    realised_roi=45.0, entry_price=1,
                )
            result = await track_record(pool, horizon="24h", window_days=90, min_sample=1)
            return result
        finally:
            await pool.close()

    result = _run(_scenario())
    head = result["headline"]
    # The poisoned row must be excluded entirely from entered/avg/median -
    # only the 5 sane rows count.
    assert head["entered"] == 5
    assert head["avg_roi_pct"] == 20.0
    assert head["median_roi_pct"] == 20.0
    # The poisoned row is excluded from total_recommendations too, not
    # just from the ROI/profit aggregates - a row whose entry_price is
    # known-bad data isn't a trustworthy graded call at all, and leaving
    # it in `total` while excluding it from `entered` would make the row
    # look exactly like a legitimate no_entry outcome, which it is not.
    assert head["total_recommendations"] == 5


def test_recent_outcomes_excludes_sub_minimum_entry_price_rows(dsn):
    async def _scenario():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            async with pool.acquire() as conn:
                await _seed_one(conn, source_card_id=300, horizon="24h",
                                 outcome_status="target_hit", target_hit=True,
                                 realised_roi=0.15, entry_price=50000,
                                 player_name="Sane Card")
                await _seed_one(conn, source_card_id=301, horizon="24h",
                                 outcome_status="target_hit", target_hit=True,
                                 realised_roi=45.0, entry_price=1,
                                 player_name="Poisoned Card")
            return await recent_outcomes(pool, horizon="24h", window_days=90, result_filter="all", limit=40)
        finally:
            await pool.close()

    rows = _run(_scenario())
    names = {r["player_name"] for r in rows}
    assert "Sane Card" in names
    assert "Poisoned Card" not in names
