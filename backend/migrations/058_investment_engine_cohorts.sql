-- Migration 058: Investment Engine — cohort classification
-- target: core
-- requires-table: futgg_players
-- requires-column: futgg_players.game_year, futgg_players.price_tier, futgg_players.primary_position
--
-- WHY
-- ---
-- The Investment Engine (see backend/INVESTMENT_ENGINE.md) is a separate
-- concept from the existing Trade/Opportunity Engine: it answers "is this
-- market segment likely to appreciate over the next 1-3 days", not "is
-- there money in this listing right now". Its first decision for any
-- card is which cohort it belongs to (premium_meta / mid_tier_meta /
-- desirable_starter / ordinary / likely_fodder), computed the same way
-- the FC26 launch-market research did it (research/fc26_launch_market
-- Phase 8's build_cohorts): a card's own launch/reference price
-- percentile WITHIN its position group, never compared across positions
-- and never using price data from after the card's own reference date.
--
-- This table exists so that classification is PERSISTED AND VERSIONED,
-- per explicit product requirement: "Persist cohort membership/version
-- so historical decisions remain auditable even if the classification
-- logic later changes." A row here is a frozen assertion ("as of
-- classifier-version cohort-1.0, this card was premium_meta"), never
-- recomputed in place - a logic change ships as a NEW classifier_version,
-- leaving every prior decision's basis intact and inspectable.
--
-- Deliberately does NOT duplicate futgg_players metadata (rating,
-- position, league, stats, etc.) - futgg_players remains the single
-- canonical card-metadata source; this table only ever adds the cohort
-- label plus the minimal basis for it (reference price, percentile,
-- position group).

CREATE TABLE IF NOT EXISTS investment_cohort_memberships (
    id                  BIGSERIAL PRIMARY KEY,
    game_year           SMALLINT NOT NULL,
    source_card_id      BIGINT NOT NULL REFERENCES futgg_players(source_card_id),

    cohort_key          TEXT NOT NULL CHECK (cohort_key IN (
        'premium_meta', 'mid_tier_meta', 'desirable_starter', 'ordinary', 'likely_fodder'
    )),
    position_group      TEXT NOT NULL,
    price_percentile    NUMERIC NOT NULL,   -- [0,1] within-position-group rank that produced this cohort
    reference_price     NUMERIC NOT NULL,   -- the price this classification was computed from
    reference_captured_at TIMESTAMPTZ,      -- when reference_price was observed (NULL if unknown)

    classifier_version  TEXT NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One row per (card, classifier_version) per game year - a version
    -- bump creates new rows rather than mutating old ones (see header).
    UNIQUE (game_year, source_card_id, classifier_version)
);

-- Cohort-membership lookups ("give me every premium_meta card for the
-- current classifier version") are the hot path the engine runs every
-- recompute pass.
CREATE INDEX IF NOT EXISTS investment_cohort_memberships_lookup_idx
    ON investment_cohort_memberships (game_year, cohort_key, classifier_version);
CREATE INDEX IF NOT EXISTS investment_cohort_memberships_card_idx
    ON investment_cohort_memberships (source_card_id, game_year);

COMMENT ON TABLE investment_cohort_memberships IS
    'Versioned, auditable cohort assignment for the Investment Engine. Never mutated in place - a classifier change adds new rows under a new classifier_version.';
