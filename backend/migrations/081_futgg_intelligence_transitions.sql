-- Migration 081: meaningful changes in the current FUT.GG intelligence state.
-- target: core
--
-- One row is written only when a card already had current intelligence and a
-- later scanner evaluation changes signal or status. Initial population is not
-- a transition; otherwise the first deployment would fabricate ~16k "changes".

CREATE TABLE IF NOT EXISTS futgg_intelligence_transitions (
    id                 BIGSERIAL PRIMARY KEY,
    source_card_id     BIGINT NOT NULL,
    changed_at         TIMESTAMPTZ NOT NULL,
    previous_signal    TEXT,
    new_signal         TEXT NOT NULL,
    previous_status    TEXT,
    new_status         TEXT NOT NULL,
    current_bin        BIGINT,
    fair_value         NUMERIC,
    expected_roi       NUMERIC,
    confidence_score   DOUBLE PRECISION,
    risk_level         TEXT,
    trend_state        TEXT,
    reason_codes       TEXT[] NOT NULL DEFAULT '{}',
    blocking_codes     TEXT[] NOT NULL DEFAULT '{}',
    engine_version     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS futgg_intelligence_transitions_card_idx
    ON futgg_intelligence_transitions (source_card_id, changed_at DESC);

CREATE INDEX IF NOT EXISTS futgg_intelligence_transitions_recent_idx
    ON futgg_intelligence_transitions (changed_at DESC);

CREATE INDEX IF NOT EXISTS futgg_intelligence_transitions_signal_idx
    ON futgg_intelligence_transitions (new_signal, changed_at DESC);
