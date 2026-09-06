-- Migration 083: scan-recency cursor for the FUT.GG segmented candidate scan
-- target: core
--
-- futgg_candidate_segments.py's Segment.refresh_minutes has always
-- documented itself as "how often a card in this segment should be
-- re-evaluated" but select_candidates_by_segment() never actually applied
-- it - each segment query is a bare ORDER BY ... LIMIT N with no filter on
-- when a card was last looked at. Every pass therefore re-selects the same
-- deterministic top-N rows per segment (highest sales_count, most recent
-- latest_sale_at, etc.), which barely change 10 minutes apart. Production
-- logs showed evaluated=~1330 out of a ~16,000-card pool, pass after pass:
-- coverage=8.3% forever, with the other 92% of the catalogue never
-- evaluated at all - not under-weighted, structurally invisible, the same
-- failure this segmentation was originally built to fix for the old single
-- "ORDER BY sales_count DESC LIMIT 500" query (see that module's docstring).
--
-- This table is the missing "last looked at" record so a segment query can
-- actually skip a card it evaluated too recently and let a different slice
-- of the pool surface instead. It intentionally covers every card the scan
-- evaluates, not just the ones that produced a persisted recommendation
-- (buy/strong_buy/watch) - an "avoid"/"insufficient_data" result is still
-- an evaluation and must still count toward "recently looked at", or the
-- rotation would only ever skip past the small slice that already clears
-- the bar and starve on everything else exactly as before.

CREATE TABLE IF NOT EXISTS futgg_scan_cursor (
    source_card_id      BIGINT PRIMARY KEY,
    last_evaluated_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS futgg_scan_cursor_last_evaluated_idx
    ON futgg_scan_cursor (last_evaluated_at);
