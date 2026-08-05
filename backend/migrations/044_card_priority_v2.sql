-- Migration 044: Adaptive priority engine v2 - baseline/effective tier
-- split, expiring triggers, full audit trail
-- target: core
-- requires-table: futgg_players
--
-- SUPERSEDES migration 042 (card_priority/watchlist_popularity/
-- priority_engine_events). 042 shipped a version that folded staleness
-- directly into the priority score - a DORMANT card aging exactly as
-- designed would silently climb back up, since its own growing
-- freshness deficit counted toward promotion. Nothing in 042's tables
-- held real production data (Phase 1/Milestone 1 had only just gone
-- live in shadow mode), so DROP+CREATE is safe here, same as this
-- codebase's established "no CREATE OR REPLACE MATERIALIZED VIEW,
-- rebuild wholesale" convention for early-stage schema iteration
-- (see migrations 023/034/036 rebuilding fair_value_mv).
--
-- WHAT CHANGED, AND WHY
-- ----------------------
-- 1. baseline_tier vs effective_tier: baseline_tier is what a card IS
--    (liquidity/card-type/opportunity/interest/volatility/event) with
--    NO freshness/staleness input at all - that was the bug. Scheduling
--    urgency ("how overdue is this card") is a claim-order concept for
--    a later milestone, never a scoring input here.
-- 2. Tier is rule-derived, not a weighted-score cutoff - importance_score
--    is still computed and stored, but only for observability/analysis
--    (see priority_engine.py). reason_codes records which rule fired.
-- 3. priority_triggers: temporary forced promotions (SBC match, sales/
--    price spike, new release, user position, manual override) are now
--    their own rows with activation/expiry, not a same-pass score
--    override - effective_tier = max(baseline_tier, best active
--    trigger's forced_min_tier). When a trigger expires, the card
--    reverts straight to baseline_tier, no forced staged demotion.
-- 4. Full audit columns throughout, per the review's own list: old/new
--    baseline tier, old/new effective tier, component snapshot, reason
--    codes, trigger source, env flags active at compute time, engine
--    version.

DROP TABLE IF EXISTS priority_engine_events;
DROP TABLE IF EXISTS priority_triggers;
DROP TABLE IF EXISTS card_priority;
-- watchlist_popularity's shape is unchanged (still card_id/watcher_count/
-- updated_at) - only its recompute query changes (recent-window distinct
-- users instead of lifetime) - so it is NOT dropped here, no schema
-- change needed. Created IF NOT EXISTS defensively in case 042 never
-- ran on this database at all.
CREATE TABLE IF NOT EXISTS watchlist_popularity (
    card_id       BIGINT PRIMARY KEY,
    watcher_count INT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE card_priority (
    card_id                    BIGINT PRIMARY KEY,

    baseline_tier              TEXT NOT NULL CHECK (baseline_tier IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'DORMANT')),
    effective_tier             TEXT NOT NULL CHECK (effective_tier IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'DORMANT')),
    importance_score           DOUBLE PRECISION NOT NULL,  -- observability only, does NOT decide baseline_tier

    -- Independently inspectable [0,1] inputs to importance_score AND to
    -- the rule table (priority_engine.py consults these directly, not
    -- just the blended score) - deliberately NO freshness/staleness
    -- component here, see header.
    liquidity_component        DOUBLE PRECISION,
    card_type_component        DOUBLE PRECISION,
    opportunity_component      DOUBLE PRECISION,
    user_interest_component    DOUBLE PRECISION,
    volatility_component       DOUBLE PRECISION,
    event_component            DOUBLE PRECISION,

    -- Reserved for a future normalized opportunity-quality rate
    -- (qualified / eligible, shrunk toward a global baseline for small
    -- samples - never a raw count, which would reward cards simply for
    -- being scraped more often). Not populated yet (opportunity_component
    -- stays 0 until the outcome-tracking join lands) - columns exist now
    -- so that milestone doesn't need another migration.
    qualified_opportunity_count INT,
    eligible_evaluation_count   INT,

    reason_codes                TEXT[] NOT NULL DEFAULT '{}',

    previous_baseline_tier      TEXT,
    previous_effective_tier     TEXT,
    baseline_tier_changed_at    TIMESTAMPTZ,
    effective_tier_changed_at   TIMESTAMPTZ,

    engine_version               TEXT NOT NULL,
    env_flags_snapshot           JSONB NOT NULL DEFAULT '{}',
    computed_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_card_priority_baseline_tier ON card_priority (baseline_tier);
CREATE INDEX idx_card_priority_effective_tier ON card_priority (effective_tier);
CREATE INDEX idx_card_priority_computed_at ON card_priority (computed_at);

-- Temporary forced promotions, independent of baseline classification.
-- A card can have multiple concurrent active triggers (e.g. an SBC match
-- AND a sales spike at once) - effective_tier takes the highest-ranked
-- forced_min_tier among all currently-active rows for that card.
CREATE TABLE priority_triggers (
    id               BIGSERIAL PRIMARY KEY,
    card_id          BIGINT NOT NULL,
    trigger_type     TEXT NOT NULL CHECK (trigger_type IN (
                         'sbc_match', 'sales_spike', 'price_spike',
                         'new_release', 'user_position', 'manual_override'
                     )),
    trigger_reason   TEXT,
    forced_min_tier  TEXT NOT NULL CHECK (forced_min_tier IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'DORMANT')),
    source_event_id  BIGINT REFERENCES market_events(id) ON DELETE SET NULL,
    activated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ,  -- NULL = indefinite (manual_override only, in practice)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_priority_triggers_card ON priority_triggers (card_id);
CREATE INDEX idx_priority_triggers_active ON priority_triggers (card_id, expires_at);

-- Full audit trail - every classification pass and every future
-- schedule mutation writes here. "Why was this card refreshed, delayed,
-- promoted, excluded, or displayed" must be answerable from this table
-- alone.
CREATE TABLE priority_engine_events (
    id                       BIGSERIAL PRIMARY KEY,
    card_id                  BIGINT NOT NULL,

    previous_baseline_tier   TEXT,
    new_baseline_tier        TEXT NOT NULL,
    previous_effective_tier  TEXT,
    new_effective_tier       TEXT NOT NULL,

    -- Only populated once a later milestone starts writing
    -- next_sales_due_at - NULL here for every Milestone 1 row.
    previous_due_at          TIMESTAMPTZ,
    new_due_at               TIMESTAMPTZ,

    reason                   TEXT NOT NULL,  -- 'initial' | 'baseline_change' | 'trigger_activated' | 'trigger_expired' | 'conservative_demotion'
    reason_codes             TEXT[] NOT NULL DEFAULT '{}',
    component_snapshot       JSONB NOT NULL DEFAULT '{}',

    trigger_source           TEXT,       -- e.g. 'sbc_match' when reason involves a trigger
    trigger_expires_at       TIMESTAMPTZ,

    score_before             DOUBLE PRECISION,
    score_after              DOUBLE PRECISION,

    env_flags_snapshot       JSONB NOT NULL DEFAULT '{}',
    engine_version            TEXT NOT NULL,

    occurred_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_priority_engine_events_time ON priority_engine_events (occurred_at DESC);
CREATE INDEX idx_priority_engine_events_card ON priority_engine_events (card_id, occurred_at DESC);
CREATE INDEX idx_priority_engine_events_reason ON priority_engine_events (reason);
