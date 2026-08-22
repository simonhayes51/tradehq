-- Migration 059: Investment Engine — cohort/regime market-state time series
-- target: core
--
-- One row per (game_year, strategy_key, cohort_key) recompute pass. This
-- is the engine's own time series - the running-peak/drawdown/momentum/
-- deceleration math all reads back the LAST FEW rows for a given cohort+
-- strategy here rather than re-deriving cohort price history from raw
-- futgg_bin_history on every pass. That keeps the hot path bounded
-- (LIMIT N rows off an indexed (strategy_key, cohort_key, computed_at)
-- lookup) instead of repeatedly scanning bin history across an entire
-- cohort's cards every few minutes - see backend/INVESTMENT_ENGINE.md's
-- performance section.
--
-- state / previous_state use the engine's regime state machine (see
-- app/services/investment_engine/state_machine.py):
--   NORMAL -> OVERHEATED -> CRASHING -> WATCH -> BOTTOMING ->
--   INVESTMENT_WINDOW -> REBOUND -> EXIT -> CLOSED
--
-- fc26_prior / fc26_prior_weight record how much the FC26 research prior
-- influenced THIS specific decision (see app/services/investment_engine/
-- drawdown.py) - the weight is expected to fall as a game's own FC27
-- live history accumulates, per the "FC26 prior vs FC27 reality"
-- requirement. Storing it per-row means a later analysis can ask "was
-- the engine still leaning on FC26 when it made this call" without
-- guessing from computed_at alone.

CREATE TABLE IF NOT EXISTS investment_market_states (
    id                                  BIGSERIAL PRIMARY KEY,
    game_year                           SMALLINT NOT NULL,
    strategy_key                        TEXT NOT NULL,
    cohort_key                          TEXT NOT NULL,

    state                               TEXT NOT NULL,
    previous_state                      TEXT,
    transition_reason                   TEXT,

    -- ---- Price / drawdown --------------------------------------------
    cohort_price_index                  NUMERIC,
    running_peak                        NUMERIC,
    reference_price                     NUMERIC,
    drawdown_pct                        NUMERIC,
    drawdown_percentile_current_cycle   NUMERIC,  -- percentile of today's drawdown vs this cycle's own history so far
    historical_reference_percentile     NUMERIC,  -- where drawdown sits vs the FC26 prior reference range
    severity_score                      NUMERIC,
    severity_class                      TEXT CHECK (severity_class IN ('normal', 'elevated', 'severe', 'extreme')),

    -- ---- Momentum / deceleration ---------------------------------------
    momentum_current                    NUMERIC,
    momentum_previous                   NUMERIC,
    deceleration_count                  INTEGER NOT NULL DEFAULT 0,
    deceleration_strength                NUMERIC,

    -- ---- Breadth --------------------------------------------------------
    pct_rising                          NUMERIC,
    pct_falling                         NUMERIC,
    breadth_spread                      NUMERIC,

    -- ---- FC27 live confirmation + FC26 prior weighting -------------------
    live_confirmation                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    fc26_prior                          JSONB NOT NULL DEFAULT '{}'::jsonb,
    fc26_prior_weight                   NUMERIC,

    confidence                          NUMERIC,
    eligible_card_count                 INTEGER,
    candidate_count                     INTEGER,

    engine_version                      TEXT NOT NULL,
    computed_at                         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS investment_market_states_series_idx
    ON investment_market_states (game_year, strategy_key, cohort_key, computed_at DESC);
CREATE INDEX IF NOT EXISTS investment_market_states_state_idx
    ON investment_market_states (state, computed_at DESC);

COMMENT ON TABLE investment_market_states IS
    'Time series of Investment Engine cohort/regime snapshots - the engine''s own bounded history for drawdown/momentum/deceleration, read back instead of re-scanning raw price history each pass.';
