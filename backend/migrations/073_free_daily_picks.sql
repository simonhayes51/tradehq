-- Migration 073: persist the two daily Free opportunity picks
-- target: core
-- requires-table: futgg_market_snapshot
--
-- Free members get FREE_DAILY_PICKS complete opportunities per FC content
-- day (18:00 Europe/London boundary - see app/services/content_day.py).
-- Those picks have to be the SAME two cards for every Free member for the
-- whole day, and they have to stay unlocked for the rest of the day even
-- if the live ranking moves underneath them.
--
-- Deterministic re-derivation alone cannot promise that: the selector
-- reads the live snapshot, so a card that drops out of the candidate pool
-- at 3pm would vanish from a user's picks mid-session, after they had
-- already been shown it. Recording the choice once, the first time the day
-- is asked about, is what makes "already unlocked stays unlocked" true.
--
-- Reversible: one new table and its index, nothing else touched.
-- `DROP TABLE free_daily_picks;` restores the previous schema exactly.

CREATE TABLE IF NOT EXISTS free_daily_picks (
    -- The LOCAL date the content day started on, so the whole window from
    -- Tuesday 18:00 to Wednesday 17:59 is keyed to Tuesday.
    content_day     DATE    NOT NULL,
    -- 0-based position. Slot order is the display order, and it is fixed
    -- at write time so the two picks never swap places on refresh.
    slot            INT     NOT NULL,
    source_card_id  BIGINT  NOT NULL,
    -- Why this card was chosen, frozen for support questions ("why did I
    -- get this one?") and so a later selector change is visible in the
    -- data rather than silently rewriting history.
    selection_reason TEXT,
    -- The engine's own numbers at selection time. NOT used for display -
    -- the API always re-reads live figures so a Free member never acts on
    -- a stale price - but kept so a pick can be audited after the fact.
    selected_signal  TEXT,
    selected_bin     BIGINT,
    selector_version TEXT    NOT NULL DEFAULT 'free-picks-1',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (content_day, slot)
);

-- The read is always "give me every pick for this content day", which the
-- primary key already serves. This one exists for the housekeeping delete
-- of old rows.
CREATE INDEX IF NOT EXISTS idx_free_daily_picks_created ON free_daily_picks (created_at);
