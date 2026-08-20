-- Migration 056: production Discord integration foundation
-- target: core

CREATE TABLE IF NOT EXISTS discord_role_sync_queue (
    user_id TEXT PRIMARY KEY,
    discord_user_id TEXT NOT NULL,
    desired_member_role BOOLEAN NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'synced', 'not_joined', 'unavailable', 'unconfigured')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS discord_role_sync_pending_idx
    ON discord_role_sync_queue (next_attempt_at, updated_at)
    WHERE status <> 'synced';

CREATE TABLE IF NOT EXISTS discord_publications (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('opportunity_digest', 'proof_outcome', 'market_pulse')),
    source_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    UNIQUE (kind, source_key)
);

CREATE INDEX IF NOT EXISTS discord_publications_recent_idx
    ON discord_publications (kind, created_at DESC);

CREATE TABLE IF NOT EXISTS discord_integration_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    installed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO discord_integration_state (singleton)
VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;

ALTER TABLE stripe_events ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;
ALTER TABLE stripe_events ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ;
ALTER TABLE stripe_events ADD COLUMN IF NOT EXISTS processing_error TEXT;

-- Preserve historical payment rows while making the Stripe identifier safe
-- for webhook upserts. The newest row retains the identifier.
WITH ranked_payments AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY stripe_payment_intent_id
               ORDER BY created_at DESC NULLS LAST, id DESC
           ) AS occurrence
    FROM payments
    WHERE stripe_payment_intent_id IS NOT NULL
)
UPDATE payments p
SET stripe_payment_intent_id = NULL
FROM ranked_payments r
WHERE p.id = r.id AND r.occurrence > 1;

CREATE UNIQUE INDEX IF NOT EXISTS payments_stripe_payment_intent_uniq
    ON payments (stripe_payment_intent_id)
    WHERE stripe_payment_intent_id IS NOT NULL;
