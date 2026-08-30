-- Migration 072: make fired alerts dismissible, readable, and resolvable
-- target: watchlist
--
-- alerts_log (migration 016) is the record of alerts that have actually
-- FIRED - it is what Home's bell badge and "Alerts & content" feed read.
-- Three things were missing, each of which produced a user-visible bug:
--
--   1. No dismissal state at all. There has never been an endpoint that
--      removes a fired alert, so "delete this alert" could only ever
--      delete the alert RULE (watchlist_alerts) - the already-fired
--      notification stayed in the feed and kept being counted by the
--      badge, forever. This is the reported "alerts do not disappear"
--      bug: the row the user is looking at was never deletable.
--
--   2. No read state. The badge counted "the 5 most recent rows",
--      which is not a count of anything the user has or hasn't seen -
--      it never went down, and it never matched the panel.
--
--   3. No `source` column. Resolving a card_id to a player NAME needs to
--      know which catalogue that id belongs to (see migration 054: FUT.GG
--      source_card_ids and legacy FUTBIN card_ids are unrelated id
--      spaces). The dashboard was inferring it with a LEFT JOIN onto
--      `watchlist`, so the moment a user removed the card from their
--      watchlist the join produced NULL, the lookup went to the wrong
--      catalogue, and the alert rendered as the raw "Card 117493814 is
--      down 13%". Recording the source ON the alert row at write time
--      makes the resolution independent of state that can change later.
--
-- Reversible: all three are additive nullable/defaulted columns plus two
-- indexes. `DROP COLUMN dismissed_at, read_at, source` and the two
-- `DROP INDEX`es restore the previous schema exactly; no existing column
-- is altered and no row is deleted.

ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ;
ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;
ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'futbin';

-- Backfill `source` from the rule that produced each alert, then from the
-- user's watchlist row, before falling back to the column default. Rows
-- that predate FUT.GG watchlist support are genuinely 'futbin', so the
-- default is correct for them - this only fixes rows fired from a FUT.GG
-- rule while the column did not exist.
UPDATE alerts_log al
SET source = wa.source
FROM watchlist_alerts wa
WHERE wa.user_id = al.user_id
  AND wa.card_id = al.card_id
  AND wa.platform = al.platform
  AND wa.source <> al.source;

UPDATE alerts_log al
SET source = w.source
FROM watchlist w
WHERE w.user_id = al.user_id
  AND w.card_id = al.card_id
  AND w.platform = al.platform
  AND w.source <> al.source
  AND NOT EXISTS (
      SELECT 1 FROM watchlist_alerts wa
      WHERE wa.user_id = al.user_id AND wa.card_id = al.card_id AND wa.platform = al.platform
  );

-- The feed query is "this user's undismissed alerts, newest first", which
-- idx_alerts_user_time (user_id, sent_at) cannot serve without also
-- reading every dismissed row. Partial index, so it only carries the rows
-- that are actually still live in someone's feed.
CREATE INDEX IF NOT EXISTS idx_alerts_log_user_live
    ON alerts_log (user_id, sent_at DESC)
    WHERE dismissed_at IS NULL;

-- The badge query is the same set restricted to unread.
CREATE INDEX IF NOT EXISTS idx_alerts_log_user_unread
    ON alerts_log (user_id)
    WHERE dismissed_at IS NULL AND read_at IS NULL;
