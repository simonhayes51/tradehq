-- backend/scripts/sql/fc26_release_price_rises_audit.sql
--
-- Read-only audit for the Launch Movers page / GET /api/v2/release-price-
-- rises (backend/app/routers/v2/release_price_rises.py). Answers three
-- questions before you trust what the page shows:
--
--   1. Do we actually have priced data near the early/full dates?
--   2. How much of it is flagged low-confidence by the chart-reconstruction
--      loader (auto_sync/research_fc26_historical_loader.py), and therefore
--      excluded from the page by default?
--   3. Which specific cards produce the most extreme (and least trustworthy)
--      % swings - i.e. the "anomalies" a spectacular-looking row is usually
--      one of?
--
-- Nothing here writes anything.
--
-- Usage (defaults to FC26's confirmed launch dates - override with -v):
--   psql "$DATABASE_URL" -f backend/scripts/sql/fc26_release_price_rises_audit.sql
--   psql "$DATABASE_URL" \
--     -v early_date="'2025-09-19'" -v full_date="'2025-09-26'" -v window_days=5 \
--     -f backend/scripts/sql/fc26_release_price_rises_audit.sql

\if :{?early_date}
\else
  \set early_date '''2025-09-19'''
\endif
\if :{?full_date}
\else
  \set full_date '''2025-09-26'''
\endif
\if :{?window_days}
\else
  \set window_days 5
\endif
\if :{?game_year}
\else
  \set game_year 26
\endif

\echo '================================================================='
\echo '1. OVERALL COVERAGE near the early/full dates'
\echo '================================================================='

SELECT
    (:early_date)::date                                 AS early_date,
    (:full_date)::date                                   AS full_date,
    count(*) FILTER (WHERE h.observed_date BETWEEN (:early_date)::date
                                                 AND (:early_date)::date + (:window_days || ' days')::interval)
                                                          AS rows_near_early,
    count(DISTINCT h.card_id) FILTER (WHERE h.observed_date BETWEEN (:early_date)::date
                                                               AND (:early_date)::date + (:window_days || ' days')::interval)
                                                          AS cards_near_early,
    count(*) FILTER (WHERE h.observed_date BETWEEN (:full_date)::date
                                                 AND (:full_date)::date + (:window_days || ' days')::interval)
                                                          AS rows_near_full,
    count(DISTINCT h.card_id) FILTER (WHERE h.observed_date BETWEEN (:full_date)::date
                                                               AND (:full_date)::date + (:window_days || ' days')::interval)
                                                          AS cards_near_full
FROM fc26_historical_daily_prices h
JOIN futgg_players p ON p.source_card_id = h.card_id
WHERE p.game_year = :game_year;

\echo ''
\echo 'Cards with a price near BOTH dates (the population the page can '
\echo 'actually rank) vs. total cards priced anywhere in the season:'

WITH near_early AS (
    SELECT DISTINCT card_id FROM fc26_historical_daily_prices
    WHERE observed_date BETWEEN (:early_date)::date AND (:early_date)::date + (:window_days || ' days')::interval
),
near_full AS (
    SELECT DISTINCT card_id FROM fc26_historical_daily_prices
    WHERE observed_date BETWEEN (:full_date)::date AND (:full_date)::date + (:window_days || ' days')::interval
),
any_priced AS (
    SELECT DISTINCT card_id FROM fc26_historical_daily_prices
)
SELECT
    (SELECT count(*) FROM any_priced) AS priced_anywhere,
    (SELECT count(*) FROM near_early) AS priced_near_early,
    (SELECT count(*) FROM near_full)  AS priced_near_full,
    (SELECT count(*) FROM near_early JOIN near_full USING (card_id)) AS priced_near_both;

\echo ''
\echo '================================================================='
\echo '2. DATA QUALITY - what the endpoint excludes by default'
\echo '   (status != success, calibration_max_pct_err > 20, or any '
\echo '   tick_warnings - see fc26_historical_extraction_runs)'
\echo '================================================================='

SELECT
    count(*)                                                        AS total_extraction_runs,
    count(*) FILTER (WHERE status = 'success')                      AS status_success,
    count(*) FILTER (WHERE status != 'success')                     AS status_not_success,
    count(*) FILTER (WHERE calibration_max_pct_err > 20)             AS high_calibration_error,
    count(*) FILTER (WHERE cardinality(tick_warnings) > 0)           AS has_tick_warnings,
    count(*) FILTER (
        WHERE status = 'success'
          AND (calibration_max_pct_err IS NULL OR calibration_max_pct_err <= 20)
          AND cardinality(tick_warnings) = 0
    )                                                                AS passes_default_filter
FROM fc26_historical_extraction_runs r
JOIN futgg_players p ON p.source_card_id = r.card_id
WHERE p.game_year = :game_year;

\echo ''
\echo '================================================================='
\echo '3. LIKELY ANOMALIES: biggest % swings among the LOW-CONFIDENCE'
\echo '   cards (these are exactly what include_uncertain=false hides)'
\echo '================================================================='

WITH nearest AS (
    SELECT DISTINCT ON (h.card_id, bucket)
        h.card_id, bucket, h.observed_date, h.price
    FROM fc26_historical_daily_prices h
    CROSS JOIN LATERAL (VALUES ('early', (:early_date)::date), ('full', (:full_date)::date)) AS t(bucket, target_date)
    WHERE h.observed_date BETWEEN t.target_date AND t.target_date + (:window_days || ' days')::interval
    ORDER BY h.card_id, bucket, h.observed_date ASC
),
paired AS (
    SELECT
        e.card_id, e.price AS early_price, e.observed_date AS early_observed,
        f.price AS full_price, f.observed_date AS full_observed
    FROM nearest e
    JOIN nearest f ON f.card_id = e.card_id AND f.bucket = 'full'
    WHERE e.bucket = 'early' AND e.price > 0
)
SELECT
    p.source_card_id, coalesce(p.display_name, p.name) AS name, p.rating, p.primary_position,
    pr.early_price, pr.early_observed, pr.full_price, pr.full_observed,
    round(100.0 * (pr.full_price - pr.early_price) / pr.early_price, 1) AS pct_change,
    r.status AS extraction_status, r.calibration_max_pct_err, r.tick_warnings
FROM paired pr
JOIN futgg_players p ON p.source_card_id = pr.card_id
LEFT JOIN fc26_historical_extraction_runs r ON r.card_id = pr.card_id
WHERE p.game_year = :game_year
  AND (
        r.status IS DISTINCT FROM 'success'
        OR r.calibration_max_pct_err > 20
        OR cardinality(r.tick_warnings) > 0
      )
ORDER BY abs(pr.full_price - pr.early_price) * 1.0 / greatest(pr.early_price, 1) DESC
LIMIT 25;

\echo ''
\echo 'Audit complete - read-only, nothing was written or changed.'
