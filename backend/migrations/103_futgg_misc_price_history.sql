-- Migration 103: FUT.GG manager and consumable price history
-- target: core
--
-- Stores the small misc-prices feed independently from player BIN history.
-- Prices are nullable: NULL plus status='no_listings' means FUT.GG checked
-- the item and found no active market; it must never be coerced to zero.

CREATE TABLE IF NOT EXISTS futgg_misc_price_history (
    id BIGSERIAL PRIMARY KEY,
    game_year SMALLINT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('pc', 'ps5')),
    item_type TEXT NOT NULL CHECK (
        item_type IN ('manager', 'manager_league', 'chemistry_style')
    ),
    item_id BIGINT NOT NULL,
    price INTEGER CHECK (price IS NULL OR price >= 0),
    status TEXT NOT NULL,
    price_updated_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS futgg_misc_price_history_latest_idx
    ON futgg_misc_price_history
       (game_year, platform, item_type, item_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS futgg_misc_price_history_retention_idx
    ON futgg_misc_price_history (game_year, captured_at, id);
