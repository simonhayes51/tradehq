-- Migration 084: narrow futgg_market_snapshot's sales window from 14 to 7 days
-- target: core
-- requires-table: futgg_players, futgg_bin_history, futgg_sales_history, fut_players
-- requires-column: futgg_players.display_name, futgg_players.full_name, futgg_players.released_at, futgg_players.price_checked_at, futgg_players.card_image_url, futgg_players.card_image_stored_url, futgg_players.card_image_stored_status, futgg_players.pace, futgg_players.card_design_image_url
--
-- WHY NOW, NOT WHEN THIS VIEW WAS FIRST WRITTEN
-- -------------------------------------------------------------------
-- futgg_market_snapshot's refresh was measured at 109-220s per pass
-- (2026-09-07), and EXPLAIN ANALYZE against the exact query behind it
-- traced 26.5 of ~28s to one WindowAgg scanning futgg_sales_history's
-- bounded_sales CTE - a full 14-day window, currently ~3.9M rows.
--
-- That data volume is not static. Commit 22b1b60 (Friday 2026-09-04,
-- 21:38 UTC, this same session, "SalesSync deploy: raise SELECT_LIMIT/
-- lower CYCLE_SLEEP toward actual demand") deliberately raised
-- SalesSync's ingestion ceiling from ~709-720 cards/hour to a
-- ~1,327/hour target - a genuinely correct fix for sales data falling
-- behind demand, but nobody connected it at the time to this view's
-- full-window rescan on every single refresh. Every additional day
-- since that deploy added MORE rows to the 14-day window than the same
-- day would have before it, at ~1.85x the old rate - meaning refresh
-- time was still getting WORSE, day over day, independent of any other
-- change, and would keep doing so until the window fully saturated
-- with the new higher rate around day 14 post-deploy (~2026-09-18).
--
-- Cutting the window to 7 days matches bin_market_signal.py's own
-- OBSERVATION_WINDOW_DAYS (already the retention bound for
-- futgg_bin_history as of migration... see market_data_provider.py's
-- EXCLUDED_PRICE_TIERS comment history) rather than picking a new,
-- unrelated number - one consistent "how much recent history actually
-- matters" answer across the sales and BIN sides of the engine, not two.
--
-- PRODUCT-VISIBLE TRADE-OFF - NOT JUST A SPEED CHANGE
-- -------------------------------------------------------------------
-- sales_count/sales_median/sales_trimmed_mean/sales_low/sales_high on
-- EVERY card's snapshot now reflect the last 7 days instead of 14. This
-- is fresher (more representative of the CURRENT market) but a smaller
-- sample - a card with 20 sales across 14 days but only 11 in the last
-- 7 could drop below futgg_candidate_segments.py's LIQUID_MIN_SALES
-- (25) or MEDIUM_MIN_SALES (8) gates where it previously qualified.
-- Accepted deliberately (2026-09-07) given the alternative was a
-- refresh time actively growing worse every day with no ceiling.
--
-- CASCADE drops fair_value_mv (it selects from futgg_market_snapshot) -
-- restored verbatim below, unchanged, same as migration 083 had to do
-- for the same reason. Do not "clean up" or refactor either definition
-- in a future migration without re-checking it against the live view
-- first.

CREATE INDEX IF NOT EXISTS idx_futgg_bin_history_card_captured
    ON futgg_bin_history (source_card_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_futgg_sales_history_card_sold
    ON futgg_sales_history (source_card_id, approximate_sold_at DESC);

DROP MATERIALIZED VIEW IF EXISTS futgg_market_snapshot CASCADE;

CREATE MATERIALIZED VIEW futgg_market_snapshot AS
WITH latest_bin AS (
    SELECT DISTINCT ON (source_card_id)
        source_card_id,
        lowest_bin            AS current_bin,
        price_range_low,
        price_range_high,
        source_age_text       AS bin_source_age_text,
        captured_at           AS bin_captured_at
    FROM futgg_bin_history
    ORDER BY source_card_id, captured_at DESC
),
bounded_sales AS (
    SELECT source_card_id, sold_price, approximate_sold_at
    FROM (
        SELECT
            source_card_id, sold_price, approximate_sold_at,
            ROW_NUMBER() OVER (
                PARTITION BY source_card_id ORDER BY approximate_sold_at DESC
            ) AS rn
        FROM futgg_sales_history
        -- Was 14 days - see this migration's own header for why 7 now.
        WHERE approximate_sold_at >= now() - interval '7 days'
    ) ranked
    WHERE rn <= 50
),
sales_stats AS (
    SELECT
        source_card_id,
        count(*)                                                       AS sales_count,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY sold_price)         AS sales_median,
        percentile_cont(0.1) WITHIN GROUP (ORDER BY sold_price)         AS sales_p10,
        percentile_cont(0.9) WITHIN GROUP (ORDER BY sold_price)         AS sales_p90,
        min(sold_price)                                                AS sales_low,
        max(sold_price)                                                AS sales_high,
        stddev_pop(sold_price)                                         AS sales_stddev,
        min(approximate_sold_at)                                       AS sales_window_earliest_at,
        max(approximate_sold_at)                                       AS sales_window_latest_at
    FROM bounded_sales
    GROUP BY source_card_id
),
trimmed AS (
    SELECT
        bs.source_card_id,
        avg(bs.sold_price) AS sales_trimmed_mean
    FROM bounded_sales bs
    JOIN sales_stats ss ON ss.source_card_id = bs.source_card_id
    WHERE bs.sold_price BETWEEN ss.sales_p10 AND ss.sales_p90
    GROUP BY bs.source_card_id
),
latest_sale AS (
    SELECT DISTINCT ON (source_card_id)
        source_card_id,
        sold_price       AS latest_sale_price,
        approximate_sold_at AS latest_sale_at
    FROM bounded_sales
    ORDER BY source_card_id, approximate_sold_at DESC
)
SELECT
    p.source_card_id,
    p.source_player_id,
    p.source_slug,
    p.source_url,
    p.game_year,
    p.name,
    p.display_name,
    p.full_name,
    p.released_at,
    p.rating,
    p.primary_position,
    p.alternate_positions,
    p.rarity,
    p.squad,
    p.price_tier,
    p.club,
    p.league,
    p.nation,
    p.club_source_id,
    p.league_source_id,
    p.nation_source_id,
    p.player_image_url,
    p.card_design_image_url,
    p.card_image_url,
    p.card_image_stored_url,
    p.card_image_stored_status,
    p.club_image_url,
    p.league_image_url,
    p.nation_image_url,
    p.pace,
    p.shooting,
    p.passing,
    p.dribbling,
    p.defending,
    p.physicality,
    p.is_active,
    p.is_tradeable,
    p.last_price_status,
    p.metadata_warnings,
    p.price_updated_at,
    p.next_price_due_at,

    lb.current_bin,
    lb.price_range_low,
    lb.price_range_high,
    lb.bin_source_age_text,
    lb.bin_captured_at,
    p.price_checked_at                                   AS bin_checked_at,

    coalesce(ss.sales_count, 0)                          AS sales_count,
    ss.sales_median,
    coalesce(t.sales_trimmed_mean, ss.sales_median)       AS sales_trimmed_mean,
    ss.sales_low,
    ss.sales_high,
    ss.sales_p10,
    ss.sales_p90,
    ss.sales_stddev,
    ls.latest_sale_price,
    ls.latest_sale_at,
    ss.sales_window_earliest_at,
    ss.sales_window_latest_at,
    CASE
        WHEN ss.sales_window_earliest_at IS NOT NULL AND ss.sales_window_latest_at IS NOT NULL
        THEN EXTRACT(EPOCH FROM (ss.sales_window_latest_at - ss.sales_window_earliest_at)) / 60.0
        ELSE NULL
    END                                                   AS sales_window_span_minutes,
    CASE
        WHEN ss.sales_median IS NOT NULL AND ss.sales_median > 0 AND ss.sales_stddev IS NOT NULL
        THEN round((ss.sales_stddev / ss.sales_median)::numeric, 4)
        ELSE NULL
    END                                                   AS sales_dispersion_ratio,
    CASE
        WHEN ss.sales_median IS NOT NULL AND ss.sales_median > 0
             AND ss.sales_p10 IS NOT NULL AND ss.sales_p90 IS NOT NULL
        THEN round(((ss.sales_p90 - ss.sales_p10) / ss.sales_median)::numeric, 4)
        ELSE NULL
    END                                                   AS sales_interdecile_ratio,

    now()                                                 AS snapshot_computed_at
FROM futgg_players p
LEFT JOIN latest_bin lb  ON lb.source_card_id = p.source_card_id
LEFT JOIN sales_stats ss ON ss.source_card_id = p.source_card_id
LEFT JOIN trimmed t      ON t.source_card_id  = p.source_card_id
LEFT JOIN latest_sale ls ON ls.source_card_id = p.source_card_id
WHERE p.is_active
WITH DATA;

-- Required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
CREATE UNIQUE INDEX IF NOT EXISTS idx_futgg_market_snapshot_card
    ON futgg_market_snapshot (source_card_id);

CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_rating
    ON futgg_market_snapshot (rating);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_position
    ON futgg_market_snapshot (primary_position);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_rarity
    ON futgg_market_snapshot (rarity);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_club
    ON futgg_market_snapshot (club);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_league
    ON futgg_market_snapshot (league);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_nation
    ON futgg_market_snapshot (nation);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_current_bin
    ON futgg_market_snapshot (current_bin);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_tradeable
    ON futgg_market_snapshot (is_tradeable) WHERE is_tradeable IS NOT FALSE;
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_name_lower
    ON futgg_market_snapshot (lower(name));

-- ---------------------------------------------------------------------
-- fair_value_mv rebuild - CASCADE-dropped above, restored here VERBATIM
-- (see the comment on the CASCADE line for why this is here at all).
-- Do not "clean up" or refactor this definition in a future migration
-- without re-checking it against the live view first; the whole point of
-- capturing it here is to stop it drifting silently again.
-- ---------------------------------------------------------------------

CREATE MATERIALIZED VIEW fair_value_mv AS
SELECT s.source_card_id AS card_id,
    s.name,
    s.rating,
    s.rarity AS version,
    s.primary_position AS "position",
    COALESCE(a.generated_card_url, s.card_image_stored_url, s.card_image_url, s.player_image_url) AS image_url,
    CASE
        WHEN s.latest_sale_at >= (now() - '24:00:00'::interval) THEN round(s.sales_median)::bigint
        ELSE NULL::bigint
    END AS fair_value_24h,
    CASE
        WHEN s.latest_sale_at >= (now() - '7 days'::interval) THEN round(s.sales_median)::bigint
        ELSE NULL::bigint
    END AS fair_value_7d,
    CASE
        WHEN s.latest_sale_at >= (now() - '24:00:00'::interval) THEN s.sales_count
        ELSE 0::bigint
    END AS sales_24h,
    CASE
        WHEN s.latest_sale_at >= (now() - '7 days'::interval) THEN s.sales_count
        ELSE 0::bigint
    END AS sales_7d,
    CASE
        WHEN s.latest_sale_at >= (now() - '24:00:00'::interval) AND s.sales_window_span_minutes > 0::numeric THEN round(s.sales_count::numeric / GREATEST(s.sales_window_span_minutes / 60.0, 1::numeric), 2)
        ELSE 0::numeric
    END AS sales_per_hour_24h,
    round(s.sales_stddev)::bigint AS volatility_24h,
    s.latest_sale_at AS last_sale_at,
    s.current_bin,
    s.bin_captured_at,
    CASE
        WHEN s.current_bin IS NOT NULL AND s.sales_median > 0::double precision AND s.latest_sale_at >= (now() - '24:00:00'::interval) THEN round(((s.sales_median - s.current_bin::double precision) / s.sales_median * 100::double precision)::numeric, 2)
        ELSE NULL::numeric
    END AS discount_pct,
    CASE
        WHEN s.current_bin IS NOT NULL AND COALESCE(s.sales_stddev, 0::numeric) > 0::numeric AND s.latest_sale_at >= (now() - '24:00:00'::interval) THEN round(((s.current_bin::double precision - s.sales_median) / s.sales_stddev::double precision)::numeric, 2)
        ELSE NULL::numeric
    END AS bin_zscore_24h,
    false AS trend_falling,
    s.current_bin IS NULL OR s.sales_median IS NULL OR s.sales_count < 5 OR s.latest_sale_at IS NULL OR s.latest_sale_at < (now() - '7 days'::interval) AS data_quality_suspect,
    s.snapshot_computed_at AS computed_at,
    a.generated_card_url,
    a.generated_card_status,
    a.generated_card_error
   FROM futgg_market_snapshot s
     LEFT JOIN fut_players a ON a.card_id = s.source_card_id
  WHERE s.source_card_id IS NOT NULL;

-- Indexes matched to the last tracked fair_value_mv definition
-- (migration 036) - CASCADE dropped these along with the view above, so
-- they must be recreated here rather than assumed to survive.
CREATE UNIQUE INDEX IF NOT EXISTS idx_fair_value_mv_card
    ON fair_value_mv (card_id);
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_discount
    ON fair_value_mv (discount_pct DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_suspect
    ON fair_value_mv (data_quality_suspect) WHERE data_quality_suspect;
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_falling
    ON fair_value_mv (trend_falling) WHERE trend_falling;
