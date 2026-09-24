-- Migration 100: first-party FUT.GG SBC solutions and FC27 Gallery data.
-- target: player
-- requires-table: market_events
--
-- Forward-only enrichment of the existing market-event schema. The old
-- EasySBC rows remain readable, while new source='futgg' rows can carry
-- platform-specific solution costs and the exact players in each squad.

ALTER TABLE sbc_details
    ADD COLUMN IF NOT EXISTS total_cost_pc BIGINT,
    ADD COLUMN IF NOT EXISTS repeat_count INTEGER,
    ADD COLUMN IF NOT EXISTS repeat_refresh_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS image_url TEXT,
    ADD COLUMN IF NOT EXISTS futgg_url TEXT,
    ADD COLUMN IF NOT EXISTS detail_scraped_at TIMESTAMPTZ;

ALTER TABLE sbc_challenges
    ADD COLUMN IF NOT EXISTS external_challenge_id INTEGER,
    ADD COLUMN IF NOT EXISTS estimated_cost_pc BIGINT,
    ADD COLUMN IF NOT EXISTS image_url TEXT,
    ADD COLUMN IF NOT EXISTS reward JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS solution_console_uuid TEXT,
    ADD COLUMN IF NOT EXISTS solution_pc_uuid TEXT,
    ADD COLUMN IF NOT EXISTS solution_console_url TEXT,
    ADD COLUMN IF NOT EXISTS solution_pc_url TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS sbc_challenges_event_external_uidx
    ON sbc_challenges (event_id, external_challenge_id)
    WHERE external_challenge_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sbc_solution_items (
    challenge_id        BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,
    platform            TEXT NOT NULL CHECK (platform IN ('console', 'pc')),
    squad_uuid          TEXT NOT NULL,
    position_idx        SMALLINT NOT NULL,
    player_ea_id        BIGINT NOT NULL,
    source_card_id      BIGINT REFERENCES futgg_players(source_card_id) ON DELETE SET NULL,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (challenge_id, platform, position_idx)
);
CREATE INDEX IF NOT EXISTS sbc_solution_items_player_idx
    ON sbc_solution_items (player_ea_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS futgg_gallery_tags (
    tag_id              INTEGER PRIMARY KEY,
    game_year           SMALLINT NOT NULL DEFAULT 27,
    name                TEXT NOT NULL,
    colour              TEXT,
    description         TEXT,
    priority            INTEGER,
    payload             JSONB NOT NULL DEFAULT '{}',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS futgg_gallery_sets (
    set_id              INTEGER PRIMARY KEY,
    game_year           SMALLINT NOT NULL DEFAULT 27,
    name                TEXT NOT NULL,
    category_name       TEXT,
    category_slug       TEXT,
    item_count          INTEGER,
    best_grade          TEXT,
    total_cost_coins    BIGINT,
    total_score         BIGINT,
    max_tokens          INTEGER,
    status              TEXT,
    payload             JSONB NOT NULL DEFAULT '{}',
    solution_payload    JSONB NOT NULL DEFAULT '{}',
    source_updated_at   TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS futgg_gallery_sets_category_idx
    ON futgg_gallery_sets (category_slug, total_cost_coins, name);

CREATE TABLE IF NOT EXISTS futgg_gallery_solution_items (
    set_id              INTEGER NOT NULL REFERENCES futgg_gallery_sets(set_id) ON DELETE CASCADE,
    grade               TEXT NOT NULL DEFAULT '',
    position_idx        SMALLINT NOT NULL,
    card_ea_id          BIGINT,
    player_ea_id        BIGINT,
    source_card_id      BIGINT REFERENCES futgg_players(source_card_id) ON DELETE SET NULL,
    price               BIGINT,
    score               INTEGER,
    overall             SMALLINT,
    club_ea_id          INTEGER,
    nation_ea_id        INTEGER,
    rarity_ea_id        INTEGER,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (set_id, grade, position_idx)
);
CREATE INDEX IF NOT EXISTS futgg_gallery_solution_player_idx
    ON futgg_gallery_solution_items (player_ea_id, captured_at DESC);
