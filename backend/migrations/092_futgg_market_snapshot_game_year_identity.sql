-- Migration 092: fix futgg_market_snapshot's unique index to match
-- futgg_players' real (game_year, source_card_id) identity.
-- target: core
-- requires-table: futgg_market_snapshot
-- requires-column: futgg_market_snapshot.game_year
--
-- Migration 091 moved futgg_players' primary key to (game_year,
-- source_card_id) specifically so an FC27 card could be inserted with
-- the same source_card_id as an FC26 card without overwriting it. But
-- futgg_market_snapshot's own unique index (idx_futgg_market_snapshot_card,
-- most recently recreated by migration 085) was never updated to match -
-- it is still just (source_card_id).
--
-- The moment a card exists under the same source_card_id in both
-- FC26 and FC27, the view legitimately contains two rows with that
-- source_card_id (one per game_year), which violates this index.
-- REFRESH MATERIALIZED VIEW (concurrent or plain - a unique index
-- violation is a data problem, not a locking-mode problem) then fails
-- on every attempt with a duplicate-key error, which is not one of the
-- specific exceptions refresher_loop backs off on - it falls into the
-- broad catch-and-log in market_data_provider.refresher_loop, so the
-- snapshot silently stops advancing from that point on with no visible
-- outage. New FC27 cards exist in futgg_players and never reach search,
-- opportunities, or anywhere else that reads futgg_market_snapshot.
--
-- Fix: rebuild the index on the same composite key the source table
-- actually uses.

DROP INDEX IF EXISTS idx_futgg_market_snapshot_card;

CREATE UNIQUE INDEX IF NOT EXISTS idx_futgg_market_snapshot_card
    ON futgg_market_snapshot (game_year, source_card_id);

CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_source_card_id
    ON futgg_market_snapshot (source_card_id);
