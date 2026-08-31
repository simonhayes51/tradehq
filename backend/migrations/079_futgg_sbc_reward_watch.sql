-- Migration 079: SBC-reward watch table for the FUT.GG buy-signal engine
-- target: core
--
-- Tracks cards that can currently be obtained by completing a live SBC,
-- so the engine can treat their price as under active supply pressure
-- rather than a genuine discount - every completion mints another copy
-- into a market that already has one. See
-- app/services/sbc_reward_match.py, which populates this table by
-- name-matching live SBC rewards (market_events on the PLAYER database,
-- migration 018) against futgg_players (this, the core database), and
-- app/services/futgg_intelligence.py's sbc_reward_active gate.
--
-- Deliberately its own small table, not a column added to
-- futgg_market_snapshot. That view is DROP+CREATE on every migration
-- (Postgres has no CREATE OR REPLACE MATERIALIZED VIEW), and the last
-- two times its column set changed, a name silently missing from
-- app/services/market_data_provider.py's _SNAPSHOT_COLUMNS list broke
-- the engine in production with no error anywhere (see that constant's
-- own comment, migrations 050/052). This table is read separately and
-- merged onto snapshot rows in Python
-- (market_data_provider._attach_sbc_reward_watch), which keeps the
-- match logic - inherently fuzzy, since EA's reward-item ids have no
-- known mapping to this codebase's card ids (see
-- easysbc_sbc_sync.py's own docstring) - entirely out of the
-- view/matview blast radius.
--
-- No foreign key to futgg_players: that table is created by auto_sync,
-- not by a backend migration (same reason 052/063 ALTER it under a
-- `requires-column` guard rather than assuming it exists), and a row
-- here that no longer matches an active card is harmless - it simply
-- never shows up in a lookup and is deleted on the next refresh pass.

CREATE TABLE IF NOT EXISTS futgg_sbc_reward_watch (
    futgg_source_card_id BIGINT PRIMARY KEY,
    reward_title          TEXT NOT NULL,
    match_method           TEXT NOT NULL,
    market_event_id        BIGINT,
    sbc_ends_at             TIMESTAMPTZ,
    matched_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_futgg_sbc_reward_watch_ends_at
    ON futgg_sbc_reward_watch (sbc_ends_at);
