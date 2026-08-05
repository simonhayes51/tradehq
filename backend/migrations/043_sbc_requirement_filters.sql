-- Migration 043: Structured SBC requirement filters
-- target: core
-- requires-table: sbc_challenges, futgg_players
--
-- sbc_challenges.requirements (migration 018) is JSONB, but both SBC
-- source scripts (auto_sync/futbin_sbc_sync.py, auto_sync/
-- easysbc_sbc_sync.py) only ever populate it with human-readable display
-- text (e.g. {"req_0": "Min. 1 Saudi Arabia Player"}) - confirmed by
-- reading both scrapers directly, neither FUTBIN's HTML nor EasySBC's
-- JSON API exposes structured nation/league/rarity/rating fields at the
-- source. "Promote every card matching this SBC's requirements" cannot
-- be computed from the stored text without parsing it, so this table is
-- the parsed, structured result - populated by
-- app/services/sbc_requirement_parser.py reading FROM the already-stored
-- requirement text (see that module's docstring for exactly what it can
-- and can't parse), not by changing the scrapers themselves.
--
-- One row per (challenge, parsed requirement) - a single challenge can
-- have multiple requirement lines (e.g. "11 Saudi League silver players"
-- AND "Min. 1 TOTW player" on the same squad-building challenge), and a
-- single requirement line can also fail to parse (kept as a row with
-- raw_text only, all filter columns NULL, matched=false) so the audit
-- trail always shows every requirement that was seen, whether or not it
-- was understood.

CREATE TABLE IF NOT EXISTS sbc_requirement_filters (
    id            BIGSERIAL PRIMARY KEY,
    challenge_id  BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,
    nation        TEXT,      -- NULL = unconstrained on this dimension
    league        TEXT,
    club          TEXT,
    rarity        TEXT,      -- matches futgg_players.rarity vocabulary where parseable
    rating_min    INT,
    rating_max    INT,
    min_count     INT NOT NULL DEFAULT 1,
    parsed        BOOLEAN NOT NULL DEFAULT FALSE,  -- false = raw_text couldn't be turned into a filter
    raw_text      TEXT NOT NULL,
    parsed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sbc_requirement_filters_challenge ON sbc_requirement_filters (challenge_id);
CREATE INDEX IF NOT EXISTS idx_sbc_requirement_filters_lookup
    ON sbc_requirement_filters (nation, league, rarity, rating_min, rating_max)
    WHERE parsed;
