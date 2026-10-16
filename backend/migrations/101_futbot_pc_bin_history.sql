-- Migration 101: isolate PC BIN observations for FutBot
-- target: core
-- requires-table: futgg_players
--
-- TradeHQ's existing market analytics interpret futgg_bin_history as the
-- console market. Keep FUT.GG's platform-specific PC feed in a separate
-- table so enabling PC pricing for FutBot cannot change console signals.

CREATE TABLE IF NOT EXISTS futbot_pc_bin_history (
    id BIGSERIAL PRIMARY KEY,
    source_card_id BIGINT NOT NULL,
    lowest_bin INTEGER NOT NULL,
    price_range_low INTEGER,
    price_range_high INTEGER,
    source_age_text TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    platform TEXT NOT NULL DEFAULT 'pc' CHECK (platform = 'pc'),
    game_year SMALLINT NOT NULL
);

CREATE INDEX IF NOT EXISTS futbot_pc_bin_history_card_year_captured_idx
    ON futbot_pc_bin_history (game_year, source_card_id, captured_at DESC);
