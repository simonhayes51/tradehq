-- Optional Discord role mentions for automated Server Feed posts.
--
-- buy_ping_role_id is mentioned when a new live opportunity is posted.
-- sell_ping_role_id is mentioned for TARGET HIT / result updates. When the
-- sell role is NULL the feed falls back to buy_ping_role_id, which makes the
-- common "use the same Trader role for both" setup a one-field change.
--
-- Nullable by design: existing servers keep today's no-ping behaviour until
-- an owner/admin explicitly configures a role in the Server Feed settings.

ALTER TABLE discord_servers
    ADD COLUMN IF NOT EXISTS buy_ping_role_id TEXT,
    ADD COLUMN IF NOT EXISTS sell_ping_role_id TEXT;
