-- Migration 098: add the 72-hour recommendation outcome horizon
-- target: core
-- requires-table: futgg_recommendation_outcomes
--
-- Discord Server Feed now follows each published trade for three days.
-- The proof/track-record pipeline uses the same horizon so "success" means
-- the same thing on the site and in Discord.

ALTER TABLE futgg_recommendation_outcomes
    DROP CONSTRAINT IF EXISTS futgg_recommendation_outcomes_horizon_check;

ALTER TABLE futgg_recommendation_outcomes
    ADD CONSTRAINT futgg_recommendation_outcomes_horizon_check
    CHECK (horizon IN ('24h', '48h', '72h', '7d'));
