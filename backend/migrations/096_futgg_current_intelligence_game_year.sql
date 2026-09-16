-- Migration 096: scope futgg_current_intelligence to (game_year, source_card_id)
-- target: core
-- requires-table: futgg_current_intelligence
--
-- futgg_current_intelligence (migration 080) is keyed by source_card_id
-- alone. A source_card_id can collide across game years (confirmed live
-- for ordinary cards, not just Icons - see migration 095's Frida Maanum
-- case), so every read-side join of this table against futgg_players/
-- futgg_market_snapshot on source_card_id alone fans out: one scanner
-- evaluation of, say, the FC26 card gets joined to BOTH the FC26 row and
-- the unrelated FC27 row sharing that numeric id, showing the FC27 card
-- with the FC26 card's signal/confidence/buy_below - and vice versa. This
-- is the same bug class as migration 095, in a second table.
--
-- futgg_current_intelligence is documented as disposable mutable current
-- state ("every scanner evaluation ... replaces the prior row" - see its
-- own module docstring), fully repopulated by the next scan pass. There is
-- no way to recover which historical rows were genuinely evaluated against
-- an FC26 vs FC27 card, so rather than guess, this truncates the table:
-- the scanner's next cycle repopulates it, now correctly scoped, with
-- zero risk of mislabeling a stale row.

ALTER TABLE futgg_current_intelligence
    ADD COLUMN IF NOT EXISTS game_year SMALLINT;

TRUNCATE TABLE futgg_current_intelligence;

ALTER TABLE futgg_current_intelligence
    ALTER COLUMN game_year SET NOT NULL,
    ALTER COLUMN game_year SET DEFAULT 27;

DO $$
DECLARE
    old_pk_name text;
BEGIN
    SELECT conname INTO old_pk_name
    FROM pg_constraint
    WHERE conrelid = 'futgg_current_intelligence'::regclass
      AND contype = 'p';

    IF old_pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE futgg_current_intelligence DROP CONSTRAINT %I', old_pk_name);
    END IF;

    ALTER TABLE futgg_current_intelligence
        ADD CONSTRAINT futgg_current_intelligence_pkey PRIMARY KEY (game_year, source_card_id);
END
$$;

-- futgg_intelligence_transitions (migration 081) has the identical
-- problem: it's an append-only audit log keyed by source_card_id, so a
-- transition row cannot be reliably attributed after the fact either.
-- Add game_year so future transitions are attributable; historical rows
-- are left as-is (it's an audit log, not a current-state table - we do
-- not truncate history).
ALTER TABLE futgg_intelligence_transitions
    ADD COLUMN IF NOT EXISTS game_year SMALLINT NOT NULL DEFAULT 27;
