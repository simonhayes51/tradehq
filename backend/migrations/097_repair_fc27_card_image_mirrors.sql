-- Migration 097: repair FC27 card image mirrors after the game-year split
-- target: core
-- requires-table: futgg_players
--
-- The mirror worker previously addressed futgg_players by source_card_id
-- alone and stored every cycle under an fc26/ key. Once source_card_id
-- became reusable across game years, that could copy FC26 artwork onto
-- the FC27 row. Clear FC27 mirror metadata so the game-year-aware worker
-- can rebuild it under fc27/ without touching preserved FC26 artwork.

UPDATE futgg_players
SET card_image_stored_url = NULL,
    card_image_stored_key = NULL,
    card_image_stored_hash = NULL,
    card_image_stored_at = NULL,
    card_image_stored_status = NULL,
    card_image_stored_error = NULL
WHERE game_year = 27
  AND (
      card_image_stored_key LIKE 'fc26/futgg-card-images/%'
      OR card_image_stored_url LIKE '%/fc26/futgg-card-images/%'
  );
