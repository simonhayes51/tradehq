"""
Ordered SQL migration runner — the deploy-time replacement for the pile of
CREATE TABLE IF NOT EXISTS statements that used to run inside the app's
lifespan on every boot (review issue C5).

Two ways to run it:

  1. Automatically at app boot (default; for deploys with no terminal
     access, e.g. Railway) - main.py's lifespan calls run() for the core
     and player targets unless RUN_MIGRATIONS_ON_BOOT=0. Guarded by a pg
     advisory lock so concurrent instances can't race, and a failure is
     logged loudly but does NOT crash the boot (every consumer of the new
     tables degrades gracefully until the migration succeeds).

  2. Manually / as a Railway pre-deploy command:
         python scripts/run_migrations.py                 # core DB
         python scripts/run_migrations.py --player        # player DB
         python scripts/run_migrations.py --dsn <url>

Rules:
  - Files in migrations/ run in lexicographic order, once each, recorded
    in schema_migrations, keyed by (target, filename) - see
    _ensure_schema_migrations_table for why filename alone stopped being a
    safe identity once single-DB deploys point core/player/watchlist at the
    same database (and therefore the same schema_migrations table).
  - A file may declare its target DB in a header comment
    ("-- target: player"); the default target is core. A run only executes
    files matching its target.
  - A file may declare tables it depends on but doesn't create itself via
    "-- requires-table: sales_history, bin_history" - typically tables
    owned by another service (e.g. auto_sync). The runner skips the file
    cleanly (not recorded, retried next run) instead of surfacing a raw
    "relation does not exist".
  - Same idea for individual columns owned by another service on a table
    this repo does create, via "-- requires-column: fut_players.name" -
    e.g. fut_players is bootstrapped here (migration 017) but its FUTBIN
    metadata columns (name/rating/version/position/image_url) are only
    ever written by the auto_sync repo's futbin_full_sync.py. A restored
    or FUT.GG-only database can have fut_players without ever having had
    that sync run against it.
  - LEGACY_BASELINE files (001-009 + the 2025 events migration) predate
    this runner and were applied by hand in pgAdmin per the docs. They are
    recorded as applied WITHOUT being executed, because re-running them on
    a live DB would fail (they aren't idempotent). Pass --execute-legacy
    on a genuinely fresh database to actually run them.
  - A failing migration aborts the run (later files don't apply).
"""
import argparse
import asyncio
import logging
import os
import pathlib
import re
import sys

import asyncpg

log = logging.getLogger("migrations")

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
ADVISORY_LOCK_KEY = 7741003  # distinct from candle-aggregator + fair-value locks

_TARGET_RE = re.compile(r"--\s*target:\s*(core|player|watchlist)", re.IGNORECASE)
_REQUIRES_RE = re.compile(r"--\s*requires-table:\s*([^\n]+)", re.IGNORECASE)
_REQUIRES_COLUMN_RE = re.compile(r"--\s*requires-column:\s*([^\n]+)", re.IGNORECASE)

# Applied manually before this runner existed - baselined, never re-executed.
LEGACY_BASELINE = frozenset(
    {
        "001_subscription_enhancements.sql",
        "001_trade_finder.sql",
        "002_monetization_features.sql",
        "003_social_trading_feed.sql",
        "004_social_feed_post_updates.sql",
        "005_single_tier_subscriptions.sql",
        "006_multiple_images.sql",
        "006_trader_payment_accounts.sql",
        "007_currency.sql",
        "008_post_tips_payment_verification.sql",
        "009_drop_cut_features.sql",
        "20250901_events_watchlist.sql",
    }
)


def _file_target(sql: str) -> str:
    m = _TARGET_RE.search(sql[:2000])
    return m.group(1).lower() if m else "core"


