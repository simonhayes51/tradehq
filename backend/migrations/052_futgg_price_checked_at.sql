-- Migration 052: separate "when we last CHECKED this price" from
-- "when this price last CHANGED"
-- target: core
-- requires-table: futgg_players, futgg_bin_history, futgg_sales_history
-- requires-column: futgg_players.card_image_url, futgg_players.card_image_stored_url, futgg_players.card_image_stored_status, futgg_players.pace, futgg_players.card_design_image_url
--
-- WHAT BROKE
-- ----------
-- futgg_bulk_price_sync.py used to write a futgg_bin_history row for
-- every card in the feed, every cycle. bin_captured_at - defined below
-- as max(captured_at) per card - therefore meant BOTH "when we last saw
-- a price for this card" and "when that price was last written", because
-- those were the same event. The engine's price-age gate
-- (futgg_config.max_price_age_minutes_by_tier, special=20 minutes) was
-- calibrated against that behaviour and passed comfortably: the writer
-- touched every card roughly every five minutes.
--
-- Change-only writes broke that equivalence. The worker now skips cards
-- whose price is unchanged (~6,000 of ~8,400 per cycle) and falls back to
-- a heartbeat row at FUTGG_BULK_HEARTBEAT_MINUTES + (card_id % that) -
-- up to 29 minutes, plus cycle time. bin_captured_at consequently stopped
-- meaning "we checked recently" and started meaning "the price last moved
-- recently", which for a stable card is an arbitrarily old timestamp.
--
-- The gate did not know the difference. A special-tier card whose price
-- had simply not moved for 20 minutes was rejected as STALE_MARKET even
-- though the feed had confirmed that exact price three minutes earlier.
-- Observed in production as `futgg scan: evaluated=1344 recommendations=0`
-- repeating identically for hours - not a scarcity of opportunities, a
-- freshness gate reading the wrong clock.
--
-- THE FIX
-- -------
-- price_checked_at records every cycle in which the feed returned a real,
-- non-zero price for a card, whether or not that price differed from the
-- last one. bin_captured_at keeps its existing meaning (last price
-- CHANGE, or heartbeat) and the deduplicated futgg_bin_history stays
-- deduplicated - the cost saving is untouched. Only the freshness signal
-- moves to the column that actually answers the freshness question.
--
-- Deliberately NOT price_updated_at. That column is also bumped for
-- 'bulk_no_market' cards - the feed reporting a card has no active
-- listings at all - and 038's write path documents that it must never be
-- allowed to make such a card look freshly priced. price_checked_at is
-- written only alongside a real price, so exposing it to the gate cannot
-- resurrect a card with no market.
--
-- The view exposes it as bin_checked_at, next to bin_captured_at, so both
-- clocks are readable and a consumer that wants "when did this price last
-- move" still has it. futgg_intelligence.py prefers bin_checked_at and
-- falls back to bin_captured_at when it is NULL, so a database that has
-- run this migration but is still being written by an older bulk sync
-- (or one whose cards have not been through a cycle yet) behaves exactly
-- as it does today rather than treating every card as unpriced.
--
-- Postgres has no CREATE OR REPLACE MATERIALIZED VIEW (same reason
-- 038/041/047/050 DROP+CREATE rather than ALTER). The SELECT below is
-- 050's, unchanged apart from the two added lines.

ALTER TABLE futgg_players
    ADD COLUMN IF NOT EXISTS price_checked_at TIMESTAMPTZ;

-- Backfill so the gate has something defensible to read on the first
-- scan after deploy, before the bulk sync has completed a cycle.
-- price_updated_at is the closest existing approximation: for cards the
-- feed priced it was set on the same statement that wrote the history
-- row. Cards last seen with no market get NULL rather than a borrowed
-- timestamp - the gate then falls back to bin_captured_at for them,
-- which is the pre-migration behaviour and the honest one.
UPDATE futgg_players
   SET price_checked_at = price_updated_at
 WHERE price_checked_at IS NULL
   AND price_updated_at IS NOT NULL
   AND last_price_status IS DISTINCT FROM 'bulk_no_market';

DROP MATERIALIZED VIEW IF EXISTS futgg_market_snapshot;

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
    -- When the feed last CONFIRMED this price, as opposed to when the
    -- price last moved. See this migration's header - these stopped
    -- being the same event when change-only writes shipped.
    p.price_checked_at                                    AS bin_checked_at,

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
