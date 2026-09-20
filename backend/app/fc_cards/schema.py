from __future__ import annotations

import asyncpg


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS fc_card_users (
    discord_user_id TEXT PRIMARY KEY,
    coins INTEGER NOT NULL DEFAULT 250,
    shards INTEGER NOT NULL DEFAULT 0,
    pack_streak INTEGER NOT NULL DEFAULT 0,
    last_pack_at TIMESTAMPTZ,
    last_pack_date DATE,
    last_weekly_pack_at TIMESTAMPTZ,
    last_weekly_pack_year INTEGER,
    last_weekly_pack_week INTEGER,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    current_streak INTEGER NOT NULL DEFAULT 0,
    best_streak INTEGER NOT NULL DEFAULT 0,
    streak_shield INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE fc_card_users ADD COLUMN IF NOT EXISTS last_weekly_pack_at TIMESTAMPTZ;
ALTER TABLE fc_card_users ADD COLUMN IF NOT EXISTS last_weekly_pack_year INTEGER;
ALTER TABLE fc_card_users ADD COLUMN IF NOT EXISTS last_weekly_pack_week INTEGER;
ALTER TABLE fc_card_users ADD COLUMN IF NOT EXISTS streak_shield INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS fc_card_inventory (
    discord_user_id TEXT NOT NULL REFERENCES fc_card_users(discord_user_id) ON DELETE CASCADE,
    card_id BIGINT NOT NULL,
    copies INTEGER NOT NULL DEFAULT 0 CHECK (copies >= 0),
    first_owned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (discord_user_id, card_id)
);
CREATE INDEX IF NOT EXISTS idx_fc_inventory_user_copies ON fc_card_inventory(discord_user_id, copies DESC);

CREATE TABLE IF NOT EXISTS fc_card_pack_openings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_user_id TEXT NOT NULL REFERENCES fc_card_users(discord_user_id) ON DELETE CASCADE,
    card_id BIGINT NOT NULL,
    source TEXT NOT NULL DEFAULT 'daily',
    streak INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fc_pack_openings_user_created ON fc_card_pack_openings(discord_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fc_card_guild_channels (
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS fc_card_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guild_id TEXT,
    channel_id TEXT,
    proposer_id TEXT NOT NULL REFERENCES fc_card_users(discord_user_id) ON DELETE CASCADE,
    recipient_id TEXT NOT NULL REFERENCES fc_card_users(discord_user_id) ON DELETE CASCADE,
    proposer_card_id BIGINT NOT NULL,
    recipient_card_id BIGINT,
    proposer_coins INTEGER NOT NULL DEFAULT 0,
    recipient_coins INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '15 minutes',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'cancelled')),
    CHECK (proposer_id <> recipient_id)
);
CREATE INDEX IF NOT EXISTS idx_fc_trades_recipient_pending ON fc_card_trades(recipient_id, status, expires_at);

CREATE TABLE IF NOT EXISTS fc_card_battles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guild_id TEXT,
    channel_id TEXT,
    challenger_id TEXT NOT NULL REFERENCES fc_card_users(discord_user_id) ON DELETE CASCADE,
    opponent_id TEXT NOT NULL REFERENCES fc_card_users(discord_user_id) ON DELETE CASCADE,
    challenger_card_id BIGINT NOT NULL,
    opponent_card_id BIGINT,
    challenger_stat TEXT NOT NULL,
    opponent_stat TEXT,
    random_stat TEXT,
    rounds JSONB,
    winner_id TEXT,
    loser_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '10 minutes',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending', 'completed', 'declined', 'expired', 'cancelled')),
    CHECK (challenger_id <> opponent_id)
);
CREATE INDEX IF NOT EXISTS idx_fc_battles_opponent_pending ON fc_card_battles(opponent_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_fc_battles_completed ON fc_card_battles(completed_at DESC) WHERE status = 'completed';
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)

