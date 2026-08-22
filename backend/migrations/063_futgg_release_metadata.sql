-- Migration 063: FUT.GG release timestamp + display/full name on futgg_players
-- target: core
-- requires-table: futgg_players
--
-- WHY THESE COLUMNS
-- -----------------
-- released_at is FUT.GG's own "Added On" value from the Player
-- Information panel - when the CARD was added to the game. This is
-- deliberately NOT futgg_players.first_seen_at, which records only when
-- OUR scraper first discovered the card and is therefore useless for
-- reconstructing a content-release timeline (a card added to the game in
-- September but first crawled by us in November has a first_seen_at of
-- November). Both are kept; neither is repurposed.
--
-- TIMEZONE SEMANTICS (important - see auto_sync/futgg_common.py's
-- parse_added_on docstring for the full reasoning):
-- The rendered string ("Mar 18, 2026, 6:00 PM") carries no timezone.
-- auto_sync/futgg_player_sync.py therefore PINS the Playwright browser
-- context to timezone_id=FUTGG_BROWSER_TIMEZONE (default "UTC"), so if
-- FUT.GG renders that value client-side in the viewer's local zone - the
-- common pattern - the scraped string is UTC by construction rather than
-- by assumption. released_at_raw preserves the exact source string so
-- that if FUT.GG is later shown to render in a different fixed zone,
-- every row can be re-derived without re-scraping the catalogue.
--
-- NAMING
-- ------
-- futgg_players.name is left EXACTLY as it was - the Player Information
-- "Name" row, which FUT.GG renders as the player's full legal name
-- ("Bruno Miguel Borges Fernandes"). 97 call sites across the backend
-- read that column and there is a lower(name) search index on
-- futgg_market_snapshot, so renaming it would be a breaking change for
-- no benefit. Instead:
--   full_name    - an explicit copy of the same value, so the semantics
--                  are legible without knowing the column's history.
--   display_name - the SHORT name from FUT.GG's own player-profile anchor
--                  ("Bruno Fernandes"), which is what users want to read.
-- User-facing surfaces read COALESCE(display_name, name), so a row whose
-- display name has not been backfilled yet degrades to today's behaviour
-- rather than to NULL.

ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS display_name     TEXT;
ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS full_name        TEXT;
ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS released_at      TIMESTAMPTZ;
ALTER TABLE futgg_players ADD COLUMN IF NOT EXISTS released_at_raw  TEXT;

-- Chronological release queries per game cycle - the access pattern the
-- promo/event reconstruction (migration 064) is built on. Partial: a row
-- with no release timestamp can never satisfy these queries, and the
-- whole point of the backfill is that a large share of rows start NULL.
CREATE INDEX IF NOT EXISTS idx_futgg_players_game_year_released_at
    ON futgg_players (game_year, released_at)
    WHERE released_at IS NOT NULL;

-- Event derivation groups by (game_year, squad, released_at) - see
-- migration 064's own comment on why squad+timestamp is a stronger
-- grouping key than rarity alone.
CREATE INDEX IF NOT EXISTS idx_futgg_players_squad_release
    ON futgg_players (game_year, squad, released_at)
    WHERE released_at IS NOT NULL AND squad IS NOT NULL;

-- The backfill's own target-selection predicate (see
-- cards_needing_release_metadata). Partial so the index only covers rows
-- that still need work and shrinks to nothing as the backfill completes.
CREATE INDEX IF NOT EXISTS idx_futgg_players_needs_release_backfill
    ON futgg_players (game_year, first_seen_at)
    WHERE released_at IS NULL OR display_name IS NULL OR full_name IS NULL;

COMMENT ON COLUMN futgg_players.released_at IS
    'FUT.GG "Added On" - when the CARD was added to the game. Distinct from first_seen_at (when our scraper discovered it). Timezone: scraped with the browser pinned to UTC; see released_at_raw for the unparsed source string.';
COMMENT ON COLUMN futgg_players.released_at_raw IS
    'Exact unparsed FUT.GG "Added On" string, retained so released_at can be re-derived if the source timezone is later shown to differ from the assumed one.';
COMMENT ON COLUMN futgg_players.display_name IS
    'Short user-facing name from FUT.GG''s player-profile anchor (e.g. "Bruno Fernandes"). User-facing surfaces read COALESCE(display_name, name).';
COMMENT ON COLUMN futgg_players.full_name IS
    'Full name from the FUT.GG Player Information panel (e.g. "Bruno Miguel Borges Fernandes") - the same value the legacy `name` column holds.';
