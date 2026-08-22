-- Migration 067: restore futgg_market_snapshot's indexes
-- target: core
-- requires-table: futgg_market_snapshot
--
-- INCIDENT: on 2026-08-22, futgg_market_snapshot had ZERO indexes -
-- confirmed via `SELECT indexname, indexdef FROM pg_indexes WHERE
-- tablename = 'futgg_market_snapshot'` returning 0 rows in production,
-- despite migration 065 creating all 9 of them (including the unique
-- index REFRESH MATERIALIZED VIEW CONCURRENTLY requires) in the same
-- transaction that populated fair_value_mv - and fair_value_mv was
-- confirmed populated (16,040 rows), which can only happen if that
-- transaction committed, meaning the indexes committed too.
--
-- No code path in this repo drops these indexes outside of migrations
-- 038/041/047/050/052/065 themselves (grepped the whole repo, including
-- auto_sync), and none of those would re-run against an already-recorded
-- (target, filename) row. The mechanism that removed them after a
-- confirmed-committed transaction is NOT understood from code alone -
-- this migration restores the missing state rather than explaining it.
-- The immediate symptom was every REFRESH MATERIALIZED VIEW CONCURRENTLY
-- futgg_market_snapshot attempt failing ("cannot refresh ... concurrently
-- / create a unique index with no WHERE clause"), which froze the
-- snapshot and made most of the app read stale-filtered, empty results.
--
-- All CREATE INDEX IF NOT EXISTS - a pure no-op wherever an index
-- already survives, so this is safe to apply regardless of which ones
-- were actually missing on any given database.

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
