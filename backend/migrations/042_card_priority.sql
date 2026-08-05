-- Migration 042: Adaptive priority engine - shadow-mode scoring tables
-- target: core
-- requires-table: futgg_players
--
-- Phase 1 of the adaptive market-coverage system (see the "Adaptive
-- Market Coverage & Priority Refresh System" plan). Purely additive:
-- this migration only creates new tables. Nothing here is read by, or
-- written to, by any live scraper (futgg_sales_sync.py,
-- futgg_bulk_price_sync.py, futgg_player_sync.py) - card_priority is
-- computed by a new backend-side refresher (priority_recompute.py) and
-- is not yet consulted by any scheduling decision. That wiring is a
-- later, separately-flagged phase (see priority_recompute.py's own
-- module docstring) - deliberately sequenced this way so the scoring
-- function can be validated against real production data for days
-- before it can influence how often anything actually gets scraped.

CREATE TABLE IF NOT EXISTS card_priority (
    card_id                  BIGINT PRIMARY KEY,
    priority_score           DOUBLE PRECISION NOT NULL,
    tier                     TEXT NOT NULL CHECK (tier IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'DORMANT')),

    -- Each component is independently inspectable ([0,1]) so a bad tier
    -- assignment can be traced to a specific input rather than just the
    -- final blended number - the whole point of computing a shadow score
    -- before trusting it is being able to explain it.
    liquidity_component      DOUBLE PRECISION,
    freshness_component      DOUBLE PRECISION,
    card_type_component      DOUBLE PRECISION,
    opportunity_component    DOUBLE PRECISION,
    user_interest_component  DOUBLE PRECISION,
    volatility_component     DOUBLE PRECISION,
    event_component          DOUBLE PRECISION,

    -- Set when event_component was forced to 1.0 by an active SBC/event
    -- match (see sbc_card_matching.py) rather than organically computed -
    -- distinguishes "promoted because someone needs this card right now"
    -- from "promoted because it's naturally liquid", which matter
    -- differently when this table is read for observability.
    forced_by_event_id       BIGINT,

    previous_tier            TEXT,
    tier_changed_at          TIMESTAMPTZ,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_card_priority_tier ON card_priority (tier);
CREATE INDEX IF NOT EXISTS idx_card_priority_computed_at ON card_priority (computed_at);

-- Fills a real gap: the live `watchlist` table (app/routers/watchlist.py,
-- on the separate WATCHLIST_DATABASE_URL pool - NOT the dead
-- watchlist_items table from 20250901_events_watchlist.sql, which that
-- router's own header comment confirms was superseded and never wired
-- up) has per-user rows but nothing anywhere aggregates them into a
-- per-card popularity count. This table is that rollup, recomputed each
-- pass rather than incrementally maintained - the source table is small
-- enough (per-user rows, not per-event) that a periodic full
-- re-aggregation is simpler and self-healing versus trying to keep a
-- running counter correct across adds/removes across a cross-pool read.
CREATE TABLE IF NOT EXISTS watchlist_popularity (
    card_id       BIGINT PRIMARY KEY,
    watcher_count INT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Promotion/demotion audit log. Every tier change, and (once a later
-- phase starts writing to next_sales_due_at/next_price_due_at) every
-- schedule adjustment, is recorded here - this is what answers "why did
-- card X stop refreshing" during an incident, and is also the direct
-- source for the promotion/demotion-count metrics the feature asks for.
CREATE TABLE IF NOT EXISTS priority_engine_events (
    id            BIGSERIAL PRIMARY KEY,
    card_id       BIGINT NOT NULL,
    from_tier     TEXT,
    to_tier       TEXT NOT NULL,
    reason        TEXT NOT NULL,  -- 'activity_spike' | 'inactivity_demote' | 'sbc_match' | 'recompute' | 'initial'
    score_before  DOUBLE PRECISION,
    score_after   DOUBLE PRECISION,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_priority_engine_events_time ON priority_engine_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_priority_engine_events_card ON priority_engine_events (card_id, occurred_at DESC);
