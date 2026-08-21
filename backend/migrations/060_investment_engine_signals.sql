-- Migration 060: Investment Engine — investment signals (the Investment Ledger)
-- target: core
-- requires-table: futgg_players
--
-- WHY
-- ---
-- This is the Investment Engine's equivalent of futgg_recommendation_
-- snapshots + futgg_recommendation_outcomes (migration 040) - a frozen
-- assertion at open time, graded later against what actually happened -
-- but kept as a single lifecycle table rather than split snapshot/
-- outcome tables, per the explicit product spec's field list ("Investment
-- Ledger... closed investment signals should retain: original entry, max
-- buy, actual/reference exit, hold time, after-tax ROI, result, strategy,
-- reason it opened, reason it closed"). entry_price/max_buy/target_price/
-- rationale are never recomputed once written; only status/closed_at/
-- close_price/outcome/exit_reason are ever updated, exactly once, when a
-- signal closes.
--
-- shadow=true means this signal was calculated but never shown to a
-- user (see INVESTMENT_ENGINE_PUBLISH_SIGNALS in
-- app/services/investment_engine/config.py) - shadow signals are graded
-- exactly like published ones, so FC27 launch predictions can be
-- compared against reality before anything is exposed publicly.
--
-- The partial unique index makes recomputes idempotent for free: a card
-- that already has an active signal under a strategy is a silent
-- ON CONFLICT DO NOTHING on the next pass, never a duplicate open
-- position.

CREATE TABLE IF NOT EXISTS investment_signals (
    id                      BIGSERIAL PRIMARY KEY,
    game_year               SMALLINT NOT NULL,
    source_card_id          BIGINT NOT NULL REFERENCES futgg_players(source_card_id),
    strategy_key            TEXT NOT NULL,
    cohort_key              TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    shadow                  BOOLEAN NOT NULL DEFAULT TRUE,

    -- ---- What the engine concluded at open time (frozen) ---------------
    entry_price              NUMERIC NOT NULL,
    max_buy                   NUMERIC NOT NULL,
    target_price               NUMERIC NOT NULL,
    expected_roi_pct            NUMERIC,
    expected_hold_hours_min      NUMERIC,
    expected_hold_hours_max      NUMERIC,
    confidence                    NUMERIC,
    liquidity                      TEXT CHECK (liquidity IN ('low', 'medium', 'high')),
    investability                   TEXT CHECK (investability IN ('limited', 'investable')),
    max_position_cards               INTEGER,

    rationale                         JSONB NOT NULL DEFAULT '[]'::jsonb,
    risk_flags                         JSONB NOT NULL DEFAULT '[]'::jsonb,
    market_state_id                     BIGINT REFERENCES investment_market_states(id),

    -- ---- Lifecycle -------------------------------------------------------
    opened_at                            TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at                            TIMESTAMPTZ NOT NULL,
    max_hold_at                            TIMESTAMPTZ NOT NULL,

    closed_at                               TIMESTAMPTZ,
    close_price                              NUMERIC,
    -- target_hit | profitable_unrealised | flat | loss_unrealised |
    -- regime_exit | max_hold_reached | invalidated
    outcome                                   TEXT,
    after_tax_profit                           NUMERIC,
    after_tax_roi_pct                           NUMERIC,
    exit_reason                                  TEXT,

    engine_version                                TEXT NOT NULL,
    created_at                                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent recompute: one active signal per (card, strategy) at a time.
CREATE UNIQUE INDEX IF NOT EXISTS investment_signals_active_uniq
    ON investment_signals (source_card_id, strategy_key)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS investment_signals_active_lookup_idx
    ON investment_signals (status, game_year, strategy_key, cohort_key)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS investment_signals_card_idx
    ON investment_signals (source_card_id, status);
CREATE INDEX IF NOT EXISTS investment_signals_opened_idx
    ON investment_signals (opened_at DESC);
CREATE INDEX IF NOT EXISTS investment_signals_closed_idx
    ON investment_signals (closed_at DESC) WHERE status = 'closed';
CREATE INDEX IF NOT EXISTS investment_signals_max_hold_idx
    ON investment_signals (max_hold_at) WHERE status = 'active';

COMMENT ON TABLE investment_signals IS
    'Investment Ledger: one row per investment signal, open through close. Entry fields are frozen at open time and never recomputed; only lifecycle fields update on close.';
