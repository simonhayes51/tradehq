-- Migration 080: separate sell-time channel for the Server Feed
-- target: core
--
-- Purely additive to discord_servers (migrations 069/070). Nullable -
-- a server with no sell_channel_id set keeps today's behavior exactly:
-- publish_opportunity_updates() posts "TARGET HIT" / result updates into
-- the same feed_channel_id as the original opportunity. When set, those
-- result updates post into this channel instead - many servers run a
-- separate "sell-time" / results channel distinct from the live buy
-- feed, and this lets them route accordingly without splitting the buy
-- feed itself.

ALTER TABLE discord_servers
    ADD COLUMN IF NOT EXISTS sell_channel_id TEXT;

COMMENT ON COLUMN discord_servers.sell_channel_id IS
    'Channel opportunity_update ("TARGET HIT" / result) posts go to. NULL = post into feed_channel_id like the original opportunity (default, unchanged behavior).';
