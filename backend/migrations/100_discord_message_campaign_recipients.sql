-- Durable deduplication of opted-in Discord campaign deliveries.
CREATE TABLE IF NOT EXISTS discord_message_campaign_recipients (
    fingerprint text NOT NULL,
    discord_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (fingerprint, discord_id)
);
CREATE INDEX IF NOT EXISTS idx_discord_campaign_status
ON discord_message_campaign_recipients (fingerprint, status);
