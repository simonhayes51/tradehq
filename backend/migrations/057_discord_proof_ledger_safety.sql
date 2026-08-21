-- Migration 057: Discord Proof Ledger cutoff and outcome provenance
-- target: core
-- requires-table: discord_integration_state, discord_publications, futgg_recommendation_outcomes

ALTER TABLE discord_integration_state
    ADD COLUMN IF NOT EXISTS proof_enabled_at TIMESTAMPTZ;

-- Durable launch boundary. Existing installations receive the migration-057
-- application time; rerunning this migration cannot move the cutoff forward.
UPDATE discord_integration_state
SET proof_enabled_at = NOW()
WHERE singleton=TRUE AND proof_enabled_at IS NULL;

COMMENT ON COLUMN discord_integration_state.proof_enabled_at IS
    'Proof Ledger launch cutoff. Snapshot evaluated_at and outcome graded_at must both be at or after this timestamp.';

ALTER TABLE futgg_recommendation_outcomes
    ADD COLUMN IF NOT EXISTS outcome_price INTEGER,
    ADD COLUMN IF NOT EXISTS outcome_observed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS outcome_price_type TEXT;

-- Target exits were already persisted and can be copied exactly. Historical
-- unrealised marks cannot be reconstructed defensibly and remain NULL.
UPDATE futgg_recommendation_outcomes
SET outcome_price = realised_sell_price,
    outcome_observed_at = exit_at,
    outcome_price_type = 'target_exit'
WHERE target_hit=TRUE
  AND realised_sell_price IS NOT NULL
  AND exit_at IS NOT NULL
  AND outcome_price IS NULL;

ALTER TABLE discord_publications
    DROP CONSTRAINT IF EXISTS discord_publications_status_check;

ALTER TABLE discord_publications
    ADD CONSTRAINT discord_publications_status_check
    CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'abandoned'));

CREATE INDEX IF NOT EXISTS discord_proof_publication_cadence_idx
    ON discord_publications (COALESCE(sent_at, created_at) DESC)
    WHERE kind='proof_outcome' AND status IN ('sending', 'sent');
