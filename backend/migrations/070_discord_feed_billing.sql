-- Migration 070: self-service Paid Partner feed billing (/feed command group)
-- target: core
--
-- Purely additive to discord_servers/discord_server_settings (migration
-- 069). No existing column, row, or behavior changes - every new column
-- is nullable or has a safe default, and existing admin-managed servers
-- (access_level='official' or admin-registered 'paid_partner' rows) are
-- unaffected since they never populate these columns.
--
-- One guild <-> one discord_servers row <-> at most one Stripe
-- subscription: stripe_subscription_id is UNIQUE, so a subscription
-- cannot be attached to a second guild, and re-running /feed setup
-- before payment UPSERTs the same row (guild_id is already the unique
-- key on discord_servers) rather than creating a duplicate.

ALTER TABLE discord_servers
    ADD COLUMN IF NOT EXISTS owner_discord_id TEXT,
    ADD COLUMN IF NOT EXISTS plan TEXT
        CHECK (plan IN ('server_feed', 'server_feed_plus')),
    ADD COLUMN IF NOT EXISTS platform_scope TEXT
        CHECK (platform_scope IN ('console', 'pc', 'both')),
    ADD COLUMN IF NOT EXISTS budget_mix TEXT
        CHECK (budget_mix IN ('balanced', 'mostly_low', 'mostly_medium', 'mostly_high')),
    ADD COLUMN IF NOT EXISTS budget_low INTEGER,
    ADD COLUMN IF NOT EXISTS budget_medium INTEGER,
    ADD COLUMN IF NOT EXISTS budget_high INTEGER,
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_checkout_session_id TEXT,
    ADD COLUMN IF NOT EXISTS subscription_status TEXT,
    ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS test_post_confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS bot_removed_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS discord_servers_stripe_subscription_uidx
    ON discord_servers (stripe_subscription_id)
    WHERE stripe_subscription_id IS NOT NULL;

-- Per-band daily counters live on discord_server_posts.payload (JSONB,
-- already there since migration 069) as payload->>'budget_band' -
-- indexed here so /feed status's "posts used today" and the per-band
-- posting cap in publish_live_opportunities aren't a sequential scan.
CREATE INDEX IF NOT EXISTS discord_server_posts_budget_band_idx
    ON discord_server_posts (server_id, kind, (payload->>'budget_band'), sent_at)
    WHERE status = 'sent';

COMMENT ON COLUMN discord_servers.subscription_status IS
    'unpaid (setup saved, no Stripe object yet) | checkout_pending (session created) | Stripe subscription status (active/trialing/past_due/canceled) once one exists.';
COMMENT ON COLUMN discord_servers.bot_removed_at IS
    'Set reactively when a Discord API call indicates the bot is no longer in this guild (this REST-only integration has no gateway to receive GUILD_DELETE) - pauses posting without touching billing.';
