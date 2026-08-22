-- Migration 066: repair the content-release tables after a name collision
-- target: core
-- requires-table: futgg_players
--
-- WHY THIS EXISTS
-- ---------------
-- Migration 064 originally named its tables `market_events` /
-- `market_event_cards`. `market_events` has existed since migration 018
-- with an entirely different schema (kind / starts_at / fingerprint /
-- ends_at) and is depended on by migrations 019/024/046/049,
-- app/services/market_events.py, auto_sync/promo_event_detector.py and
-- both SBC sync writers.
--
-- Because 064 used CREATE TABLE IF NOT EXISTS, the collision produced NO
-- error: the statement silently did nothing, the pre-existing table was
-- left untouched (so nothing was corrupted), and the mistake only
-- surfaced much later as
--
--     UndefinedColumnError: column "game_year" of relation
--     "market_events" does not exist
--
-- at INSERT time. 064 has since been corrected to use collision-free
-- names, but a database that already ran the original version needs
-- repairing, hence this migration.
--
-- Note the failure mode was loud-but-late rather than destructive: the
-- schemas differed enough that every write failed outright. Had they
-- been closer, this could instead have written derived promo rows into a
-- table the SBC and priority systems read from.
--
-- SAFETY: this migration NEVER touches `market_events` itself. It only
-- drops `market_event_cards`, which 064 created, which nothing else in
-- the repo references, and whose foreign key points at the wrong table.
-- Confirmed before writing: no migration, service or worker outside
-- 064 mentions market_event_cards.

-- The stray child table from the original 064. It is either empty (the
-- reconstruction never completed a write) or contains rows keyed to the
-- wrong parent, so dropping is correct in both cases.
DROP TABLE IF EXISTS market_event_cards;

-- Create the correctly-named tables for databases whose 064 was recorded
-- as applied before the rename. Bodies are identical to the corrected
-- 064; on a fresh database 064 creates them first and these are no-ops.
CREATE TABLE IF NOT EXISTS content_release_events (
    id                  BIGSERIAL PRIMARY KEY,
    game_year           SMALLINT NOT NULL,
    event_type          TEXT NOT NULL,
    event_key           TEXT NOT NULL,
    title               TEXT,
    rarity              TEXT,
    squad               TEXT,
    released_at         TIMESTAMPTZ NOT NULL,
    card_count          INTEGER NOT NULL DEFAULT 0,
    event_scale         TEXT NOT NULL CHECK (event_scale IN ('broad', 'mini', 'isolated')),
    source              TEXT NOT NULL DEFAULT 'derived_futgg_release',
    confidence          TEXT NOT NULL DEFAULT 'medium'
                         CHECK (confidence IN ('low', 'medium', 'high')),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS content_release_events_identity_uniq
    ON content_release_events (game_year, event_key, released_at);
CREATE INDEX IF NOT EXISTS content_release_events_timeline_idx
    ON content_release_events (game_year, released_at DESC);
CREATE INDEX IF NOT EXISTS content_release_events_type_idx
    ON content_release_events (game_year, event_type, released_at DESC);

CREATE TABLE IF NOT EXISTS content_release_event_cards (
    event_id            BIGINT NOT NULL REFERENCES content_release_events(id) ON DELETE CASCADE,
    source_card_id      BIGINT NOT NULL,
    source_player_id    BIGINT,
    PRIMARY KEY (event_id, source_card_id)
);

CREATE INDEX IF NOT EXISTS content_release_event_cards_card_idx
    ON content_release_event_cards (source_card_id);