def _file_requires_tables(sql: str) -> list[str]:
    """Tables a migration's header declares as prerequisites it doesn't
    create itself - e.g. sales_history/bin_history, which auto_sync's
    ensure_tables() creates on its own first run, not any migration here.
    Lets the runner skip cleanly instead of throwing a raw Postgres error
    if the backend boots before that other service has ever run against
    this database."""
    m = _REQUIRES_RE.search(sql[:2000])
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def _file_requires_columns(sql: str) -> list[tuple[str, str]]:
    """Table.column pairs a migration's header declares as prerequisites it
    doesn't create itself - e.g. fut_players.name/rating/version/position/
    image_url, which are only ever written by the auto_sync repo's
    futbin_full_sync.py (INSERTs/ALTERs fut_players, never CREATEs it - see
    migration 017's own comment). A database can have fut_players with just
    the columns this repo's migrations put there (card_id, card art, rarity,
    ...) and nothing from that external sync - lets the runner skip cleanly
    instead of throwing a raw Postgres 'column does not exist' error."""
    m = _REQUIRES_COLUMN_RE.search(sql[:2000])
    if not m:
        return []
    pairs = []
    for item in m.group(1).split(","):
        item = item.strip()
        if not item:
            continue
        table, _, column = item.partition(".")
        if table and column:
            pairs.append((table.strip(), column.strip()))
    return pairs


async def _missing_tables(conn: asyncpg.Connection, names: list[str]) -> list[str]:
    if not names:
        return []
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name = ANY($1::text[])",
        names,
    )
    present = {r["table_name"] for r in rows}
    return [n for n in names if n not in present]


async def _missing_columns(conn: asyncpg.Connection, pairs: list[tuple[str, str]]) -> list[str]:
    if not pairs:
        return []
    missing = []
    for table, column in pairs:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
            )
            """,
            table, column,
        )
        if not exists:
            missing.append(f"{table}.{column}")
    return missing


async def _ensure_schema_migrations_table(conn: asyncpg.Connection) -> None:
    """Bootstraps schema_migrations, upgrading it in place from the legacy
    filename-only-PK shape to a composite (target, filename) PK.

    filename-only was fine when core/player/watchlist each had their own
    DSN and therefore their own schema_migrations table - one table per
    target made the filename unique by construction. A single-DB deploy
    (DATABASE_URL == PLAYER_DATABASE_URL == WATCHLIST_DATABASE_URL) points
    all three at the same table, so filename alone stops being a safe
    identity: a filename recorded as applied for one target would read as
    already-applied for every other target sharing that row's DB, and
    "INSERT ... ON CONFLICT (filename) DO NOTHING" would silently drop the
    second target's own record of having applied its own, differently-named
    migration of the same basename. (target, filename) is what's actually
    unique. Forward-only: upgrades an existing legacy table's structure in
    place, never touches migration content or already-applied history.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            target     TEXT NOT NULL DEFAULT 'core',
            filename   TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (target, filename)
        )
        """
    )
    has_target_column = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'schema_migrations'
              AND column_name = 'target'
        )
        """
    )
    if has_target_column:
        return

    # Legacy table predating target-awareness - filename was the sole PK.
    # Add target, backfill each already-applied row from that migration
    # file's own "-- target:" header (the same source of truth the runner
    # itself uses to pick which files to run), then repoint the PK.
    await conn.execute("ALTER TABLE schema_migrations ADD COLUMN target TEXT")
    rows = await conn.fetch("SELECT filename FROM schema_migrations")
    for r in rows:
        path = MIGRATIONS_DIR / r["filename"]
        file_target = _file_target(path.read_text()) if path.exists() else "core"
        await conn.execute(
            "UPDATE schema_migrations SET target = $1 WHERE filename = $2",
            file_target, r["filename"],
        )
    await conn.execute("ALTER TABLE schema_migrations ALTER COLUMN target SET NOT NULL")
    await conn.execute("ALTER TABLE schema_migrations DROP CONSTRAINT schema_migrations_pkey")
    await conn.execute("ALTER TABLE schema_migrations ADD PRIMARY KEY (target, filename)")
    log.info("upgraded schema_migrations to (target, filename) primary key")


