-- Migration 065: expose display_name / full_name / released_at on futgg_market_snapshot
-- target: core
-- requires-table: futgg_players, futgg_bin_history, futgg_sales_history
-- requires-column: futgg_players.display_name, futgg_players.full_name, futgg_players.released_at, futgg_players.card_image_url, futgg_players.card_image_stored_url, futgg_players.card_image_stored_status, futgg_players.pace, futgg_players.card_design_image_url
--
-- Adds three columns to the snapshot so backend read paths can prefer the
-- SHORT user-facing player name without a second join to futgg_players on
-- every query. Nothing else changes: the joins, CTEs, aggregation and
-- window logic are byte-identical to migration 050 (this file was
-- generated from 050's own definition rather than hand-transcribed, so a
-- copy error is not possible).
--
-- p.name is left in place and unchanged. 97 backend call sites read it
-- and there is a lower(name) search index below; user-facing surfaces
-- move to COALESCE(display_name, name), which degrades to exactly
-- today's behaviour for any row whose display name has not been
-- backfilled yet.
--
-- released_at rides along because the promo/event layer (migration 064)
-- and any card-level "how old is this card" display both want it, and a
-- matview rebuild is expensive enough that adding it now avoids a second
-- DROP+CREATE later.
--
-- Postgres has no CREATE OR REPLACE MATERIALIZED VIEW (same reason
-- 038/041/047/050 DROP+CREATE rather than ALTER).
--
-- CASCADE + fair_value_mv rebuild, added after this migration first
-- failed in production:
--
--     DependentObjectsStillExistError: cannot drop materialized view
--     futgg_market_snapshot because other objects depend on it
--     DETAIL: materialized view fair_value_mv depends on materialized
--     view futgg_market_snapshot
--
-- fair_value_mv's SELECT was never defined anywhere in THIS repo's
-- migrations (011-036 built it against the old FUTBIN-era columns; no
-- migration after that touches it) - it was rebuilt against
-- futgg_market_snapshot directly on production at some point outside
-- the tracked migration history. 050/052 both already DROP
-- futgg_market_snapshot without CASCADE and never hit this, which places
-- that rebuild AFTER 052 and confirms it is untracked drift, not
-- something this migration set caused.
--
-- Captured verbatim via `SELECT pg_get_viewdef('fair_value_mv'::regclass,
-- true)` against production and transcribed byte-for-byte below - not
-- reconstructed from an old migration, which could easily have missed a
-- production-only change. DROP ... CASCADE takes fair_value_mv and its
-- indexes down along with futgg_market_snapshot; both are rebuilt in the
-- same transaction as this file, so a failure here still rolls back
-- atomically and never leaves the database without fair_value_mv.

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
        WHERE approximate_sold_at >= now() - interval '14 days'
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
