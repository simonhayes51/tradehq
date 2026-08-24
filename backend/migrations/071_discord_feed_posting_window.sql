-- Migration 071: configurable posting time window for the self-service
-- Server Feed (/feed preferences and the web settings page)
-- target: core
--
-- Purely additive to discord_servers (migrations 069/070). All three
-- columns are nullable - a server with none of them set has no posting
-- restriction at all (existing behavior, unchanged). When set, they
-- describe a single daily hour range in the server's own timezone during
-- which publish_live_opportunities is allowed to post, e.g. start=8,
-- end=23, timezone='Europe/London' for a UK-only server that doesn't
-- want 3am pings. start > end is a valid overnight wraparound window
-- (e.g. start=20, end=6).

ALTER TABLE discord_servers
    ADD COLUMN IF NOT EXISTS posting_window_start_hour INTEGER
        CHECK (posting_window_start_hour IS NULL OR posting_window_start_hour BETWEEN 0 AND 23),
    ADD COLUMN IF NOT EXISTS posting_window_end_hour INTEGER
        CHECK (posting_window_end_hour IS NULL OR posting_window_end_hour BETWEEN 0 AND 23),
    ADD COLUMN IF NOT EXISTS posting_timezone TEXT;

COMMENT ON COLUMN discord_servers.posting_window_start_hour IS
    'Local hour (0-23, in posting_timezone) posting may start. NULL = no restriction (post any time, subject to cooldown/budget).';
COMMENT ON COLUMN discord_servers.posting_window_end_hour IS
    'Local hour (0-23, in posting_timezone) posting must stop by. May be less than start_hour for an overnight window (e.g. 20 -> 6).';
COMMENT ON COLUMN discord_servers.posting_timezone IS
    'IANA timezone name (e.g. "Europe/London") the start/end hours are interpreted in. NULL defaults to UTC if a window is set.';
