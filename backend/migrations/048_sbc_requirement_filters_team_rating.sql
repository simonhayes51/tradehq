-- Migration 048: team_rating_min on sbc_requirement_filters
-- target: core
-- requires-table: sbc_requirement_filters
--
-- SBC relevance model revision (production-readiness review): the parser
-- (app/services/sbc_requirement_parser.py) previously discarded "Team
-- Rating: Min. 89"-shaped lines entirely (confirmed real EasySBC text -
-- see auto_sync/easysbc_sbc_sync.py's own module docstring) rather than
-- risk conflating a whole-squad-average constraint with a per-card
-- rating threshold. It now captures it into this new, deliberately
-- separate column - never used as a card-matching filter dimension
-- (there is no single card that "is" a team rating requirement; a user
-- solves an 88-rated squad with many different individual ratings, not
-- 11 cards each rated >=88), only ever surfaced as a MIN_TEAM_RATING
-- resource-demand signal (see migration 049's sbc_resource_demand and
-- app/services/sbc_demand_classifier.py).

ALTER TABLE sbc_requirement_filters
    ADD COLUMN IF NOT EXISTS team_rating_min INT;
