-- Migration 074: onboarding state and the two preferences the app uses
-- target: core
-- requires-table: users
--
-- Persisting onboarding completion is the whole point: a welcome flow
-- that reappears on every login stops being onboarding and becomes an
-- interstitial. localStorage is not an option - it is per-browser, so
-- signing in on a phone would replay the flow.
--
-- Only two preferences are stored, and BOTH are read by the product:
--
--   coin_budget      -> seeds the Trade Finder budget field and filters
--                       the "can I afford this" hint on opportunity cards
--   wants_price_alerts -> whether to offer the alert prompt after a card
--                       is added to the watchlist
--
-- Deliberately NOT stored: "preferred trading style". The engine has no
-- per-user strategy weighting, so asking would be collecting an answer
-- that changes nothing - which is worse than not asking, because it
-- implies a personalisation that does not exist.
--
-- Reversible: one additive table. `DROP TABLE member_onboarding;`
-- restores the previous schema exactly. No existing column is altered.

CREATE TABLE IF NOT EXISTS member_onboarding (
    user_id            TEXT PRIMARY KEY,
    -- NULL while in progress; set once the member finishes OR skips, so
    -- skipping is honoured permanently rather than re-asked next login.
    completed_at       TIMESTAMPTZ,
    skipped            BOOLEAN NOT NULL DEFAULT FALSE,
    coin_budget        BIGINT,
    wants_price_alerts BOOLEAN,
    -- So a future onboarding revision can decide whether to re-run for
    -- members who completed an older one, instead of guessing.
    version            TEXT NOT NULL DEFAULT 'onboarding-1',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
