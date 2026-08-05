-- Migration 045: SBC requirement filters v2 - source traceability +
-- explicit ambiguity flag
-- target: core
-- requires-table: sbc_challenges, futgg_players
--
-- SUPERSEDES migration 043. No behavioural change to what CAN be
-- parsed (both SBC sources are confirmed free-text-only at the source -
-- see migration 043's original header and the plan doc's "What already
-- exists" section for how that was verified by reading both scrapers
-- directly, not guessed) - this adds:
--   1. source / source_challenge_external_id, so every normalized
--      requirement traces back to which provider and which upstream
--      record produced it (auditability requirement from the review).
--   2. ambiguous - a requirement mentioning multiple candidate known
--      values (e.g. two different nations) or an OR-condition
--      ("Any TOTW/TOTS/FOF") is flagged distinctly from a plain
--      parse failure, and like an unparsed row is NEVER used for
--      auto-matching (see sbc_card_matching.py) - recorded for
--      diagnostics, not treated as safe to act on.
-- No real data existed in the old table (Milestone 1 shadow mode had
-- only just started), so DROP+CREATE rather than ALTER.

DROP TABLE IF EXISTS sbc_requirement_filters;

CREATE TABLE sbc_requirement_filters (
    id                            BIGSERIAL PRIMARY KEY,
    challenge_id                  BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,
    source                        TEXT NOT NULL CHECK (source IN ('futbin', 'easysbc')),
    source_challenge_external_id  TEXT,  -- the source's own natural key for this challenge/set, where available

    nation        TEXT,      -- NULL = unconstrained on this dimension
    league        TEXT,
    club          TEXT,
    rarity        TEXT,      -- matches futgg_players.rarity vocabulary where parseable
    rating_min    INT,
    rating_max    INT,
    min_count     INT NOT NULL DEFAULT 1,

    parsed        BOOLEAN NOT NULL DEFAULT FALSE,  -- false = raw_text couldn't be turned into a filter at all
    ambiguous     BOOLEAN NOT NULL DEFAULT FALSE,  -- true = multiple candidate interpretations or an OR-condition - never auto-matched even if parsed=true
    raw_text      TEXT NOT NULL,
    parsed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sbc_requirement_filters_challenge ON sbc_requirement_filters (challenge_id);
CREATE INDEX idx_sbc_requirement_filters_lookup
    ON sbc_requirement_filters (nation, league, rarity, rating_min, rating_max)
    WHERE parsed AND NOT ambiguous;
