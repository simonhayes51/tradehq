-- Repair for the SalesSync data_403 incident (production, Coolify,
-- started 2026-08-04 18:56 UTC).
--
-- WHAT HAPPENED
-- -------------
-- The pre-fix worker (see futgg_sales_sync.py's apply_batch_results, git
-- history before the fix this file ships alongside) unconditionally ran
--
--     UPDATE futgg_players
--     SET next_sales_due_at = NOW() + (5 * INTERVAL '1 minute'),
--         last_sales_status = 'error:' || <error>
--     WHERE source_card_id = <card>
--
-- for EVERY failed fetch that wasn't a bare 429/challenge_required -
-- including data_403 and sign_502, the two errors actually observed.
-- This silently converted "never read" (next_sales_due_at IS NULL) into
-- "read, but failed" for every never-read card that happened to be
-- selected during the incident, and pushed every already-read card's due
-- date out by 5 minutes despite zero genuine sales data being written.
-- The observed symptom: cards_ok=0, sales=0, but the never_read count
-- kept falling by (up to) 200 every cycle.
--
-- WHAT THIS REPAIR DOES
-- ----------------------
-- Resets next_sales_due_at/last_sales_status back to NULL (their
-- pre-incident default: "never read" for a card that genuinely was, or
-- simply "due now" for a previously-read card whose last real read is
-- untouched by this reset - only the SCHEDULE fields are cleared, never
-- futgg_sales_history) for rows whose CURRENT last_sales_status is an
-- error AND whose write happened during the incident window.
--
-- SCOPING CAVEAT - READ BEFORE RUNNING
-- --------------------------------------
-- This database predates the last_sales_status_at column the fix adds
-- (ensure_schema() ALTER ... ADD COLUMN IF NOT EXISTS), so there is no
-- exact "when was this status written" timestamp for rows written before
-- the fix is deployed. This repair instead reverses the OLD code's
-- hardcoded 5-minute offset: any row where (next_sales_due_at -
-- 5 minutes) falls at/after the incident start must have been written by
-- that old 5-minutes-flat code path. This is exact ONLY if run before
-- the NEW code (which uses a different, configurable
-- FUTGG_SALES_FAILURE_RETRY_BACKOFF_MINUTES, default 2 - not 5) has had
-- a chance to write its own new error rows that would also satisfy the
-- ">= incident start" test. Run this promptly right after redeploying
-- the fix, before many new cycles accumulate, to keep that window small.
-- Any FUTURE incident, once this fix is live, can be scoped exactly via
-- last_sales_status_at instead - no such caveat needed then.
--
-- Does NOT touch any row whose last_sales_status = 'success' - a
-- genuinely successful fetch, whether or not it returned any sales that
-- cycle, is never touched. Does NOT touch futgg_sales_history - no sales
-- data is deleted or modified, only the futgg_players scheduling columns.
--
-- Run manually. Not applied automatically by any migration or worker.

-- ---------------------------------------------------------------------
-- STEP 1 - PREVIEW. Run this first and eyeball the row count/sample
-- before running the UPDATE below.
-- ---------------------------------------------------------------------
SELECT
    source_card_id,
    rating,
    last_sales_status,
    next_sales_due_at,
    (next_sales_due_at - interval '5 minutes') AS reconstructed_write_time
FROM futgg_players
WHERE last_sales_status LIKE 'error:%'
  AND (next_sales_due_at - interval '5 minutes') >= '2026-08-04 18:56:00+00'::timestamptz
ORDER BY next_sales_due_at;

-- ---------------------------------------------------------------------
-- STEP 2 - THE REPAIR. Only run after checking the preview above looks
-- right (row count in the ballpark of what the incident affected, no
-- unexpected cards, no last_sales_status = 'success' rows present).
-- ---------------------------------------------------------------------
UPDATE futgg_players
SET next_sales_due_at = NULL,
    last_sales_status = NULL,
    last_sales_status_at = NULL
WHERE last_sales_status LIKE 'error:%'
  AND (next_sales_due_at - interval '5 minutes') >= '2026-08-04 18:56:00+00'::timestamptz;

-- ---------------------------------------------------------------------
-- STEP 3 - VERIFY. Row count here should be 0 (everything matching the
-- incident window has been reset back to NULL/never-read).
-- ---------------------------------------------------------------------
SELECT count(*)
FROM futgg_players
WHERE last_sales_status LIKE 'error:%'
  AND (next_sales_due_at - interval '5 minutes') >= '2026-08-04 18:56:00+00'::timestamptz;
