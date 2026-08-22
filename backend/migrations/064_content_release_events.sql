-- Migration 064: content release timeline
-- target: core
-- requires-table: futgg_players
-- requires-column: futgg_players.released_at, futgg_players.squad
--
-- WHAT THIS IS
-- ------------
-- A timeline of GAME CONTENT RELEASES (promo teams, TOTW squads, and
-- later SBCs/Evolutions/rewards), reconstructed from FUT.GG's own card
-- release timestamps rather than guessed at.
--
-- NAMED content_release_events, NOT market_events: a `market_events`
-- table has existed since migration 018 with an entirely different
-- schema (kind/starts_at/fingerprint/ends_at) and is depended on by
-- migrations 019/024/046/049, app/services/market_events.py,
-- auto_sync/promo_event_detector.py and both SBC sync writers. Reusing
-- that name meant CREATE TABLE IF NOT EXISTS silently did nothing, which
-- surfaced only much later as an UndefinedColumnError at INSERT time.
--
-- Its first purpose is to let a research pass ask "how did the market
-- behave around this release" - but the table is deliberately NOT
-- promo-specific, so the same timeline can later carry SBC releases and
-- expiries, Evolution launches, reward cycles, pack changes, and promo
-- start/end markers without a schema rewrite.
--
-- Derived from released_at, NEVER from first_seen_at: first_seen_at only
-- records when our scraper noticed a card, which for historical FC26
-- cards can be weeks after the actual release and would produce a
-- fictional timeline.
--
-- GROUPING RULE
-- -------------
-- The grouping key is (game_year, squad, release CLUSTER), NOT rarity.
-- Rarity is the broad promo FAMILY ("Team of the Week", "Cornerstones");
-- squad is the specific release ("TOTW 27", "Cornerstones 1"). Many
-- distinct releases share one rarity - every weekly TOTW squad has
-- rarity "Team of the Week" - so grouping by rarity would collapse a
-- season of weekly releases into one meaningless mega-event.
--
-- Including released_at in the key also handles cards ADDED LATER to an
-- existing promo: they form their own event at their own timestamp
-- rather than being retroactively folded into the original drop, which
-- is what actually happened in the market.
--
-- card_count is what distinguishes a genuine multi-card promo team from
-- a mini-release or a one-off SBC/objective card that merely happens to
-- carry a squad label. Nothing here forces an isolated card into a
-- "promo team" it was never part of - see event_scale.

CREATE TABLE IF NOT EXISTS content_release_events (
    id                  BIGSERIAL PRIMARY KEY,
    game_year           SMALLINT NOT NULL,

    -- Deliberately open-ended (no CHECK constraint enumerating promo
    -- types): the whole point is that SBC/Evolution/reward events can be
    -- added later without a migration. Current derivation emits
    -- 'promo_release' and 'totw_release' only.
    event_type          TEXT NOT NULL,
    -- Stable human/machine key for this event, e.g. 'totw_27' or
    -- 'cornerstones_1'. Unique per (game_year, event_key, released_at) -
    -- see the index below for why released_at is part of that.
    event_key           TEXT NOT NULL,
    title               TEXT,

    rarity              TEXT,
    squad               TEXT,
    released_at         TIMESTAMPTZ NOT NULL,

    card_count          INTEGER NOT NULL DEFAULT 0,
    -- 'broad' (a full promo team), 'mini' (a small batch), 'isolated'
    -- (a single card - typically an SBC/objective release rather than a
    -- promo). Lets analysis exclude isolated cards without having to
    -- re-derive the distinction from card_count every time.
    event_scale         TEXT NOT NULL CHECK (event_scale IN ('broad', 'mini', 'isolated')),

    -- How this event was established. 'derived_futgg_release' = inferred
    -- from FUT.GG release timestamps by the reconstruction job. Leaves
    -- room for 'manual' or a future announcement feed.
    source              TEXT NOT NULL DEFAULT 'derived_futgg_release',
    -- Confidence in the event itself (not in any market effect). A broad
    -- multi-card release sharing one exact timestamp is high confidence;
    -- a single card with a squad label is low.
    confidence          TEXT NOT NULL DEFAULT 'medium'
                         CHECK (confidence IN ('low', 'medium', 'high')),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent re-derivation: re-running the reconstruction must update
-- the existing event rather than create a duplicate. released_at is part
-- of the key because the same squad name CAN legitimately appear at two
-- timestamps (a promo re-release, or cards added later to an existing
-- squad) and those are genuinely different events.
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

-- "Which events did this card belong to" - the direction the event-study
-- join runs when starting from a price series.
CREATE INDEX IF NOT EXISTS content_release_event_cards_card_idx
    ON content_release_event_cards (source_card_id);

COMMENT ON TABLE content_release_events IS
    'Content/market event timeline reconstructed from FUT.GG card release timestamps (futgg_players.released_at, never first_seen_at). Grouped by (game_year, squad, released_at) because rarity is the promo family while squad is the specific release. Intentionally not promo-specific: SBC/Evolution/reward events can be added later without schema changes.';
COMMENT ON COLUMN content_release_events.event_scale IS
    'broad = full promo team, mini = small batch, isolated = single card (usually SBC/objective, not a promo). Prevents isolated cards being analysed as though they were promo teams.';
