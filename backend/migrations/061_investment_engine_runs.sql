-- Migration 061: Investment Engine — recompute run observability
-- target: core
--
-- One row per (strategy, cohort) recompute pass, so a run can be
-- debugged after the fact without grepping logs - "premium_meta went
-- CRASHING -> WATCH with 0 signals created because live_confirmation was
-- insufficient" needs to be answerable from the database at 3am during
-- an FC27 launch, not only from whatever happened to still be in the
-- log buffer. See app/services/investment_engine/runner.py.

CREATE TABLE IF NOT EXISTS investment_engine_runs (
    id                    BIGSERIAL PRIMARY KEY,
    game_year             SMALLINT NOT NULL,
    strategy_key          TEXT NOT NULL,
    cohort_key            TEXT NOT NULL,

    previous_state        TEXT,
    new_state             TEXT NOT NULL,

    eligible_card_count   INTEGER,
    candidate_count       INTEGER,
    signals_created       INTEGER NOT NULL DEFAULT 0,
    signals_closed        INTEGER NOT NULL DEFAULT 0,
    signals_suppressed    INTEGER NOT NULL DEFAULT 0,
    -- {"stale_price": 4, "insufficient_liquidity": 12, ...}
    suppression_reasons   JSONB NOT NULL DEFAULT '{}'::jsonb,

    duration_ms           INTEGER,
    error                 TEXT,

    run_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS investment_engine_runs_lookup_idx
    ON investment_engine_runs (strategy_key, cohort_key, run_at DESC);
CREATE INDEX IF NOT EXISTS investment_engine_runs_errors_idx
    ON investment_engine_runs (run_at DESC) WHERE error IS NOT NULL;

COMMENT ON TABLE investment_engine_runs IS
    'Observability log for the Investment Engine recompute loop - one row per (strategy, cohort) pass, independent of investment_signals so a quiet/no-op pass is still visible.';
