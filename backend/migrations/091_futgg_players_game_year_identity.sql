-- Migration 091: preserve FUT.GG cards across game cycles
-- target: core
-- requires-table: futgg_players
--
-- FUT.GG source_card_id is not a safe cross-cycle identity. An FC27
-- catalogue import must be able to insert a card with the same
-- source_card_id as an FC26 card without overwriting the FC26 row.
-- Move futgg_players to the actual natural key: (game_year,
-- source_card_id).

DO $$
DECLARE
    old_pk_name text;
    any_pk_name text;
BEGIN
    SELECT conname INTO old_pk_name
    FROM pg_constraint
    WHERE conrelid = 'futgg_players'::regclass
      AND contype = 'p'
      AND conkey = ARRAY[
          (SELECT attnum FROM pg_attribute WHERE attrelid = 'futgg_players'::regclass AND attname = 'source_card_id')
      ];

    IF old_pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE futgg_players DROP CONSTRAINT %I CASCADE', old_pk_name);
    END IF;

    SELECT conname INTO any_pk_name
    FROM pg_constraint
    WHERE conrelid = 'futgg_players'::regclass
      AND contype = 'p';

    IF any_pk_name IS NULL THEN
        ALTER TABLE futgg_players
            ALTER COLUMN source_card_id SET NOT NULL,
            ALTER COLUMN game_year SET NOT NULL;

        ALTER TABLE futgg_players
            ADD CONSTRAINT futgg_players_pkey PRIMARY KEY (game_year, source_card_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS futgg_players_source_card_idx
    ON futgg_players (source_card_id);
