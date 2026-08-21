-- Migration 058: one daily Discord Proof Ledger report
-- target: core
-- requires-table: discord_integration_state, discord_publications, futgg_recommendation_outcomes

ALTER TABLE discord_integration_state
    ADD COLUMN IF NOT EXISTS daily_proof_enabled_at TIMESTAMPTZ;

-- This timestamp is the immutable daily-report launch boundary. The worker
-- waits until it has a complete reporting period after this instant.
UPDATE discord_integration_state
SET daily_proof_enabled_at = NOW()
WHERE singleton=TRUE AND daily_proof_enabled_at IS NULL;

COMMENT ON COLUMN discord_integration_state.daily_proof_enabled_at IS
    'Daily Proof Ledger launch cutoff. Reports require a complete period after this timestamp.';

CREATE TABLE IF NOT EXISTS discord_daily_proof_publications (
    report_date DATE PRIMARY KEY,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    timezone_name TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('sending', 'retryable', 'sent', 'abandoned')),
    discord_nonce VARCHAR(25) NOT NULL UNIQUE,
    discord_message_id TEXT,
    payload JSONB NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_until TIMESTAMPTZ,
    last_error TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (period_end > period_start)
);

-- Retire every unsent item from the removed per-opportunity publisher.
UPDATE discord_publications
SET status='abandoned',
    last_error='individual_proof_publisher_retired'
WHERE kind='proof_outcome' AND status <> 'sent';
