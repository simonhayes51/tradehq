-- Migration 059: capture FUT.GG's current listing-tile GG Rating
-- target: core
-- requires-table: futgg_players

ALTER TABLE futgg_players
    ADD COLUMN IF NOT EXISTS gg_rating NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS gg_rating_updated_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'futgg_players_gg_rating_range'
          AND conrelid = 'futgg_players'::regclass
    ) THEN
        ALTER TABLE futgg_players
            ADD CONSTRAINT futgg_players_gg_rating_range
            CHECK (gg_rating IS NULL OR gg_rating BETWEEN 0 AND 100);
    END IF;
END $$;

COMMENT ON COLUMN futgg_players.gg_rating IS
    'Current GG Rating most recently observed on a FUT.GG listing tile; not historical point-in-time data.';
COMMENT ON COLUMN futgg_players.gg_rating_updated_at IS
    'When a valid GG Rating was last successfully observed from FUT.GG, whether or not its value changed.';

CREATE TABLE IF NOT EXISTS futgg_gg_rating_backfill_state (
    game_year SMALLINT PRIMARY KEY,
    next_page INTEGER NOT NULL DEFAULT 1 CHECK (next_page >= 1),
    pages_processed INTEGER NOT NULL DEFAULT 0,
    cards_seen BIGINT NOT NULL DEFAULT 0,
    ratings_found BIGINT NOT NULL DEFAULT 0,
    rows_matched BIGINT NOT NULL DEFAULT 0,
    rows_updated BIGINT NOT NULL DEFAULT 0,
    missing_ratings BIGINT NOT NULL DEFAULT 0,
    invalid_ratings BIGINT NOT NULL DEFAULT 0,
    failures BIGINT NOT NULL DEFAULT 0,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE futgg_gg_rating_backfill_state IS
    'Durable page checkpoint and counters for the listing-only FUT.GG GG Rating backfill.';
