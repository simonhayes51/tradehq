-- Multi-server EAFC.Live leak feed destinations.
-- One destination per guild. Server admins manage it with /leaks.

CREATE TABLE IF NOT EXISTS discord_leak_feed_destinations (
    guild_id TEXT PRIMARY KEY,
    guild_name TEXT,
    channel_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    configured_by_discord_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_discord_leak_feed_destinations_enabled
    ON discord_leak_feed_destinations (enabled)
    WHERE enabled = TRUE;
