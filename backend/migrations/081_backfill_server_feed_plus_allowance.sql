-- Migration 081: backfill existing Server Feed+ subscribers to the new
-- 15/day allowance
-- target: core
--
-- discord_server_settings.max_posts_per_day and discord_servers.budget_
-- low/medium/high are written once, at /feed setup time
-- (discord_feed_billing.upsert_pending_setup), from the PLANS /
-- BUDGET_ALLOCATIONS dicts in app/services/discord_feed_billing.py.
-- Nothing re-syncs them afterward on its own - not even /feed
-- preferences, which only recomputes budget_low/medium/high (never
-- max_posts_per_day). So bumping server_feed_plus from 12 to 15/day in
-- code (and rescaling its budget-mix presets to sum to 15) only affects
-- servers that run /feed setup from here on; every server that already
-- subscribed is permanently stuck on the numbers it signed up under
-- unless backfilled here.
--
-- Purely a data UPDATE, scoped to plan='server_feed_plus', and
-- idempotent - re-running it is a no-op past the first time. budget_mix
-- is always one of the four named presets (there is no free-form custom
-- allocation), so recomputing budget_low/medium/high from budget_mix
-- alone is safe: it cannot clobber a genuinely custom split, because
-- none exists.

UPDATE discord_server_settings st
SET max_posts_per_day = 15, updated_at = NOW()
FROM discord_servers s
WHERE st.server_id = s.id
  AND s.plan = 'server_feed_plus'
  AND st.max_posts_per_day <> 15;

UPDATE discord_servers
SET
    budget_low = CASE budget_mix
        WHEN 'balanced' THEN 5
        WHEN 'mostly_low' THEN 10
        WHEN 'mostly_medium' THEN 2
        WHEN 'mostly_high' THEN 0
    END,
    budget_medium = CASE budget_mix
        WHEN 'balanced' THEN 5
        WHEN 'mostly_low' THEN 5
        WHEN 'mostly_medium' THEN 11
        WHEN 'mostly_high' THEN 5
    END,
    budget_high = CASE budget_mix
        WHEN 'balanced' THEN 5
        WHEN 'mostly_low' THEN 0
        WHEN 'mostly_medium' THEN 2
        WHEN 'mostly_high' THEN 10
    END,
    updated_at = NOW()
WHERE plan = 'server_feed_plus'
  AND budget_mix IN ('balanced', 'mostly_low', 'mostly_medium', 'mostly_high');
