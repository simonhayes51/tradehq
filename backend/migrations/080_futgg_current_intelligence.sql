-- Migration 080: one current FUT.GG intelligence row per card.
-- target: core
--
-- This is deliberately separate from futgg_recommendation_snapshots.
-- recommendation_snapshots is an immutable historical audit corpus used by
-- Track Record/outcome grading and therefore only records actual calls worth
-- grading. This table is mutable current state: every scanner evaluation,
-- including HOLD/SELL/AVOID/INSUFFICIENT_DATA, replaces the prior row.
--
-- That separation lets bulk product surfaces read the latest market-wide
-- intelligence without polluting the historical recommendation record.

CREATE TABLE IF NOT EXISTS futgg_current_intelligence (
    source_card_id                BIGINT PRIMARY KEY,
    evaluated_at                  TIMESTAMPTZ NOT NULL,
    current_bin                   BIGINT,
    signal                        TEXT NOT NULL,
    status                        TEXT NOT NULL,
    fair_value                    NUMERIC,
    theoretical_max_buy           NUMERIC,
    recommended_buy_max           NUMERIC,
    current_executable_buy        NUMERIC,
    break_even_price              NUMERIC,
    recommended_sell_target       NUMERIC,
    buy_below                     NUMERIC,
    expected_profit_after_tax     NUMERIC,
    expected_roi                  NUMERIC,
    confidence_score              DOUBLE PRECISION,
    liquidity_score               DOUBLE PRECISION,
    risk_level                    TEXT,
    trend_state                   TEXT,
    valuation_source              TEXT,
    evidence_quality              TEXT,
    evidence_age_minutes          INTEGER,
    price_age_minutes             INTEGER,
    sales_sample_size             INTEGER,
    sales_window_span_minutes     DOUBLE PRECISION,
    reason_codes                  TEXT[] NOT NULL DEFAULT '{}',
    blocking_codes                TEXT[] NOT NULL DEFAULT '{}',
    reasons                       JSONB NOT NULL DEFAULT '[]'::jsonb,
    trend_features                JSONB NOT NULL DEFAULT '{}'::jsonb,
    engine_version                TEXT,
    trend_version                 TEXT,
    expires_at                    TIMESTAMPTZ,
    evaluated_bin                 BIGINT,
    bin_sample_count              INTEGER,
    bin_history_span_hours        DOUBLE PRECISION,
    bin_reference_price           NUMERIC,
    bin_trend_direction           TEXT,
    bin_volatility                DOUBLE PRECISION,
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS futgg_current_intelligence_signal_idx
    ON futgg_current_intelligence (signal, evaluated_at DESC);

CREATE INDEX IF NOT EXISTS futgg_current_intelligence_trend_idx
    ON futgg_current_intelligence (trend_state, evaluated_at DESC);

CREATE INDEX IF NOT EXISTS futgg_current_intelligence_evaluated_idx
    ON futgg_current_intelligence (evaluated_at DESC);
