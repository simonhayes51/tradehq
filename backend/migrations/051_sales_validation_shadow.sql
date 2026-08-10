-- Migration 051: shadow-mode audit tables for the BIN-first market engine
-- target: core
-- requires-table: futgg_players, futgg_bin_history, futgg_sales_history
--
-- Purely additive. Nothing in this migration changes how any worker
-- behaves; both tables are written only when
-- BIN_FIRST_MARKET_ENGINE_ENABLED is on, and read only by
-- scripts/bin_first_shadow_report.py. futgg_sales_sync.py's own
-- scheduling columns (futgg_players.next_sales_due_at,
-- last_sales_status), its throttle and its circuit breaker are all
-- untouched.
--
-- WHY THESE TABLES EXIST
-- ----------------------
-- The claim being tested is that 90-95% of expensive sales requests can
-- be removed. That claim cannot be settled by argument, only by recording
-- - for every card, on every pass - what the gate WOULD have decided while
-- the existing worker carried on unchanged, and then comparing.
--
-- sales_validation_decisions answers "would we have paid for this card,
-- and why / why not". sales_validation_outcomes answers the harder and
-- more important question: when we did pay, was the BIN hypothesis right?
-- Without the second table the system could happily report a 95%
-- reduction while quietly having stopped finding anything.

CREATE TABLE IF NOT EXISTS sales_validation_decisions (
    id                     BIGSERIAL PRIMARY KEY,
    card_id                BIGINT NOT NULL,
    decided_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- What the gate concluded.
    eligible               BOOLEAN NOT NULL,
    reason                 TEXT,
    skip_reason            TEXT,
    urgency                INT,
    card_class             TEXT NOT NULL,

    -- What the existing rating-band schedule would have done at the same
    -- moment. The whole savings estimate is the difference between this
    -- and `eligible`, so it is recorded per row rather than re-derived.
    legacy_would_scrape    BOOLEAN NOT NULL,

    -- BIN-derived state at decision time, kept so a decision can be
    -- re-explained months later without replaying history.
    market_state           TEXT NOT NULL,
    current_bin            INTEGER,
    bin_baseline           NUMERIC,
    anomaly_score          NUMERIC,
    price_persistence      NUMERIC,
    volatility_24h         NUMERIC,
    observation_count_24h  INT,
    thin_market            BOOLEAN NOT NULL DEFAULT FALSE,

    evidence_age_minutes   INT,
    max_evidence_age_minutes INT,

    engine_version         TEXT NOT NULL,
    gate_version           TEXT NOT NULL,
    notes                  TEXT[] NOT NULL DEFAULT '{}'
);

-- The report groups by decided_at window, then by reason/skip_reason.
CREATE INDEX IF NOT EXISTS sales_validation_decisions_time_idx
    ON sales_validation_decisions (decided_at DESC);
CREATE INDEX IF NOT EXISTS sales_validation_decisions_card_idx
    ON sales_validation_decisions (card_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS sales_validation_decisions_reason_idx
    ON sales_validation_decisions (reason, decided_at DESC);
-- Partial: the savings headline counts exactly these rows, and they are a
-- small minority of a 10k-card-per-pass table.
CREATE INDEX IF NOT EXISTS sales_validation_decisions_eligible_idx
    ON sales_validation_decisions (decided_at DESC) WHERE eligible;

COMMENT ON TABLE sales_validation_decisions IS
    'Shadow record of what the BIN-first eligibility gate would have decided for each card, alongside what the existing rating-band schedule would have done. Written only when BIN_FIRST_MARKET_ENGINE_ENABLED=true; never changes worker behaviour.';
COMMENT ON COLUMN sales_validation_decisions.legacy_would_scrape IS
    'Whether futgg_sales_sync.py''s existing next_sales_due_at schedule would have scraped this card at this moment. The savings estimate is the difference between this and eligible.';


CREATE TABLE IF NOT EXISTS sales_validation_outcomes (
    id                BIGSERIAL PRIMARY KEY,
    card_id           BIGINT NOT NULL,
    decided_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    reason            TEXT NOT NULL,
    -- SALES_CONFIRMED_UNDERCUT | SALES_CONFIRMED_SPIKE |
    -- BIN_BASELINE_LAGGING | SALES_AGREE_WITH_BIN |
    -- INSUFFICIENT_TRANSACTION_EVIDENCE
    outcome           TEXT NOT NULL,
    -- BIN_DERIVED | SALES_CONFIRMED | HIGH_CONFIDENCE. Explicit because a
    -- BIN is an asking price and a sale is a transaction, and the two must
    -- never silently merge into one "price".
    evidence_type     TEXT NOT NULL,

    bin_baseline      NUMERIC,
    current_bin       INTEGER,
    sales_median      NUMERIC,
    sales_count       INT NOT NULL DEFAULT 0,

    notes             TEXT[] NOT NULL DEFAULT '{}',
    UNIQUE (card_id, decided_at)
);

CREATE INDEX IF NOT EXISTS sales_validation_outcomes_time_idx
    ON sales_validation_outcomes (decided_at DESC);
CREATE INDEX IF NOT EXISTS sales_validation_outcomes_reason_idx
    ON sales_validation_outcomes (reason, outcome);

COMMENT ON TABLE sales_validation_outcomes IS
    'Did completed-sales evidence support the BIN-derived hypothesis that justified spending a sales request? This is the measurement that distinguishes a genuine cost reduction from having quietly stopped finding opportunities.';
