-- Migration 069: multi-server Discord trading feed
-- target: core
--
-- Adds the schema for the multi-server Discord trading feed system
-- (Free daily overview / Paid Partner live feed / Official superset feed)
-- described in the trading-feed project. Purely additive - no existing
-- table, column, or row is altered or removed, and every new table starts
-- empty so nothing posts anywhere until an admin explicitly registers a
-- server via the new admin endpoints.
--
-- The pre-existing single-guild Official server (DISCORD_GUILD_ID /
-- DISCORD_OPPORTUNITY_CHANNEL_ID / DISCORD_PROOF_CHANNEL_ID env vars) is
-- seeded here as a discord_servers row so it is managed the same way as
-- every other server going forward; the env vars remain the fallback if
-- this table is empty (see app/services/discord_integration.py).

CREATE TABLE IF NOT EXISTS discord_servers (
    id BIGSERIAL PRIMARY KEY,
    guild_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    access_level TEXT NOT NULL
        CHECK (access_level IN ('free', 'paid_partner', 'official')),
    feed_channel_id TEXT,
    proof_channel_id TEXT,
    status TEXT NOT NULL DEFAULT 'paused'
        CHECK (status IN ('active', 'paused')),
    enabled_by_admin_id TEXT,
    enabled_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS discord_servers_status_idx
    ON discord_servers (status, access_level);

-- Per-server posting policy. One row per server, created alongside it.
CREATE TABLE IF NOT EXISTS discord_server_settings (
    server_id BIGINT PRIMARY KEY REFERENCES discord_servers(id) ON DELETE CASCADE,
    max_posts_per_day INTEGER NOT NULL DEFAULT 10 CHECK (max_posts_per_day > 0),
    min_confidence NUMERIC NOT NULL DEFAULT 0.6 CHECK (min_confidence BETWEEN 0 AND 1),
    min_expected_profit NUMERIC NOT NULL DEFAULT 1000 CHECK (min_expected_profit >= 0),
    cooldown_minutes INTEGER NOT NULL DEFAULT 15 CHECK (cooldown_minutes >= 0),
    platform TEXT NOT NULL DEFAULT 'ps' CHECK (platform IN ('ps', 'xbox', 'pc')),
    daily_overview_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    daily_overview_hour INTEGER NOT NULL DEFAULT 12 CHECK (daily_overview_hour BETWEEN 0 AND 23),
    daily_overview_timezone TEXT NOT NULL DEFAULT 'Europe/London',
    live_opportunities_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Every post attempt (live opportunity, opportunity update, daily overview,
-- product update, test post). Doubles as the dedup ledger (unique on
-- server_id + kind + dedup_key) and the admin "recent posts / failures"
-- view.
CREATE TABLE IF NOT EXISTS discord_server_posts (
    id BIGSERIAL PRIMARY KEY,
    server_id BIGINT NOT NULL REFERENCES discord_servers(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN (
        'daily_overview', 'opportunity', 'opportunity_update',
        'product_update', 'test'
    )),
    dedup_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'abandoned')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    discord_message_id TEXT,
    last_error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    UNIQUE (server_id, kind, dedup_key)
);

CREATE INDEX IF NOT EXISTS discord_server_posts_recent_idx
    ON discord_server_posts (server_id, created_at DESC);

CREATE INDEX IF NOT EXISTS discord_server_posts_daily_count_idx
    ON discord_server_posts (server_id, kind, sent_at)
    WHERE status = 'sent';

-- Every admin action against a server (register, channel change, access
-- change, allowance/expiry change, pause/resume, test post) for the audit
-- trail requirement.
CREATE TABLE IF NOT EXISTS discord_server_audit_log (
    id BIGSERIAL PRIMARY KEY,
    server_id BIGINT REFERENCES discord_servers(id) ON DELETE CASCADE,
    admin_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS discord_server_audit_log_server_idx
    ON discord_server_audit_log (server_id, created_at DESC);

-- The pre-existing Official server (DISCORD_GUILD_ID env var) is seeded as
-- a discord_servers row at application boot instead of here, because a SQL
-- migration has no access to the app's environment variables - see
-- app.services.discord_trading_feed.seed_official_server(), called once
-- from main.py's lifespan. It is a best-effort convenience seed, not a
-- hard dependency: discord_integration.py still falls back to the env
-- vars directly when this table has no matching row.
