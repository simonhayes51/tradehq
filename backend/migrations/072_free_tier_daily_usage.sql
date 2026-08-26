-- Migration 072: concurrency-safe daily usage counters for Free-tier limits
-- target: core
--
-- Backs the Free/Premium tier separation: Free's daily allowances (Live
-- Opportunities, Trade Finder searches, AI Chat messages) all reset once
-- per UTC day (see app/auth/entitlements.py's DAILY_RESET_TIMEZONE) and
-- must be safe under concurrent requests from the same user (e.g. two
-- browser tabs). A single row per (user_id, usage_key, day) with an atomic
-- "INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count"
-- serializes concurrent increments on that row without an explicit lock,
-- the same way every other multi-instance-sensitive write in this codebase
-- already does (see main.py's advisory-lock registry for the alternative
-- pattern used where a whole pass, not a single row, needs coordinating).
--
-- Purely additive - a fresh table, no existing data touched. Old rows are
-- never queried once their day has passed, so there is no backfill to do;
-- an optional periodic prune (mirroring prune_bin_history()'s pattern) can
-- be added later if the table's growth becomes worth trimming.

CREATE TABLE IF NOT EXISTS daily_usage_counters (
    user_id     TEXT NOT NULL,
    usage_key   TEXT NOT NULL,
    day         DATE NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, usage_key, day)
);

COMMENT ON TABLE daily_usage_counters IS
    'Concurrency-safe per-user daily usage counts for Free-tier limits (opportunities viewed, trade-finder searches, AI chat messages). One row per (user_id, usage_key, UTC day).';
COMMENT ON COLUMN daily_usage_counters.usage_key IS
    'Which allowance this row tracks, e.g. ''opportunity_view'', ''trade_finder_search'', ''ai_chat_message''.';
