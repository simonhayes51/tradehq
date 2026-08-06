-- Migration 049: SBC resource-demand model
-- target: core
-- requires-table: sbc_challenges, market_events, sbc_requirement_filters
--
-- SBC relevance model revision (production-readiness review). Root
-- cause of the 8,372-row sbc_match trigger blowup: sync_sbc_triggers()
-- (app/services/sbc_card_matching.py, pre-revision) matched each parsed
-- requirement LINE independently and unioned the results across every
-- challenge/event, with no gate on how many cards a bare rarity/rating
-- constraint (no nation/league/club) actually resolves to. "Min. 1 TOTW
-- Player" alone promoted every TOTW card in the whole catalogue to HIGH,
-- individually, forever (until grace-expiry) - Fernando Torres' SBC
-- matched 1,687 cards this way, not because 1,687 cards are relevant to
-- THAT card, but because a broad resource requirement was mis-modelled
-- as an individual-card identity trigger.
--
-- This table is the fix: broad, reusable-resource requirements (exact/
-- min rating, TOTW, TOTS, named promo types, Icon/Hero, Gold/Silver/
-- Bronze Rare, whole-squad rating) are recorded as MARKET-SEGMENT demand
-- here instead of creating one priority_triggers row per qualifying
-- card. Narrow, genuinely constrained pools (named league/nation/club,
-- optionally combined with rarity) still create individual
-- priority_triggers rows via app/services/sbc_demand_classifier.py's
-- pool-size/concentration gating - that path is unchanged in shape, just
-- now gated. See that module for the full classification logic and the
-- time-decay model.
--
-- One row per (challenge_id, resource_type, resource_value) - re-synced
-- (last_confirmed_at bumped, decayed_weight recomputed) on every
-- classification pass, same idempotent upsert convention priority_
-- triggers already uses (migration 046).

CREATE TABLE IF NOT EXISTS sbc_resource_demand (
    id                            BIGSERIAL PRIMARY KEY,
    source_event_id               BIGINT NOT NULL REFERENCES market_events(id) ON DELETE CASCADE,
    challenge_id                  BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,

    resource_type                 TEXT NOT NULL CHECK (resource_type IN (
                                       'EXACT_RATING', 'MIN_RATING', 'MIN_TEAM_RATING',
                                       'TOTW', 'TOTS', 'PROMO_TYPE',
                                       'ICON', 'HERO',
                                       'GOLD_RARE', 'SILVER_RARE', 'BRONZE_RARE'
                                   )),
    -- Free-form value scoped to resource_type - a rating number
    -- ("87"), a rating range ("85-90"), or a promo-type slug
    -- ("fut_birthday") for PROMO_TYPE. Never a card_id - this table
    -- never names individual cards, by design.
    resource_value                TEXT NOT NULL,
    required_count                INT NOT NULL DEFAULT 1,

    event_started_at              TIMESTAMPTZ,
    event_expires_at              TIMESTAMPTZ,
    repeatable                    BOOLEAN NOT NULL DEFAULT FALSE,
    last_confirmed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    source_provider                TEXT NOT NULL CHECK (source_provider IN ('futbin', 'easysbc')),
    source_challenge_external_id  TEXT,
    raw_text                      TEXT NOT NULL,

    -- CONFIRMED = a clean, unambiguous, single-interpretation parse -
    -- counted in active aggregation. AMBIGUOUS/UNPARSED = stored for
    -- diagnostics/audit but never activated as demand (mirrors
    -- sbc_requirement_filters.ambiguous's own "store, never guess" rule).
    ambiguity_status               TEXT NOT NULL DEFAULT 'CONFIRMED'
                                       CHECK (ambiguity_status IN ('CONFIRMED', 'AMBIGUOUS', 'UNPARSED')),

    -- Reinforcement status - structural placeholder for step 6 of the
    -- revision (observed market behaviour confirming/weakening a resource
    -- hypothesis). Every row starts UNCONFIRMED; nothing in this
    -- migration/round computes MARKET_CONFIRMED/DECAYING/EXPIRED yet -
    -- see sbc_demand_classifier.py's module docstring for what's
    -- implemented now vs reserved for later.
    market_status                  TEXT NOT NULL DEFAULT 'UNCONFIRMED'
                                       CHECK (market_status IN ('UNCONFIRMED', 'MARKET_CONFIRMED', 'DECAYING', 'EXPIRED')),

    -- Snapshot of compute_demand_weight()'s output at last sync time -
    -- stored (not just computed on read) so every accepted/decayed value
    -- is auditable against what the engine actually used, not
    -- recomputed differently later against a config that may have since
    -- changed. See sbc_demand_classifier.py::compute_demand_weight.
    decayed_weight                  DOUBLE PRECISION,

    created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (challenge_id, resource_type, resource_value)
);

CREATE INDEX IF NOT EXISTS idx_sbc_resource_demand_type_value
    ON sbc_resource_demand (resource_type, resource_value)
    WHERE ambiguity_status = 'CONFIRMED';
CREATE INDEX IF NOT EXISTS idx_sbc_resource_demand_event
    ON sbc_resource_demand (source_event_id);
CREATE INDEX IF NOT EXISTS idx_sbc_resource_demand_confirmed_at
    ON sbc_resource_demand (last_confirmed_at);
