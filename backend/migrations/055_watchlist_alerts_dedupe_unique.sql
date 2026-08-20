-- Migration 055: dedupe watchlist_alerts, then enforce uniqueness
-- target: watchlist
--
-- watchlist_alerts (migration 016) has never had a uniqueness constraint
-- on (user_id, card_id, platform, metric), unlike watchlist itself
-- (idx_watchlist_unique, migration 016). POST /api/watchlist-alerts was a
-- bare INSERT with no ON CONFLICT clause, so any repeat submission (a
-- double-click before the button's disabled state committed, a manual
-- resubmit after a slow response) silently created a second identical
-- rule rather than being rejected or merged - this is the confirmed cause
-- of a card showing two identical alert rules in the Watchlist UI.
--
-- Step 1: deduplicate existing rows before the constraint can be added,
-- keeping the most recently created row per (user_id, card_id, platform,
-- metric) - the newest is the one most likely to reflect the user's last
-- intended configuration, and its cooloff/last_alert_at state is the most
-- current. This does not touch alerts_log (the fired-alert history), so no
-- record of past notifications is lost - only the redundant rule
-- definition rows are removed.
DELETE FROM watchlist_alerts wa
WHERE wa.id NOT IN (
    SELECT DISTINCT ON (user_id, card_id, platform, metric) id
    FROM watchlist_alerts
    ORDER BY user_id, card_id, platform, metric, created_at DESC, id DESC
);

-- Step 2: enforce it going forward. app/main.py's create_watchlist_alert
-- now upserts on this exact key (see the ON CONFLICT clause), so this
-- constraint is a backstop against any other write path, not the primary
-- mechanism - but it is the one that actually guarantees no duplicate can
-- exist regardless of how it might be inserted.
CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_alerts_unique
    ON watchlist_alerts (user_id, card_id, platform, metric);
