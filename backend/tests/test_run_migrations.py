"""
Tests for scripts/run_migrations.py's single-database migration
architecture: three targets (core/player/watchlist) sharing one DSN and
therefore one schema_migrations table, migration identity by
(target, filename) rather than filename alone, and the requires-table/
requires-column clean-skip machinery that lets a migration wait on schema
owned by another service instead of crashing the boot.

Needs a real Postgres reachable at TEST_ADMIN_DATABASE_URL (default
postgresql://postgres:postgres@localhost:5432/postgres) with CREATE
DATABASE privileges - each test gets its own throwaway database, dropped
on teardown. Tests are skipped outright if that's not reachable.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest

from scripts import run_migrations as rm

ADMIN_DSN = os.getenv(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)


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
    name = f"tradehq_test_{uuid.uuid4().hex[:12]}"
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
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            name,
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
    try:
        yield _dsn_for(name)
    finally:
        _run(_drop_db(name))


def _legacy_futbin_fut_players(conn_dsn: str) -> None:
    """Seeds the FUTBIN-shaped fut_players + sales_history/bin_history
    tables migration 011 expects, so it can actually apply (used by tests
    that want the pre-FUT.GG legacy path to succeed rather than skip)."""

    async def _seed():
        conn = await asyncpg.connect(conn_dsn)
        try:
            await conn.execute(
                """
                CREATE TABLE fut_players (
                    card_id BIGINT PRIMARY KEY,
                    name TEXT,
                    rating INTEGER,
                    version TEXT,
                    position TEXT,
                    image_url TEXT
                )
                """
            )
            await conn.execute(
                "CREATE TABLE sales_history (player_id BIGINT, sold_price NUMERIC, sold_at TIMESTAMPTZ)"
            )
            await conn.execute(
                "CREATE TABLE bin_history (player_id BIGINT, platform TEXT, lowest_bin NUMERIC, captured_at TIMESTAMPTZ)"
            )
        finally:
            await conn.close()

    _run(_seed())


def _restored_prod_fut_players(conn_dsn: str) -> None:
    """Seeds fut_players the way a restored/FUT.GG-only production database
    has it: created by this repo's own migration 017 bootstrap (card_id
    only) plus whatever additive columns 022/025 would add - never touched
    by the external auto_sync FUTBIN scraper, so name/rating/version/
    position/image_url were never written. This is the exact shape that
    made migration 011 crash boot with 'column p.name does not exist'
    before the requires-column fix."""

    async def _seed():
        conn = await asyncpg.connect(conn_dsn)
        try:
            await conn.execute(
                "CREATE TABLE fut_players (card_id BIGINT PRIMARY KEY, card_bg_image TEXT, rarity TEXT)"
            )
            await conn.execute(
                "CREATE TABLE sales_history (player_id BIGINT, sold_price NUMERIC, sold_at TIMESTAMPTZ)"
            )
            await conn.execute(
                "CREATE TABLE bin_history (player_id BIGINT, platform TEXT, lowest_bin NUMERIC, captured_at TIMESTAMPTZ)"
            )
        finally:
            await conn.close()

    _run(_seed())


def _futgg_tables(conn_dsn: str) -> None:
    """Seeds the minimal FUT.GG-backed tables migrations 038/040 require,
    so a full run_on_boot() pass can be exercised end to end."""

    async def _seed():
        conn = await asyncpg.connect(conn_dsn)
        try:
            await conn.execute(
                """
                CREATE TABLE futgg_players (
                    source_card_id BIGINT PRIMARY KEY,
                    source_player_id BIGINT,
                    source_slug TEXT,
                    source_url TEXT,
                    game_year INTEGER,
                    name TEXT,
                    rating INTEGER,
                    primary_position TEXT,
                    alternate_positions TEXT[],
                    rarity TEXT,
                    squad TEXT,
                    price_tier TEXT,
                    club TEXT,
                    league TEXT,
                    nation TEXT,
                    club_source_id BIGINT,
                    league_source_id BIGINT,
                    nation_source_id BIGINT,
                    player_image_url TEXT,
                    card_design_image_url TEXT,
                    club_image_url TEXT,
                    league_image_url TEXT,
                    nation_image_url TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    is_tradeable BOOLEAN,
                    last_price_status TEXT,
                    metadata_warnings TEXT,
                    price_updated_at TIMESTAMPTZ,
                    next_price_due_at TIMESTAMPTZ
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE futgg_bin_history (
                    source_card_id BIGINT,
                    lowest_bin INTEGER,
                    price_range_low INTEGER,
                    price_range_high INTEGER,
                    source_age_text TEXT,
                    captured_at TIMESTAMPTZ
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE futgg_sales_history (
                    source_card_id BIGINT,
                    sold_price INTEGER,
                    approximate_sold_at TIMESTAMPTZ
                )
                """
            )
        finally:
            await conn.close()

    _run(_seed())


# ---------------------------------------------------------------------
# Three targets, one DSN
# ---------------------------------------------------------------------


def test_three_targets_share_one_dsn_and_all_boot_clean(dsn):
    """The Coolify deploy this bug report describes: DATABASE_URL,
    PLAYER_DATABASE_URL and WATCHLIST_DATABASE_URL all point at the same
    restored database. run_on_boot() must not raise, and each target's
    migrations must land in the shared schema_migrations table keyed by
    its own target."""
    _restored_prod_fut_players(dsn)
    _futgg_tables(dsn)

    _run(rm.run_on_boot(dsn, dsn, dsn))

    async def _check():
        conn = await asyncpg.connect(dsn)
        try:
            targets = {
                r["target"] for r in await conn.fetch("SELECT DISTINCT target FROM schema_migrations")
            }
            # core's FUT.GG migrations (038/040) and watchlist's own
            # bootstrap (016/039) must both have actually applied.
            assert "core" in targets
            assert "watchlist" in targets
            assert await conn.fetchval(
                "SELECT to_regclass('public.futgg_market_snapshot')"
            ) is not None
            assert await conn.fetchval(
                "SELECT to_regclass('public.watchlist')"
            ) is not None
        finally:
            await conn.close()

    _run(_check())


def test_running_all_three_targets_is_idempotent(dsn):
    """A second run_on_boot() against the same shared DSN (e.g. the next
    container restart) must execute zero additional migrations - nothing
    should look "still pending" just because three targets share a table."""
    _restored_prod_fut_players(dsn)
    _futgg_tables(dsn)
    _run(rm.run_on_boot(dsn, dsn, dsn))

    ran_core = _run(rm.run(dsn, target="core"))
    ran_watchlist = _run(rm.run(dsn, target="watchlist"))

    assert ran_core == 0
    assert ran_watchlist == 0


# ---------------------------------------------------------------------
# Migration identity: (target, filename), not filename alone
# ---------------------------------------------------------------------


def test_schema_migrations_primary_key_is_target_and_filename(dsn):
    _run(rm.run(dsn, target="core"))

    async def _check():
        conn = await asyncpg.connect(dsn)
        try:
            cols = await conn.fetch(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = 'schema_migrations'::regclass AND i.indisprimary
                """
            )
            pk_columns = {r["attname"] for r in cols}
            assert pk_columns == {"target", "filename"}
        finally:
            await conn.close()

    _run(_check())


def test_same_filename_under_two_targets_does_not_collide(dsn):
    """Regression test for the bug a filename-only PK would reintroduce
    now that a single DB shares one schema_migrations table across all
    three targets: recording a migration as applied for "player" must not
    make an identically-named "watchlist" migration look already-applied,
    and inserting both must not violate a unique constraint."""

    async def _check():
        conn = await asyncpg.connect(dsn)
        try:
            await rm._ensure_schema_migrations_table(conn)
            await conn.execute(
                "INSERT INTO schema_migrations (target, filename) VALUES ('player', '999_shared_name.sql')"
            )
            # Same filename, different target - must be a distinct row,
            # not silently dropped by ON CONFLICT and not a PK violation.
            await conn.execute(
                "INSERT INTO schema_migrations (target, filename) VALUES ('watchlist', '999_shared_name.sql')"
            )
            rows = await conn.fetch(
                "SELECT target FROM schema_migrations WHERE filename = '999_shared_name.sql' ORDER BY target"
            )
            assert [r["target"] for r in rows] == ["player", "watchlist"]
        finally:
            await conn.close()

    _run(_check())


def test_legacy_filename_only_table_upgrades_in_place(dsn):
    """A production database that already ran migrations under the old
    filename-PRIMARY-KEY schema_migrations (single-DB-per-target world)
    must upgrade cleanly on the next boot: existing rows keep their
    applied_at, get backfilled with the target their own file declares,
    and the table ends up on the new composite PK - without re-running
    anything or losing history."""

    async def _seed_legacy():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                """
                CREATE TABLE schema_migrations (
                    filename   TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # A real core migration and a real player migration, exactly
            # as they'd have been recorded by the pre-fix runner.
            await conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES ('010_core_overhaul.sql')"
            )
            await conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES ('017_fut_players_bootstrap.sql')"
            )
        finally:
            await conn.close()

    _run(_seed_legacy())

    async def _upgrade_and_check():
        conn = await asyncpg.connect(dsn)
        try:
            await rm._ensure_schema_migrations_table(conn)
            rows = {
                r["filename"]: r["target"]
                for r in await conn.fetch("SELECT filename, target FROM schema_migrations")
            }
            assert rows["010_core_overhaul.sql"] == "core"
            assert rows["017_fut_players_bootstrap.sql"] == "player"

            pk_cols = {
                r["attname"]
                for r in await conn.fetch(
                    """
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = 'schema_migrations'::regclass AND i.indisprimary
                    """
                )
            }
            assert pk_cols == {"target", "filename"}
        finally:
            await conn.close()

    _run(_upgrade_and_check())

    # Real migrations already marked applied must not re-execute - if the
    # upgrade mis-tagged either row's target, this either raises (PK
    # collision) or double-runs 010_core_overhaul.sql/017's bootstrap.
    _restored_prod_fut_players(dsn)
    _futgg_tables(dsn)
    _run(rm.run(dsn, target="core"))
    _run(rm.run(dsn, target="player"))

    async def _still_recorded_once_each():
        conn = await asyncpg.connect(dsn)
        try:
            count = await conn.fetchval(
                "SELECT count(*) FROM schema_migrations WHERE filename IN "
                "('010_core_overhaul.sql', '017_fut_players_bootstrap.sql')"
            )
            assert count == 2
        finally:
            await conn.close()

    _run(_still_recorded_once_each())


# ---------------------------------------------------------------------
# Legacy fut_players shape (the actual boot failure this bug reports)
# ---------------------------------------------------------------------


def test_legacy_player_migration_skips_cleanly_on_incompatible_fut_players(dsn):
    """migration 011 must not raise on a database whose fut_players
    lacks the FUTBIN metadata columns (name/rating/version/position/
    image_url) - the exact shape a restored, FUT.GG-only production
    database has. It should skip cleanly and NOT be recorded as applied,
    so it's retried (and can succeed) later if that data ever shows up."""
    _restored_prod_fut_players(dsn)

    # Must not raise (this reproduces the exact boot failure from the bug
    # report) - other player migrations not gated on FUTBIN columns still
    # apply normally, only 011 (and its fair_value_mv-rebuilding siblings)
    # skip cleanly.
    _run(rm.run(dsn, target="player"))

    async def _check():
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval(
                "SELECT to_regclass('public.fair_value_mv')"
            ) is None
            applied = await conn.fetchval(
                "SELECT count(*) FROM schema_migrations WHERE filename = '011_player_fair_value.sql'"
            )
            assert applied == 0
            # A migration with no FUTBIN-column dependency (just needs
            # fut_players.card_id, which the restored DB does have) must
            # still have applied normally.
            applied_bootstrap = await conn.fetchval(
                "SELECT count(*) FROM schema_migrations WHERE filename = '017_fut_players_bootstrap.sql'"
            )
            assert applied_bootstrap == 1
        finally:
            await conn.close()

    _run(_check())


def test_legacy_player_migration_applies_once_futbin_columns_exist(dsn):
    """The flip side: on a database that DOES have the full FUTBIN-synced
    fut_players shape (auto_sync has run against it), migration 011 must
    still apply normally and build fair_value_mv - the fix must not
    disable the legacy pipeline outright, only gate it correctly."""
    _legacy_futbin_fut_players(dsn)

    ran = _run(rm.run(dsn, target="player"))
    assert ran >= 1

    async def _check():
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval(
                "SELECT to_regclass('public.fair_value_mv')"
            ) is not None
        finally:
            await conn.close()

    _run(_check())


def test_boot_does_not_hide_a_genuine_migration_failure(dsn):
    """run() (not run_on_boot()) must still raise/propagate a real syntax
    or constraint error instead of swallowing it - only requires-table/
    requires-column gated waits are meant to be silent. Simulates a
    genuinely broken migration by pointing the runner at a temp
    migrations dir containing one bad file."""
    import pathlib
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bad = pathlib.Path(tmp) / "001_broken.sql"
        bad.write_text("-- target: core\nSELECT this is not valid sql;\n")
        original_dir = rm.MIGRATIONS_DIR
        rm.MIGRATIONS_DIR = pathlib.Path(tmp)
        try:
            with pytest.raises(Exception):
                _run(rm.run(dsn, target="core"))
        finally:
            rm.MIGRATIONS_DIR = original_dir


def test_boot_swallows_a_target_specific_failure_but_logs_it_and_others_proceed(dsn, caplog):
    """run_on_boot() (the actual code path main.py's lifespan calls) must
    still boot the app even when one target's migrations genuinely fail -
    the existing "log loudly, don't crash boot" contract - while the other
    two targets keep making progress independently. Simulated with an
    unreachable player DSN, which is exactly the class of genuine failure
    (as opposed to a clean requires-* skip) the log line must not hide."""
    import logging

    _restored_prod_fut_players(dsn)
    _futgg_tables(dsn)

    unreachable_player_dsn = "postgresql://postgres:postgres@localhost:1/nope"

    with caplog.at_level(logging.ERROR, logger="migrations"):
        _run(rm.run_on_boot(dsn, unreachable_player_dsn, dsn))

    assert any("BOOT MIGRATIONS FAILED (player)" in r.message for r in caplog.records)

    async def _check_others_still_worked():
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval(
                "SELECT to_regclass('public.watchlist')"
            ) is not None
            assert await conn.fetchval(
                "SELECT to_regclass('public.futgg_market_snapshot')"
            ) is not None
        finally:
            await conn.close()

    _run(_check_others_still_worked())
