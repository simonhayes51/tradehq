-- Queue leak fanout through the main backend Discord worker.
-- The leak monitor only needs DATABASE_URL/X credentials; the main backend
-- already owns DISCORD_BOT_TOKEN and performs the actual Discord sends.

CREATE TABLE IF NOT EXISTS discord_leak_feed_deliveries (
    tweet_id TEXT NOT NULL REFERENCES leak_tweets(tweet_id) ON DELETE CASCADE,
    guild_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tweet_id, guild_id)
);

CREATE INDEX IF NOT EXISTS idx_discord_leak_feed_deliveries_pending
    ON discord_leak_feed_deliveries (status, next_attempt_at, created_at)
    WHERE status IN ('pending','retrying');
