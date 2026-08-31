-- Migration 076: AI Chat usage accounting
-- target: core
--
-- The assistant had no accounting at all: response.usage came back from
-- the provider on every call and was discarded. The daily allowance was
-- therefore a guess, and there was no way to check the guess afterwards.
--
-- What is recorded is what is needed to answer "what is this costing us,
-- and who is using it" and nothing else. Deliberately NOT stored:
--   * no message text, in either direction
--   * no tool arguments and no tool results
--   * no Discord identity, no email address
--   * no IP address, no user agent
-- user_id is the internal id. A row here says how much a conversation
-- cost, never what it was about.
--
-- Token counts are the provider's own reported usage, not an estimate
-- from character counts: an estimate that drifts from the invoice is
-- worse than no number, because it looks authoritative.
--
-- Reversible: one new table and its indexes, nothing else touched.
-- `DROP TABLE ai_usage_events;` restores the previous schema exactly.

CREATE TABLE IF NOT EXISTS ai_usage_events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT        NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model           TEXT        NOT NULL,
    -- 'ok'      the customer got an answer
    -- 'failed'  the provider errored; tokens may still have been spent
    -- 'blocked' refused before any model call (limit, kill switch) - the
    --           row exists so refusals are visible, and costs nothing
    status          TEXT        NOT NULL,
    input_tokens    INTEGER     NOT NULL DEFAULT 0,
    output_tokens   INTEGER     NOT NULL DEFAULT 0,
    total_tokens    INTEGER     NOT NULL DEFAULT 0,
    -- One CUSTOMER message can be several model calls: the tool loop
    -- calls again with each tool result in context. Without this the
    -- per-message cost looks like a third of what it is.
    model_calls     SMALLINT    NOT NULL DEFAULT 0,
    -- Computed at write time from the rates then in force, so a later
    -- price change does not silently rewrite history.
    estimated_cost  NUMERIC(12, 6) NOT NULL DEFAULT 0,
    cost_currency   TEXT        NOT NULL DEFAULT 'USD',
    CONSTRAINT ai_usage_events_status_check
        CHECK (status IN ('ok', 'failed', 'blocked'))
);

-- "What did this account spend this day / this month", which is what both
-- limiters and the admin summary ask.
CREATE INDEX IF NOT EXISTS ai_usage_events_user_time_idx
    ON ai_usage_events (user_id, occurred_at DESC);
-- "What did the whole product spend over this period", for the summary.
CREATE INDEX IF NOT EXISTS ai_usage_events_time_idx
    ON ai_usage_events (occurred_at DESC);
