-- Migration 093: ensure leak_context exists for installations where migration 090
-- was already recorded as applied before the column was added to that file.
-- target: core

ALTER TABLE futgg_current_intelligence
    ADD COLUMN IF NOT EXISTS leak_context JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS futgg_current_intelligence_leak_context_idx
    ON futgg_current_intelligence USING GIN (leak_context);
