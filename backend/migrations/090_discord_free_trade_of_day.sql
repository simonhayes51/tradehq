-- Migration 090: one canonical Free Server Feed trade per content day.
-- target: core
--
-- Every free Discord server must receive the SAME daily opportunity. Storing the
-- frozen choice here means an API restart halfway through a broadcast cannot pick
-- a different card for the remaining servers.

CREATE TABLE IF NOT EXISTS discord_free_trade_of_day (
    trade_date DATE PRIMARY KEY,
    source_card_id BIGINT NOT NULL,
    payload JSONB NOT NULL,
    embed JSONB NOT NULL,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS discord_free_trade_of_day_selected_idx
    ON discord_free_trade_of_day (selected_at DESC);

COMMENT ON TABLE discord_free_trade_of_day IS
    'Frozen once-per-content-day opportunity broadcast to every Free Discord Server Feed. Paid feeds remain server-specific.';