async def run(dsn: str, target: str = "core", execute_legacy: bool = False) -> int:
    """Apply pending migrations for `target` against `dsn`.
    Returns the number of migrations executed (baselines not counted)."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_KEY)
        try:
            await _ensure_schema_migrations_table(conn)
            applied = {
                r["filename"]
                for r in await conn.fetch(
                    "SELECT filename FROM schema_migrations WHERE target = $1", target
                )
            }

            ran = 0
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in applied:
                    continue
                sql = path.read_text()
                if _file_target(sql) != target:
                    continue

                missing = await _missing_tables(conn, _file_requires_tables(sql))
                missing += await _missing_columns(conn, _file_requires_columns(sql))
                if missing:
                    # Not an error: this migration depends on tables owned
                    # by a different service (e.g. auto_sync's
                    # sales_history/bin_history, created by its own
                    # ensure_tables() on first run, not by any migration
                    # here). Skip cleanly and retry next boot instead of
                    # letting Postgres raise 'relation does not exist' -
                    # not recorded as applied, so it's picked up as soon as
                    # the prerequisite exists.
                    log.info(
                        "skipping %s for now - waiting on table(s) %s (owned by another service)",
                        path.name, ", ".join(missing),
                    )
                    continue

                if path.name in LEGACY_BASELINE and not execute_legacy:
                    await conn.execute(
                        "INSERT INTO schema_migrations (target, filename) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        target, path.name,
                    )
                    log.info("baselined (recorded, not executed): %s", path.name)
                    continue

                log.info("applying %s ...", path.name)
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (target, filename) VALUES ($1, $2)",
                        target, path.name,
                    )
                ran += 1
                log.info("applied %s", path.name)

            log.info(
                "migrations done (target=%s): %d executed, %d previously recorded",
                target, ran, len(applied),
            )
            return ran
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
    finally:
        await conn.close()


async def run_on_boot(core_dsn: str, player_dsn: str, watchlist_dsn: str | None = None) -> None:
    """Called from main.py's lifespan. Never raises - the app must still
    boot (and serve its degraded-but-working paths) if a migration fails.

    watchlist_dsn is optional and defaults to core_dsn (matching main.py's
    own "same pool if the DSNs match" pattern for WATCHLIST_DATABASE_URL) -
    "watchlist" has always been a valid --target this runner recognizes
    (see _TARGET_RE above), it just was never actually invoked here until
    migration 016 needed it."""
    try:
        await run(core_dsn, target="core")
    except Exception as e:
        log.error("BOOT MIGRATIONS FAILED (core): %s - app continuing", e)
    try:
        await run(player_dsn, target="player")
    except Exception as e:
        log.error("BOOT MIGRATIONS FAILED (player): %s - app continuing", e)
    try:
        await run(watchlist_dsn or core_dsn, target="watchlist")
    except Exception as e:
        log.error("BOOT MIGRATIONS FAILED (watchlist): %s - app continuing", e)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", action="store_true", help="run against PLAYER_DATABASE_URL")
    ap.add_argument("--dsn", help="explicit DSN (overrides env)")
    ap.add_argument(
        "--execute-legacy",
        action="store_true",
        help="actually execute the 001-009 legacy files (fresh databases only)",
    )
    args = ap.parse_args()

    dsn = args.dsn
    if not dsn:
        env = "PLAYER_DATABASE_URL" if args.player else "DATABASE_URL"
        dsn = os.getenv(env) or os.getenv("DATABASE_URL")
    if not dsn:
        print("No DSN - set DATABASE_URL (or pass --dsn).", file=sys.stderr)
        return 1

    target = "player" if args.player else "core"
    asyncio.run(run(dsn, target=target, execute_legacy=args.execute_legacy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
