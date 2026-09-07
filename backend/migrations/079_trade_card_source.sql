-- Migration 079: make the catalogue behind trades.card_id explicit
-- target: core
--
-- FUT.GG source_card_id and legacy FUTBIN fut_players.card_id are different
-- identifier spaces. A bare numeric trades.card_id therefore cannot safely
-- tell a caller which catalogue to query. This ambiguity caused the live
-- held-position refresher to skip valid FUT.GG positions while looking only
-- in legacy fut_players.
--
-- Existing rows are deliberately NOT bulk-backfilled here. The legacy player
-- catalogue lives behind PLAYER_DATABASE_URL, not the core database, and a
-- numeric id can only be classified safely by checking the appropriate source.
-- Guessing 'futgg' for every historical row would turn an explicit ambiguity
-- into silently wrong data. Current writers set card_source at creation time;
-- old NULL rows remain compatible with the source-probing fallback until they
-- are classified by a dedicated audited backfill.

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS card_source TEXT;

ALTER TABLE trades
    DROP CONSTRAINT IF EXISTS trades_card_source_check;

ALTER TABLE trades
    ADD CONSTRAINT trades_card_source_check
    CHECK (card_source IS NULL OR card_source IN ('futgg', 'futbin'));

CREATE INDEX IF NOT EXISTS trades_open_card_source_idx
    ON trades (card_source, card_id)
    WHERE status = 'open' AND card_id IS NOT NULL;

COMMENT ON COLUMN trades.card_source IS
    'Catalogue namespace for card_id: futgg = futgg source_card_id, futbin = legacy fut_players.card_id. NULL means historical/unclassified; never infer a source from the number alone.';
