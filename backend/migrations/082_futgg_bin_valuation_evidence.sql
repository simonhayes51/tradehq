-- Migration 082: BIN-history fallback evidence columns
-- target: core
-- requires-table: futgg_recommendation_snapshots
--
-- Purely additive: app/services/futgg_bin_valuation.py adds a separate,
-- explicitly lower-confidence valuation path used ONLY when completed-
-- sales evidence is unavailable or stale (see that module's own
-- docstring). These columns record, for every persisted recommendation
-- snapshot, which path produced it and - when it was the BIN path - the
-- evidence behind that estimate, so a later analysis or audit can always
-- tell a confirmed sales-derived call from an estimated BIN-derived one
-- without re-deriving it from raw history.
--
-- DEFAULTs exist only so a backfilled row from before this migration
-- reads as "completed_sales / confirmed" (i.e. exactly what it always
-- was) rather than NULL - every row written from here on always sets
-- these explicitly (see futgg_recommendation_store.py's _INSERT).

ALTER TABLE futgg_recommendation_snapshots
    ADD COLUMN IF NOT EXISTS valuation_source TEXT NOT NULL DEFAULT 'completed_sales',
    ADD COLUMN IF NOT EXISTS evidence_quality TEXT NOT NULL DEFAULT 'confirmed',
    ADD COLUMN IF NOT EXISTS bin_sample_count INTEGER,
    ADD COLUMN IF NOT EXISTS bin_history_span_hours NUMERIC,
    ADD COLUMN IF NOT EXISTS bin_reference_price NUMERIC,
    ADD COLUMN IF NOT EXISTS bin_trend_direction TEXT,
    ADD COLUMN IF NOT EXISTS bin_volatility NUMERIC,
    ADD COLUMN IF NOT EXISTS evidence_age_minutes INTEGER;

CREATE INDEX IF NOT EXISTS futgg_rec_snap_valuation_source_idx
    ON futgg_recommendation_snapshots (valuation_source, evaluated_at DESC);
