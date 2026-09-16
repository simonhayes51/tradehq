-- Migration 093: add platform to futgg_bin_history/futgg_sales_history
-- target: core
-- requires-table: futgg_bin_history, futgg_sales_history, futgg_players, fut_players
--
-- FUT.GG tracks PC and console as genuinely separate markets (confirmed
-- live: the site's own "Select platform" switcher, and its bulk price
-- feed is literally a different file per platform -
-- r2.fut.gg/<year>/player-prices-<platform>-dyn.v1.<hash>.json). Both
-- futgg_bin_history and futgg_sales_history were built with no platform
-- concept at all - one row per (source_card_id, captured_at), full
-- stop - because only one platform (ps5, the default for
-- FUTGG_BULK_PLATFORM/FUTGG_SALES_PLATFORM) was ever ingested.
--
-- Running a second worker instance against FUTGG_BULK_PLATFORM=pc /
-- FUTGG_SALES_PLATFORM=pc without this column would write PC
-- observations into the exact same rows the ps5 worker writes,
-- indistinguishable from each other - every "latest price" read
-- (futgg_market_snapshot's latest_bin/bounded_sales CTEs,
-- select_rows_to_write's change-only-write dedup) would non-
-- deterministically mix ps5 and pc prices depending on which worker's
-- cycle happened to write last.
--
-- Adding the column defaulting to 'ps5' is non-disruptive on its own:
-- every existing row is correctly labelled (there has only ever been
-- one platform's data in these tables), and every read path that does
-- not yet filter on platform keeps working exactly as before until
-- migration 094 (the view rebuild) makes that filter explicit.

-- Metadata-only on Postgres 11+ (a constant DEFAULT does not rewrite
-- existing rows), so safe to run through the normal transactional
-- migration runner even against these tables' current live size.
ALTER TABLE futgg_bin_history
    ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'ps5';

ALTER TABLE futgg_sales_history
    ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'ps5';

-- The new (source_card_id, platform, <timestamp>) indexes these tables
-- need are NOT created here. Both tables are under continuous write
-- load from live PriceSync/SalesSync (same situation migration 053
-- documented), so a plain CREATE INDEX would hold a write-blocking lock
-- for the full build - run these manually, CONCURRENTLY, once, directly
-- against production, same as 053's own precedent:
--
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_futgg_bin_history_card_platform_captured
--       ON futgg_bin_history (source_card_id, platform, captured_at DESC);
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_futgg_sales_history_card_platform_sold
--       ON futgg_sales_history (source_card_id, platform, approximate_sold_at DESC);
--
-- Until those exist, latest_bin/bounded_sales still work correctly
-- (platform is a plain filter, not required for correctness) - they
-- just fall back to the existing (source_card_id, captured_at DESC) /
-- (source_card_id, approximate_sold_at DESC) indexes from migration 053
-- and filter platform without one of its own, which is slower under
-- load but not wrong. Add the platform-aware indexes as soon as a
-- maintenance window allows.
