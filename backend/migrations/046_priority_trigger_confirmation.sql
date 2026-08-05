-- Migration 046: SBC trigger lifecycle hardening - confirmation tracking
-- target: core
-- requires-table: priority_triggers
--
-- Gap found during production-readiness review of migration 044: a
-- priority_triggers row's expires_at is set ONCE at creation from
-- market_events.ends_at, but both SBC sync writers
-- (auto_sync/futbin_sbc_sync.py, auto_sync/easysbc_sbc_sync.py) update
-- ends_at via `COALESCE($3, ends_at)` - confirmed by reading both
-- writers directly, not assumed - meaning an SBC that silently
-- disappears from the upstream feed (delisted early, or simply not
-- re-scraped) never gets its ends_at proactively corrected. A trigger
-- created from a stale/incorrect ends_at could stay "active" long after
-- the SBC is actually gone.
--
-- last_confirmed_at is the fix: sbc_card_matching.sync_sbc_triggers()
-- now updates it to now() every pass a match is RE-confirmed (not just
-- on creation), and a trigger is only "active" (see
-- priority_recompute.py::_active_triggers_by_card /
-- _CONSERVATIVE_DEMOTION_CANDIDATES_QUERY) when BOTH expires_at hasn't
-- passed AND last_confirmed_at is within PRIORITY_TRIGGER_CONFIRMATION_
-- GRACE_HOURS (see priority_config.py) - a bounded grace period, not an
-- indefinite hold, so a genuinely-vanished SBC's trigger naturally goes
-- stale and stops applying even if expires_at was wrong, without ever
-- needing a proactive "is this SBC still really live" check upstream.
--
-- manual_override triggers (the one trigger_type with no natural
-- "re-confirm" source) are exempt from the grace-period check - see
-- priority_recompute.py's active-trigger query.

ALTER TABLE priority_triggers
    ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_priority_triggers_last_confirmed ON priority_triggers (last_confirmed_at);
