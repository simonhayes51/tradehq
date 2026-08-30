-- Migration 075: an internal funnel-event store
-- target: core
--
-- There is no analytics provider in this project, and adding one is a
-- privacy and contractual decision that is not an implementation detail.
-- This is the small internal interface instead: one table, one endpoint
-- (app/routers/v2/events.py), one named vocabulary shared with
-- frontend/src/lib/analytics.js.
--
-- What it deliberately does NOT store:
--   * no player searches, chat text, email addresses or Discord handles -
--     the server re-applies its own allow-list to `props`, so a client
--     that sends more than it should has the extra dropped here rather
--     than trusted not to send it
--   * no IP address and no user agent
--   * user_id is nullable and is the internal id, never a Discord id, so
--     a signed-out funnel step records no identity at all
--
-- Recommended implementation if a provider is adopted later: keep this
-- endpoint as the only call site, and forward from the server. Every
-- event already passes through one function with the allow-list applied,
-- which is the part that is genuinely hard to retrofit.
--
-- Reversible: one new table and its indexes, nothing else touched.
-- `DROP TABLE funnel_events;` restores the previous schema exactly.

CREATE TABLE IF NOT EXISTS funnel_events (
    id           BIGSERIAL PRIMARY KEY,
    event        TEXT        NOT NULL,
    -- Nullable on purpose: signed-out steps (signup_viewed, and every
    -- upgrade_prompt_viewed a visitor sees) are the most important half
    -- of the funnel and must be recordable without an account.
    user_id      TEXT,
    -- Allow-listed keys only; see app/routers/v2/events.py.
    props        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The two questions this table exists to answer: "how many of each event
-- in a period" and "what did this account do in order".
CREATE INDEX IF NOT EXISTS funnel_events_event_time_idx
    ON funnel_events (event, occurred_at DESC);
CREATE INDEX IF NOT EXISTS funnel_events_user_time_idx
    ON funnel_events (user_id, occurred_at DESC)
    WHERE user_id IS NOT NULL;
