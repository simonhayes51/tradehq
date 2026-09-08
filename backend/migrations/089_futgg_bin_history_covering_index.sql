-- @target core
-- Cover the scanner's hourly BIN valuation read without changing its evidence model.
-- Existing indexes stop at (source_card_id, captured_at); the valuation loader
-- also needs lowest_bin for every qualifying observation. INCLUDE allows index-only
-- scans when PostgreSQL's visibility map permits them while preserving the same
-- search key and all existing indexes.
CREATE INDEX IF NOT EXISTS idx_futgg_bin_history_card_captured_covering
    ON futgg_bin_history (source_card_id, captured_at DESC)
    INCLUDE (lowest_bin);
