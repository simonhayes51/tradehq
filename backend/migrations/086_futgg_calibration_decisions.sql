-- Migration 086: durable audit trail for outcome-backed engine calibration.
-- target: core
--
-- This table NEVER changes live engine configuration. It records the evidence
-- and human decision around a proposed/accepted/rejected/rolled-back threshold
-- change so a future engine version can be explained and audited.

CREATE TABLE IF NOT EXISTS futgg_calibration_decisions (
    id                    BIGSERIAL PRIMARY KEY,
    decided_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    parameter             TEXT NOT NULL,
    from_engine_version   TEXT NOT NULL,
    to_engine_version     TEXT,
    old_value             JSONB,
    new_value             JSONB,
    horizon               TEXT CHECK (horizon IS NULL OR horizon IN ('24h','48h','7d')),
    window_days           INTEGER CHECK (window_days IS NULL OR window_days > 0),
    lower_bucket          TEXT,
    higher_bucket         TEXT,
    lower_sample          INTEGER CHECK (lower_sample IS NULL OR lower_sample >= 0),
    higher_sample         INTEGER CHECK (higher_sample IS NULL OR higher_sample >= 0),
    profitable_delta_pp   NUMERIC,
    p_value               NUMERIC,
    adjusted_alpha        NUMERIC,
    config_fingerprint    TEXT,
    rationale             TEXT NOT NULL,
    evidence              JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                TEXT NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed','accepted','rejected','rolled_back')),
    source_commit         TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS futgg_calibration_decisions_parameter_idx
    ON futgg_calibration_decisions (parameter, decided_at DESC);

CREATE INDEX IF NOT EXISTS futgg_calibration_decisions_version_idx
    ON futgg_calibration_decisions (from_engine_version, decided_at DESC);

CREATE INDEX IF NOT EXISTS futgg_calibration_decisions_status_idx
    ON futgg_calibration_decisions (status, decided_at DESC);

COMMENT ON TABLE futgg_calibration_decisions IS
    'Human calibration decisions and the outcome evidence behind them. This table is audit-only and never mutates the live engine.';
