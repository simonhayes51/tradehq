-- Migration: Partner referral codes
-- Adds first-class partner attribution while preserving existing user referral codes.

ALTER TABLE referral_codes
    ALTER COLUMN user_id DROP NOT NULL;

ALTER TABLE referral_codes
    ADD COLUMN IF NOT EXISTS code_type TEXT NOT NULL DEFAULT 'user';

ALTER TABLE referral_codes
    ADD COLUMN IF NOT EXISTS partner_name TEXT;

ALTER TABLE referral_codes
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE referral_codes
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'referral_codes_code_type_check'
    ) THEN
        ALTER TABLE referral_codes
            ADD CONSTRAINT referral_codes_code_type_check
            CHECK (code_type IN ('user', 'partner'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_referral_codes_type
    ON referral_codes(code_type);

CREATE INDEX IF NOT EXISTS idx_referral_codes_active
    ON referral_codes(is_active)
    WHERE is_active = TRUE;

-- FutProfit launch partner.
-- No user_id is required because this code belongs to a partner/server,
-- not to an individual EAFC.Live account.
INSERT INTO referral_codes (
    user_id,
    code,
    code_type,
    partner_name,
    is_active,
    created_at,
    updated_at
)
VALUES (
    NULL,
    'FUTPROFIT',
    'partner',
    'FutProfit | FC27 Trading',
    TRUE,
    NOW(),
    NOW()
)
ON CONFLICT (code) DO UPDATE
SET code_type = 'partner',
    partner_name = EXCLUDED.partner_name,
    is_active = TRUE,
    updated_at = NOW();
