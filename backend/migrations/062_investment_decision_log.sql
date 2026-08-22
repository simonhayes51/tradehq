-- Migration 062: Investment Engine — per-card decision log (FC27 learning dataset)
-- target: core
-- requires-table: investment_signals
--
-- WHY THIS TABLE EXISTS
-- ---------------------
-- investment_engine_runs (migration 061) records per-PASS aggregates:
-- how many candidates, how many suppressed, a JSON reason histogram.
-- That answers "what did the engine do" but not "what did the engine
-- decide about THIS card, and was that decision right".
--
-- The single most valuable thing FC26 could not tell us is whether the
-- engine's REFUSALS were good. A card that looked cheap while its cohort
-- was still crashing, which the engine declined to recommend, and which
-- then fell another 22% - that is evidence the engine worked, and
-- nothing in the schema before this row could reconstruct it.
--
-- So this table records BOTH recommended and suppressed decisions, with
-- the reason, at the moment they were made. Outcomes are graded onto the
-- same row later (see migration 063 / investment_outcomes.py) so a
-- decision and its consequence live together.
--
-- WRITE VOLUME DISCIPLINE
-- -----------------------
-- The engine recomputes every few minutes across thousands of cards. A
-- naive "log every card every pass" would write millions of near-identical
-- rows per day, which is both expensive and useless (the interesting
-- events are the CHANGES). Two mechanisms keep this bounded, both
-- enforced by the writer (investment_decision_log.py), not by hope:
--
--   1. decision_fingerprint - a hash of the decision-relevant fields
--      (decision, reason, cohort state, rounded price band). The partial
--      unique index below makes re-inserting an identical fingerprint for
--      the same card+strategy while the previous one is still "current" a
--      no-op via ON CONFLICT DO NOTHING.
--   2. superseded_at - when a card's decision genuinely changes, the old
--      row is closed out and a new one opened, so the table is a
--      change-log (one row per decision EPISODE), not a sampling log.
--
-- Net effect: a card whose situation is unchanged for six hours produces
-- ONE row, not seventy.

CREATE TABLE IF NOT EXISTS investment_decision_log (
    id                      BIGSERIAL PRIMARY KEY,
    game_year               SMALLINT NOT NULL,
    source_card_id          BIGINT NOT NULL,
    strategy_key            TEXT NOT NULL,
    cohort_key              TEXT NOT NULL,

    -- 'recommended' (a signal was opened) or 'suppressed' (deliberately
    -- not recommended). Both matter; see the table comment.
    decision                TEXT NOT NULL CHECK (decision IN ('recommended', 'suppressed')),
    -- For suppressed: one of readiness.ALL_SUPPRESSION_REASONS.
    -- For recommended: the state-machine transition reason.
    reason                  TEXT NOT NULL,

    -- ---- What the engine knew about the CARD at decision time --------
    reference_price         NUMERIC,
    card_drawdown_pct       NUMERIC,
    card_momentum_pct       NUMERIC,
    relative_strength_pct   NUMERIC,
    liquidity               TEXT,
    investability           TEXT,
    confidence              NUMERIC,

    -- ---- What the engine knew about the MARKET at decision time ------
    market_state            TEXT,
    market_severity_class   TEXT,
    market_drawdown_pct     NUMERIC,
    cohort_price_index      NUMERIC,

    -- Set only for decision = 'recommended'; lets a decision row be
    -- joined straight to the signal it produced.
    signal_id               BIGINT REFERENCES investment_signals(id) ON DELETE SET NULL,

    -- ---- Outcome, graded later (never at write time) -----------------
    -- NULL until the corresponding horizon has actually elapsed. A NULL
    -- here means "not yet known", never "no change" - see
    -- investment_outcomes.py's own no-hindsight discipline.
    price_after_6h          NUMERIC,
    price_after_12h         NUMERIC,
    price_after_24h         NUMERIC,
    price_after_48h         NUMERIC,
    price_after_72h         NUMERIC,
    roi_after_24h_pct       NUMERIC,   -- after-tax, via trading_math
    roi_after_72h_pct       NUMERIC,   -- after-tax, via trading_math
    max_adverse_pct         NUMERIC,
    max_favourable_pct      NUMERIC,
    outcome_graded_at       TIMESTAMPTZ,

    decision_fingerprint    TEXT NOT NULL,
    shadow                  BOOLEAN NOT NULL DEFAULT TRUE,
    engine_version          TEXT NOT NULL,
    decided_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at           TIMESTAMPTZ
);

-- Deduplication: at most one CURRENT (not-yet-superseded) row per
-- card+strategy+fingerprint. This is what makes the writer's
-- ON CONFLICT DO NOTHING collapse an unchanged decision into a no-op.
CREATE UNIQUE INDEX IF NOT EXISTS investment_decision_log_current_uniq
    ON investment_decision_log (source_card_id, strategy_key, decision_fingerprint)
    WHERE superseded_at IS NULL;

-- The grader's access pattern: find rows old enough to grade that have
-- not been graded yet.
CREATE INDEX IF NOT EXISTS investment_decision_log_grading_idx
    ON investment_decision_log (decided_at)
    WHERE outcome_graded_at IS NULL;

-- The analysis access pattern: "how did suppressed decisions for this
-- strategy/reason turn out".
CREATE INDEX IF NOT EXISTS investment_decision_log_analysis_idx
    ON investment_decision_log (game_year, strategy_key, decision, reason, decided_at DESC);

COMMENT ON TABLE investment_decision_log IS
    'Per-card Investment Engine decisions - BOTH recommended and suppressed - with outcomes graded on later. Deduplicated into decision episodes via decision_fingerprint/superseded_at so an unchanged decision does not write a row every pass.';
