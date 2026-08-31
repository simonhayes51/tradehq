-- Migration 078: record of the periodic Discord role reconciliation
-- target: core
--
-- The queue-based sync (migration 056) is event-driven: a billing change
-- enqueues a role change and the worker retries it. That is correct until
-- a queued job is lost - a prolonged Discord outage that exhausts the
-- retries, a row deleted, a deploy at the wrong moment - after which the
-- account stays wrong forever, because nothing will ever look at it
-- again. The queue has no way to notice it is out of step.
--
-- The sweep compares stored entitlement against actual Discord role state
-- for every linked account, so a lost job self-heals on the next pass.
-- This table is the record of those passes: enough to answer "is the
-- sweep running, and is it working" without reading logs.
--
-- Deliberately NOT stored: bot tokens, Discord API responses, and any
-- error text that could carry a credential. `failure_reason` is a short
-- code chosen from a fixed set in app/services/discord_reconcile.py.
--
-- Reversible: one new table, nothing else touched.
-- `DROP TABLE discord_reconciliation_runs;` restores the previous schema.

CREATE TABLE IF NOT EXISTS discord_reconciliation_runs (
    id               BIGSERIAL PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    -- 'running' | 'completed' | 'failed'
    status           TEXT        NOT NULL DEFAULT 'running',
    dry_run          BOOLEAN     NOT NULL DEFAULT FALSE,
    accounts_checked INTEGER     NOT NULL DEFAULT 0,
    roles_granted    INTEGER     NOT NULL DEFAULT 0,
    roles_revoked    INTEGER     NOT NULL DEFAULT 0,
    already_correct  INTEGER     NOT NULL DEFAULT 0,
    skipped          INTEGER     NOT NULL DEFAULT 0,
    failed           INTEGER     NOT NULL DEFAULT 0,
    -- A short code, never an exception message. See _FAILURE_REASONS.
    failure_reason   TEXT,
    CONSTRAINT discord_reconciliation_runs_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

-- "When did a full sweep last succeed", which is the question the admin
-- summary and the health check both ask.
CREATE INDEX IF NOT EXISTS discord_reconciliation_runs_finished_idx
    ON discord_reconciliation_runs (finished_at DESC NULLS LAST);
