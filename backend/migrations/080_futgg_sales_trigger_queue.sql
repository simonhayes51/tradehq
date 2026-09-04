-- Migration 080: tables backing the triggered detailed-sales queue
-- target: core
-- requires-table: futgg_players
--
-- Part 5 of the FUT.GG price-detail infrastructure work replaces "every
-- card gets a detailed sales read on its tier's fixed cadence" (alone)
-- with a set of specific, falsifiable reasons a particular card deserves
-- one right now. Most of those reasons read data that already exists
-- (watchlist_popularity, alerts_log, futgg_recommendation_snapshots,
-- futgg_players.released_at, futgg_market_snapshot) - see
-- auto_sync/futgg_sales_triggers.py. Two reasons have no existing home:
--
-- futgg_price_change_events - futgg_bulk_price_sync.py already computes,
-- in-process, which cards had a genuine price change this cycle
-- (_last_price_changes), but that worker and futgg_sales_sync.py are
-- separate processes/containers with no shared memory - the only channel
-- between them is this database. This table is that channel: one row per
-- genuine bulk-price change, consumed (marked, never deleted - deletion
-- would erase the audit trail a "why did this fire" question needs) by
-- the trigger-selection pass once it has been considered.
--
-- futgg_sales_priority_requests - "user explicitly requested current
-- analysis" has no existing mechanism at all (bump_price_priority() in
-- market_data_provider.py bumps PRICE priority only, not sales). This is
-- a minimal queue a future endpoint can insert into; the trigger-selection
-- pass consumes it the same way.

CREATE TABLE IF NOT EXISTS futgg_price_change_events (
    id             BIGSERIAL PRIMARY KEY,
    card_id        BIGINT NOT NULL,
    previous_price INT,
    new_price      INT NOT NULL,
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_futgg_price_change_events_unconsumed
    ON futgg_price_change_events (detected_at)
    WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_futgg_price_change_events_card
    ON futgg_price_change_events (card_id);

CREATE TABLE IF NOT EXISTS futgg_sales_priority_requests (
    id            BIGSERIAL PRIMARY KEY,
    card_id       BIGINT NOT NULL,
    requested_by  TEXT,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_futgg_sales_priority_requests_unconsumed
    ON futgg_sales_priority_requests (requested_at)
    WHERE consumed_at IS NULL;
