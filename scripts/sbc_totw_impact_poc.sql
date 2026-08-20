-- SBC → TOTW fodder price impact: proof-of-concept event study.
-- Read-only. Run against the PLAYER database (market_events/sbc_challenges/
-- futgg_sales_history all live there). No writes.
--
-- Question: when an SBC requiring "min. 1 TOTW player" releases, what
-- happens to the price of cheap TOTW fodder cards already on the market?
--
-- Method: for each qualifying historical SBC event, take the median sale
-- price of all TOTW-rarity cards released before that event's starts_at,
-- in a "before" window (24h pre-release) and several "after" windows,
-- using futgg_sales_history (NOT the dead legacy bin_history/sales_history
-- tables). Report sample size and spread, not just an average - a single
-- number here would hide whether this is a reliable signal or noise.

-- Step 1: find qualifying events (adjust the requirement-text match to
-- whatever sbc_requirement_parser.py's TOTW detection considers a match -
-- this LIKE is a rough stand-in for a real run, which should call the
-- parser instead of guessing at text patterns).
WITH totw_sbcs AS (
    SELECT DISTINCT e.id AS event_id, e.title, e.starts_at
    FROM market_events e
    JOIN sbc_challenges c ON c.event_id = e.id
    WHERE e.kind = 'sbc'
      AND e.starts_at IS NOT NULL
      AND c.requirements::text ILIKE '%totw%'
),
-- Step 2: the TOTW fodder pool available *at the time* of each event -
-- must have existed (been released) before the SBC started, since a TOTW
-- card that comes out later can't have been fodder for an earlier SBC.
totw_pool AS (
    SELECT p.card_id, p.first_seen_at
    FROM futgg_players p
    WHERE p.rarity = 'totw'
),
before_after AS (
    SELECT
        t.event_id, t.title, t.starts_at,
        (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY s.sold_price)
         FROM futgg_sales_history s JOIN totw_pool tp ON tp.card_id = s.source_card_id
         WHERE tp.first_seen_at < t.starts_at
           AND s.approximate_sold_at BETWEEN t.starts_at - interval '24 hours' AND t.starts_at
        ) AS median_price_before,
        (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY s.sold_price)
         FROM futgg_sales_history s JOIN totw_pool tp ON tp.card_id = s.source_card_id
         WHERE tp.first_seen_at < t.starts_at
           AND s.approximate_sold_at BETWEEN t.starts_at + interval '3 hours' AND t.starts_at + interval '6 hours'
        ) AS median_price_at_3_6h,
        (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY s.sold_price)
         FROM futgg_sales_history s JOIN totw_pool tp ON tp.card_id = s.source_card_id
         WHERE tp.first_seen_at < t.starts_at
           AND s.approximate_sold_at BETWEEN t.starts_at + interval '20 hours' AND t.starts_at + interval '28 hours'
        ) AS median_price_at_24h,
        (SELECT COUNT(*)
         FROM futgg_sales_history s JOIN totw_pool tp ON tp.card_id = s.source_card_id
         WHERE tp.first_seen_at < t.starts_at
           AND s.approximate_sold_at BETWEEN t.starts_at - interval '24 hours' AND t.starts_at + interval '28 hours'
        ) AS sample_size
    FROM totw_sbcs t
)
SELECT
    event_id, title, starts_at, sample_size,
    median_price_before, median_price_at_3_6h, median_price_at_24h,
    ROUND(100.0 * (median_price_at_3_6h - median_price_before) / NULLIF(median_price_before, 0), 1) AS pct_change_3_6h,
    ROUND(100.0 * (median_price_at_24h - median_price_before) / NULLIF(median_price_before, 0), 1) AS pct_change_24h
FROM before_after
WHERE sample_size >= 5   -- discard events with too few sales to trust
ORDER BY starts_at DESC;

-- After running: compute across all qualifying rows -
--   - N (row count)
--   - median of pct_change_24h (not mean - one huge outlier SBC shouldn't
--     dominate the headline number)
--   - % of rows where pct_change_24h > 0
--   - min/max of pct_change_24h (the spread - this is what tells you
--     whether "TOTW prices rise after a TOTW SBC" is a reliable signal
--     or mostly noise with a few big winners)
-- If N < ~10-15 after the sample_size>=5 filter, say so plainly rather
-- than reporting a percentage off a handful of events.
