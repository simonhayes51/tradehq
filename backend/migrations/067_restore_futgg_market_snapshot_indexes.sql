-- Migration 067: restore futgg_market_snapshot's indexes
-- target: core
-- requires-table: futgg_market_snapshot
--
-- INCIDENT: on 2026-08-22, futgg_market_snapshot had only 1 of its 9
-- expected indexes, and fair_value_mv had only 2 of its 4. Root cause,
-- confirmed via `SELECT schemaname, tablename, indexname FROM pg_indexes
-- WHERE indexname LIKE 'idx_futgg_market_snapshot%' OR indexname LIKE
-- 'idx_fair_value_mv%'` in production: two orphaned tables,
-- futgg_market_snapshot_legacy and fair_value_mv_legacy, were already
-- holding most of these exact index names. Postgres index names are
-- unique per SCHEMA, not per table, so every `CREATE INDEX IF NOT
-- EXISTS idx_x ON futgg_market_snapshot (...)` in migration 065 silently
-- no-op'd against the _legacy table's same-named index instead of
-- creating a real one on futgg_market_snapshot - Postgres correctly
-- reported "already exists", just not on the table anyone expected.
-- This is also why the earlier half of this migration (before this
-- fix), which only re-ran the same CREATE INDEX IF NOT EXISTS
-- statements, did not actually fix anything: it hit the identical
-- collision.
--
-- Where the _legacy tables came from is still not explained by anything
-- in this repo (grepped migrations, backend and auto_sync - nothing
-- renames futgg_market_snapshot/fair_value_mv aside). Whatever renamed
-- them, they are inert now: nothing in this codebase reads
-- *_legacy, so freeing their colliding index names is safe. This
-- migration renames ONLY an index that (a) has one of the expected
-- names and (b) is NOT already attached to the real table - i.e. it
-- only ever touches a genuine orphan, never a correctly-placed index,
-- and is a no-op on a fresh database where no _legacy tables exist.
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename NOT IN ('futgg_market_snapshot', 'fair_value_mv')
          AND indexname IN (
              'idx_futgg_market_snapshot_card', 'idx_futgg_market_snapshot_rating',
              'idx_futgg_market_snapshot_position', 'idx_futgg_market_snapshot_rarity',
              'idx_futgg_market_snapshot_club', 'idx_futgg_market_snapshot_league',
              'idx_futgg_market_snapshot_nation', 'idx_futgg_market_snapshot_current_bin',
              'idx_futgg_market_snapshot_tradeable', 'idx_futgg_market_snapshot_name_lower',
              'idx_fair_value_mv_card', 'idx_fair_value_mv_discount',
              'idx_fair_value_mv_suspect', 'idx_fair_value_mv_falling'
          )
    LOOP
        EXECUTE format('ALTER INDEX %I RENAME TO %I', rec.indexname, rec.indexname || '_orphan_bak');
    END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_futgg_market_snapshot_card
    ON futgg_market_snapshot (source_card_id);

CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_rating
    ON futgg_market_snapshot (rating);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_position
    ON futgg_market_snapshot (primary_position);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_rarity
    ON futgg_market_snapshot (rarity);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_club
    ON futgg_market_snapshot (club);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_league
    ON futgg_market_snapshot (league);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_nation
    ON futgg_market_snapshot (nation);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_current_bin
    ON futgg_market_snapshot (current_bin);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_tradeable
    ON futgg_market_snapshot (is_tradeable) WHERE is_tradeable IS NOT FALSE;
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_name_lower
    ON futgg_market_snapshot (lower(name));

-- Same incident class could affect fair_value_mv - cheap to check and
-- restore defensively rather than wait for its own outage.
CREATE UNIQUE INDEX IF NOT EXISTS idx_fair_value_mv_card
    ON fair_value_mv (card_id);
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_discount
    ON fair_value_mv (discount_pct DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_suspect
    ON fair_value_mv (data_quality_suspect) WHERE data_quality_suspect;
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_falling
    ON fair_value_mv (trend_falling) WHERE trend_falling;
