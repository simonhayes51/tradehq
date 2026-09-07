-- Migration 087: read-only shadow observations for information-value sales scheduling.
-- target: core
--
-- This records what an information-value selector WOULD have prioritised after
-- the existing live select_due() chose its normal batch. It does not change the
-- live sales scheduler, consume trigger queues, or cause extra FUT.GG requests.

CREATE TABLE IF NOT EXISTS futgg_trigger_shadow_observations (
    id                    BIGSERIAL PRIMARY KEY,
    observed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_count             INTEGER NOT NULL DEFAULT 0,
    triggered_count       INTEGER NOT NULL DEFAULT 0,
    triggered_new_count   INTEGER NOT NULL DEFAULT 0,
    user_driven_count     INTEGER NOT NULL DEFAULT 0,
    held_position_count   INTEGER NOT NULL DEFAULT 0,
    due_card_ids          BIGINT[] NOT NULL DEFAULT '{}',
    triggered_card_ids    BIGINT[] NOT NULL DEFAULT '{}',
    reason_counts         JSONB NOT NULL DEFAULT '{}'::jsonb,
    selector_version      TEXT NOT NULL,
    notes                 JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS futgg_trigger_shadow_observations_time_idx
    ON futgg_trigger_shadow_observations (observed_at DESC);

COMMENT ON TABLE futgg_trigger_shadow_observations IS
    'Read-only comparison of live tier-based sales selection vs information-value candidates. Never implies those candidates were fetched.';
