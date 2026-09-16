-- Migration 094: pin futgg_market_snapshot to platform='ps5'
-- target: core
-- requires-table: futgg_players, futgg_bin_history, futgg_sales_history, fut_players
-- requires-column: futgg_players.display_name, futgg_players.full_name, futgg_players.released_at, futgg_players.price_checked_at, futgg_players.card_image_url, futgg_players.card_image_stored_url, futgg_players.card_image_stored_status, futgg_players.pace, futgg_players.card_design_image_url, futgg_bin_history.platform, futgg_sales_history.platform
--
-- Migration 093 added `platform` to futgg_bin_history/futgg_sales_history
-- so a second worker instance (FUTGG_BULK_PLATFORM=pc /
-- FUTGG_SALES_PLATFORM=pc) can safely record PC prices/sales alongside
-- the existing ps5 data without the two overwriting each other's rows.
--
-- This view's latest_bin/bounded_sales CTEs do not know that yet: each
-- picks "the newest row for this source_card_id" with no platform
-- filter at all, so once PC rows exist they compete on equal footing
-- with ps5 rows for "most recent" - current_bin/sales_* would silently
-- flip between platforms depending on which worker happened to write
-- last, corrupting the live product (Opportunities, search, the
-- Discord feed - everything - still assumes one price per card).
--
-- Fix: filter both CTEs to platform = 'ps5' explicitly. This is a
-- deliberate, temporary pin - not a design decision that PC doesn't
-- matter - it keeps the live product's behaviour IDENTICAL to before
-- migration 093 while PC data is captured in the background. Surfacing
-- PC prices in the product (a platform toggle, per-platform columns on
-- this view, etc.) is separate follow-up work; this migration's only
-- job is "don't let recording PC data change what ps5-only readers see
-- today."
--
-- Query shape and columns are otherwise unchanged from migration 092.

DROP MATERIALIZED VIEW IF EXISTS futgg_market_snapshot CASCADE;

CREATE MATERIALIZED VIEW futgg_market_snapshot AS
WITH active_players AS MATERIALIZED (
    SELECT *
    FROM futgg_players
    WHERE is_active
),
latest_bin AS (
    SELECT
        p.game_year,
        p.source_card_id,
        b.current_bin,
        b.price_range_low,
        b.price_range_high,
        b.bin_source_age_text,
        b.bin_captured_at
    FROM active_players p
    LEFT JOIN LATERAL (
        SELECT
            h.lowest_bin      AS current_bin,
            h.price_range_low,
            h.price_range_high,
            h.source_age_text AS bin_source_age_text,
            h.captured_at     AS bin_captured_at
        FROM futgg_bin_history h
        WHERE h.source_card_id = p.source_card_id
          AND h.platform = 'ps5'
        ORDER BY h.captured_at DESC
        LIMIT 1
    ) b ON TRUE
),
bounded_sales AS (
    SELECT
        p.game_year,
        p.source_card_id,
        s.sold_price,
        s.approximate_sold_at
    FROM active_players p
    CROSS JOIN LATERAL (
        SELECT
            h.sold_price,
            h.approximate_sold_at
        FROM futgg_sales_history h
        WHERE h.source_card_id = p.source_card_id
          AND h.platform = 'ps5'
          AND h.approximate_sold_at >= now() - interval '7 days'
        ORDER BY h.approximate_sold_at DESC
        LIMIT 50
    ) s
),
sales_stats AS (
    SELECT
        game_year,
        source_card_id,
        count(*)                                                        AS sales_count,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY sold_price)          AS sales_median,
        percentile_cont(0.1) WITHIN GROUP (ORDER BY sold_price)          AS sales_p10,
        percentile_cont(0.9) WITHIN GROUP (ORDER BY sold_price)          AS sales_p90,
        min(sold_price)                                                  AS sales_low,
        max(sold_price)                                                  AS sales_high,
        stddev_pop(sold_price)                                           AS sales_stddev,
        min(approximate_sold_at)                                         AS sales_window_earliest_at,
        max(approximate_sold_at)                                         AS sales_window_latest_at
    FROM bounded_sales
    GROUP BY game_year, source_card_id
),
trimmed AS (
    SELECT
        bs.game_year,
        bs.source_card_id,
        avg(bs.sold_price) AS sales_trimmed_mean
    FROM bounded_sales bs
    JOIN sales_stats ss ON ss.game_year = bs.game_year AND ss.source_card_id = bs.source_card_id
    WHERE bs.sold_price BETWEEN ss.sales_p10 AND ss.sales_p90
    GROUP BY bs.game_year, bs.source_card_id
),
latest_sale AS (
    SELECT DISTINCT ON (game_year, source_card_id)
        game_year,
        source_card_id,
        sold_price          AS latest_sale_price,
        approximate_sold_at AS latest_sale_at
    FROM bounded_sales
    ORDER BY game_year, source_card_id, approximate_sold_at DESC
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
    p.price_checked_at                                    AS bin_checked_at,

    coalesce(ss.sales_count, 0)                           AS sales_count,
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
    END                                                    AS sales_window_span_minutes,
    CASE
        WHEN ss.sales_median IS NOT NULL AND ss.sales_median > 0 AND ss.sales_stddev IS NOT NULL
        THEN round((ss.sales_stddev / ss.sales_median)::numeric, 4)
        ELSE NULL
    END                                                    AS sales_dispersion_ratio,
    CASE
        WHEN ss.sales_median IS NOT NULL AND ss.sales_median > 0
             AND ss.sales_p10 IS NOT NULL AND ss.sales_p90 IS NOT NULL
        THEN round(((ss.sales_p90 - ss.sales_p10) / ss.sales_median)::numeric, 4)
        ELSE NULL
    END                                                    AS sales_interdecile_ratio,

    now()                                                  AS snapshot_computed_at
FROM active_players p
LEFT JOIN latest_bin lb  ON lb.game_year = p.game_year AND lb.source_card_id = p.source_card_id
LEFT JOIN sales_stats ss ON ss.game_year = p.game_year AND ss.source_card_id = p.source_card_id
LEFT JOIN trimmed t      ON t.game_year  = p.game_year AND t.source_card_id  = p.source_card_id
LEFT JOIN latest_sale ls ON ls.game_year = p.game_year AND ls.source_card_id = p.source_card_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_futgg_market_snapshot_card
    ON futgg_market_snapshot (game_year, source_card_id);
CREATE INDEX IF NOT EXISTS idx_futgg_market_snapshot_source_card_id
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

-- fair_value_mv is CASCADE-dropped above because it depends on the
-- snapshot. Restored verbatim from migration 092 - unaffected by the
-- platform pin, since it reads current_bin/sales_* off the snapshot,
-- which is already ps5-only above.
CREATE MATERIALIZED VIEW fair_value_mv AS
WITH current_snapshot AS (
    SELECT DISTINCT ON (source_card_id) *
    FROM futgg_market_snapshot
    ORDER BY source_card_id, game_year DESC
)
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
FROM current_snapshot s
LEFT JOIN fut_players a ON a.card_id = s.source_card_id
WHERE s.source_card_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_fair_value_mv_card
    ON fair_value_mv (card_id);
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_discount
    ON fair_value_mv (discount_pct DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_suspect
    ON fair_value_mv (data_quality_suspect) WHERE data_quality_suspect;
CREATE INDEX IF NOT EXISTS idx_fair_value_mv_falling
    ON fair_value_mv (trend_falling) WHERE trend_falling;
