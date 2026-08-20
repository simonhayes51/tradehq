-- Migration 054: watchlist_alerts.source, for FUT.GG-aware alert evaluation
-- target: watchlist
--
-- Mirrors migration 039's watchlist.source column, one step later in the
-- pipeline. watchlist_alerts (migration 016) has always been implicitly
-- FUTBIN-only: the poll loop's fetch_price()/_ref_price_for_alert()/
-- _resolve_player_name() all queried fut_players / a live FUTBIN scrape by
-- card_id, the legacy FUTBIN id space. A rule created for a FUT.GG-sourced
-- watchlist card (its card_id is a source_card_id from the unrelated
-- FUT.GG id space) resolved to nothing in either place - fetch_price()
-- returned {"price": None}, the poll loop silently skipped evaluating it
-- every single pass, and the rule could never fire regardless of how far
-- the real price moved. This column lets a rule declare which catalogue
-- its card_id belongs to, so main.py can branch to the right price/name
-- lookup per row instead of assuming FUTBIN. Existing rows all predate
-- FUT.GG watchlist support, hence the default.
ALTER TABLE watchlist_alerts ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'futbin';

-- Backfill: for any existing rule whose (user_id, card_id, platform) has a
-- matching watchlist row, adopt that row's real source rather than trusting
-- the blanket 'futbin' default - a rule created after FUT.GG watchlist
-- support shipped but before this column existed would otherwise be
-- mis-tagged.
UPDATE watchlist_alerts wa
SET source = w.source
FROM watchlist w
WHERE w.user_id = wa.user_id
  AND w.card_id = wa.card_id
  AND w.platform = wa.platform
  AND w.source <> wa.source;
