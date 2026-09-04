-- Migration 081: shadow-mode comparison log for the triggered
-- detailed-sales queue
-- target: core
-- requires-table: futgg_players
--
-- Part 6 of the FUT.GG price-detail infrastructure work: run
-- futgg_sales_triggers.select_triggered() (Part 5) alongside the live
-- select_due() every cycle WITHOUT changing what futgg_sales_sync.py
-- actually fetches, and record enough per-cycle detail to answer the
-- capacity/targeting questions after a representative interval has run.
--
-- One row per cycle, not per card - a per-card event log at this
-- worker's cadence is exactly the write-volume problem
-- futgg_bulk_price_sync.py's whole existence already fixed for the bulk
-- feed (see that file's own module docstring: 14.8M rows, 269-second
-- checkpoints). successful_card_ids is a small array (bounded by
-- SELECT_LIMIT, ~200) so cross-cycle dedup for a genuine "unique cards
-- refreshed per wall-clock hour" figure is cheap without needing a
-- second per-card table.
-- due_count_by_tier/triggered_card_ids let the interval report compute
-- "backlog by priority" and true cross-cycle unique-card dedup (a card
-- can legitimately reappear in many consecutive cycles - most trigger
-- reasons are NOT consumed the way futgg_price_change_events/futgg_
-- sales_priority_requests are, e.g. a popular watchlist card stays a
-- watchlist hit every cycle until it's actually fetched) without a
-- second per-card table - both arrays stay small (bounded by
-- SELECT_LIMIT/TRIGGERED_SELECT_LIMIT per cycle, a couple hundred rows
-- at most), unlike the per-card-per-cycle write volume this codebase
-- has already had to fix once (see futgg_bulk_price_sync.py).
CREATE TABLE IF NOT EXISTS futgg_sales_shadow_log (
    id                   BIGSERIAL PRIMARY KEY,
    cycle_started_at     TIMESTAMPTZ NOT NULL,
    cycle_ended_at       TIMESTAMPTZ NOT NULL,
    due_count            INT NOT NULL,
    due_count_by_tier    JSONB NOT NULL DEFAULT '{}'::jsonb,
    triggered_count      INT NOT NULL,
    triggered_new_count  INT NOT NULL,
    triggered_card_ids   BIGINT[] NOT NULL DEFAULT '{}'::bigint[],
    reason_counts        JSONB NOT NULL DEFAULT '{}'::jsonb,
    successful_reads     INT NOT NULL,
    failed_reads         INT NOT NULL,
    successful_card_ids  BIGINT[] NOT NULL DEFAULT '{}'::bigint[],
    throttled_batches    INT NOT NULL DEFAULT 0,
    rate_limited_count   INT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_futgg_sales_shadow_log_started
    ON futgg_sales_shadow_log (cycle_started_at);
