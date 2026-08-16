# app/services/market_data_provider.py
"""
Provider-neutral application layer over the FUT.GG market-intelligence
tables (futgg_players / futgg_bin_history / futgg_sales_history) and the
`futgg_market_snapshot` materialized view built from them (see
migrations/038_futgg_market_layer.sql).

This module is the ONLY place that writes raw SQL against futgg_* tables
or the snapshot view - routers (app/routers/v2/futgg_market.py) and the
intelligence layer (app/services/futgg_intelligence.py) call methods on
`FutggMarketDataProvider`, never `conn.fetch()` directly, so the SQL
lives in exactly one place and the schema can change without touching
either of those callers.

Deliberately no ABC/Protocol ceremony: a plain class with async methods
is enough here. `MarketDataProvider` is kept as a minimal structural
placeholder purely to document the intended provider-neutral interface
(a hypothetical FutbinMarketDataProvider could implement the same shape
over the legacy tables later) - it is NOT imported or relied on for
polymorphism anywhere in this codebase today.

Nothing here ever fabricates a price. `get_current_price()` returns
`None` when there is no BIN row for a card - never 0 - and every field
sourced from `approximate_sold_at` is labeled explicitly as approximate
everywhere it's surfaced (see get_recent_sales()).
"""
from __future__ import annotations

import logging
import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import asyncpg

from app.services.priority_config import PRIORITY_ENGINE_CONFIG
from app.services.priority_config import TIERS as PRIORITY_TIERS

log = logging.getLogger("market_data_provider")

# Must match migrations/038's REFRESH lock key family (distinct from the
# other advisory locks already in use - see scripts/run_migrations.py's
# 7741003, recommendation_engine_v2.py's 7741007, etc.).
REFRESH_ADVISORY_LOCK_KEY = 7741011

# Same bound the migration's own recent-sales window uses - kept as a
# named constant here too since get_recent_sales() enforces it again on
# top of the view (the view already only ever contains <=50 rows/card
# within 14 days, this is just the query-time ceiling for callers asking
# for fewer).
MAX_RECENT_SALES = 50

# The valuation window above is deliberately small - 50 rows is what the
# engine reasons over, and widening it would change what fair value means.
# A CHART is a different job: it is showing a human what the market has
# been doing, and truncating that to the newest 50 prints is why the
# range picker looked broken. On a liquid card those 50 rows can cover
# six hours, so "7d" and "24h" rendered the identical six-hour picture and
# the toggle appeared to do nothing at all.
MAX_CHART_SALES = 500

# The longest window any chart range may request. futgg_market_snapshot's
# bounded_sales CTE cuts at 14 days, and get_recent_sales/get_sales_by_ids
# now match it, so offering a "30d" range promised data that structurally
# cannot exist.
MAX_CHART_DAYS = 14

# Expected price-refresh cadence - used only for the freshness endpoint's
# "stale" bucketing (see get_freshness_summary()), not for any scoring
# math (that lives in futgg_intelligence.py and uses a single confidence-
# weighted staleness curve, not a per-tier cliff).
#
# CORRECTED: this used to be a per-price_tier dict of guessed intervals
# (special:30/gold_rare:60/gold_common:180/silver:360/bronze:720 min),
# explicitly flagged in this module's own history as "not values verified
# against auto_sync's actual scheduling". That guess is now provably
# wrong, not just unverified: BIN price is captured for the ENTIRE
# catalogue in one pass every FUTGG_BULK_INTERVAL_SECONDS by
# auto_sync/futgg_bulk_price_sync.py (the live PriceSync worker) - there
# is no per-tier price cadence anymore, so a single threshold (with a
# safety margin for one missed cycle) is what actually describes current
# reality. Sales-side staleness (a genuinely per-card, per-tier cadence -
# see auto_sync/futgg_sales_sync.py's SALES_INTERVALS and
# card_priority.effective_tier) is reported separately below, not
# folded into this same number.
_BULK_PRICE_INTERVAL_SECONDS = int(os.getenv("FUTGG_BULK_INTERVAL_SECONDS", "300"))
EXPECTED_PRICE_INTERVAL_MINUTES = (_BULK_PRICE_INTERVAL_SECONDS / 60.0) * 2  # one missed cycle of slack

# Sales-refresh cadence per adaptive-priority tier (see
# app/services/priority_config.py's tier_sales_interval_minutes - same
# values, imported rather than duplicated). Falls back to the flat
# rating-band default (NORMAL) for any card card_priority hasn't scored
# yet, since that's this engine's own tier-derivation default too.
DEFAULT_SALES_TIER = "NORMAL"


def _strip_diacritics(text: str) -> str:
    """Best-effort diacritic folding for search (e.g. 'Otavio' matches
    'Otávio'). Application-side, not a DB extension - see migration 038's
    comment on why a pg_trgm/unaccent index wasn't used."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_search_term(term: str) -> str:
    return _strip_diacritics(term).lower().strip()


class MarketDataProvider:
    """Documents the provider-neutral method shape. Not used
    polymorphically today - see module docstring."""

    async def get_player(self, card_id: int) -> Optional[Dict[str, Any]]: ...

    async def get_current_price(self, card_id: int, platform: Optional[str] = None) -> Optional[Dict[str, Any]]: ...

    async def get_recent_sales(self, card_id: int, limit: int = MAX_RECENT_SALES) -> List[Dict[str, Any]]: ...

    async def get_sales_series(self, card_id: int, *, days: int, limit: int = MAX_CHART_SALES) -> List[Dict[str, Any]]: ...

    async def get_sales_coverage(self, card_id: int) -> Dict[str, Any]: ...

    async def get_price_history(self, card_id: int, period: Optional[str] = None) -> List[Dict[str, Any]]: ...


_SNAPSHOT_COLUMNS = """
    source_card_id, source_player_id, source_slug, source_url, game_year,
    name, rating, primary_position, alternate_positions, rarity, squad,
    price_tier, club, league, nation, club_source_id, league_source_id,
    nation_source_id, player_image_url, card_design_image_url,
    card_image_url, card_image_stored_url, card_image_stored_status,
    club_image_url, league_image_url, nation_image_url,
    pace, shooting, passing, dribbling, defending, physicality, is_active,
    is_tradeable, last_price_status, metadata_warnings, price_updated_at,
    next_price_due_at, current_bin, price_range_low, price_range_high,
    bin_source_age_text, bin_captured_at, bin_checked_at, sales_count,
    sales_median, sales_trimmed_mean, sales_low, sales_high, sales_stddev,
    sales_p10, sales_p90, latest_sale_price, latest_sale_at,
    sales_window_earliest_at, sales_window_latest_at,
    sales_window_span_minutes, sales_dispersion_ratio,
    sales_interdecile_ratio, snapshot_computed_at
"""
# Every key futgg_intelligence.evaluate_card() reads off a snapshot row
# must appear above, or the engine silently sees None and skips whatever
# that column drives - no error, no log, just a gate that quietly stops
# existing. This list has now dropped a new column twice:
#
#   * sales_interdecile_ratio (migration 050) - the dispersion gate built
#     for the Pacho case never fired through the scanner or the player
#     list at all, because those paths select this list rather than *.
#   * bin_checked_at (migration 052) - the price-freshness fix was inert
#     for the same reason: _price_observed_at found no key, fell back to
#     bin_captured_at, and every stable card kept failing STALE_MARKET.
#     Production stayed at "0 trades available" through a deploy that was
#     supposed to fix exactly that.
#
# Both slipped through because the unit tests construct snapshot dicts by
# hand and the live smoke-tests used SELECT * - so the one layer that
# actually decides which columns reach the engine was never exercised by
# either. test_snapshot_columns_cover_engine_reads() in
# test_futgg_price_freshness_clock.py now asserts the two agree.

#: Columns that a database may legitimately not have yet, because the
#: migration adding them can be SKIPPED rather than applied - every
#: futgg_market_snapshot migration declares `requires-column` guards and
#: defers when auto_sync has not yet created the columns it reads (see
#: 050's header). Naming a missing column in a SELECT is not a degraded
#: read, it is an UndefinedColumnError on EVERY provider query: the
#: players list, opportunities, trade finder and dashboard all 500 at
#: once. That is strictly worse than the stale-data problem adding these
#: columns was meant to solve, and migration 050 explicitly promised the
#: opposite ("a database still on 047's view shape keeps working exactly
#: as it did"). Adding them to the list above silently broke that promise.
#:
#: The engine already treats each of these as "gate not applicable" when
#: absent, so dropping them from the SELECT degrades exactly as intended.
_OPTIONAL_SNAPSHOT_COLUMNS = frozenset({
    "bin_checked_at",           # migration 052
    "sales_p10",                # migration 050
    "sales_p90",                # migration 050
    "sales_interdecile_ratio",  # migration 050
})

#: Resolved once per process against the live view. None until first use.
_snapshot_columns_sql: Optional[str] = None
#: Whether the live view actually has bin_checked_at. Set by
#: resolve_snapshot_columns; None means "not yet resolved".
_snapshot_has_checked_at: Optional[bool] = None


def price_age_sql(column_prefix: str = "") -> str:
    """SQL for "how old is our price for this card", tolerant of a view
    that predates migration 052.

    Mirrors futgg_intelligence._price_observed_at: prefer when the feed
    last CONFIRMED the price, fall back to when it last CHANGED. Naming
    bin_checked_at unconditionally would raise UndefinedColumnError on a
    database where 052 has not run, taking out every query that filters
    or reports on price age.
    """
    captured = f"{column_prefix}bin_captured_at"
    if _snapshot_has_checked_at is False:
        return captured
    return f"coalesce({column_prefix}bin_checked_at, {captured})"


async def resolve_snapshot_columns(conn: asyncpg.Connection) -> str:
    """The SELECT list, narrowed to columns this database actually has.

    Cached per process: the view's shape only changes when a migration
    runs, and a migration means a redeploy.
    """
    global _snapshot_columns_sql
    if _snapshot_columns_sql is not None:
        return _snapshot_columns_sql

    wanted = [c.strip() for c in _SNAPSHOT_COLUMNS.replace("\n", " ").split(",")]
    wanted = [c for c in wanted if c]
    present = {
        r["attname"]
        for r in await conn.fetch(
            """
            SELECT a.attname FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'futgg_market_snapshot'
              AND n.nspname = current_schema()
              AND a.attnum > 0 AND NOT a.attisdropped
            """
        )
    }
    if not present:
        # The view does not exist yet (fresh database, auto_sync has not
        # bootstrapped). Leave the list alone and let the caller's own
        # error handling report a missing relation, which is the honest
        # error - not a silently truncated column list.
        _snapshot_columns_sql = _SNAPSHOT_COLUMNS
        return _snapshot_columns_sql

    missing = [c for c in wanted if c not in present]
    required_missing = [c for c in missing if c not in _OPTIONAL_SNAPSHOT_COLUMNS]
    if required_missing:
        # Not survivable by dropping columns - the view is a shape this
        # code does not understand. Say so loudly; the query below will
        # raise and that is correct.
        log.error(
            "futgg_market_snapshot is missing required column(s) %s - the market "
            "layer cannot read it. Has a migration failed part-way?",
            ", ".join(required_missing),
        )
    elif missing:
        log.warning(
            "futgg_market_snapshot lacks optional column(s) %s - the features they "
            "drive are disabled until the migration that adds them runs. "
            "bin_checked_at absent means the price-freshness gate falls back to "
            "'when the price last changed', which rejects cards whose price is "
            "merely stable.",
            ", ".join(missing),
        )
    global _snapshot_has_checked_at
    _snapshot_has_checked_at = "bin_checked_at" in present
    _snapshot_columns_sql = ", ".join(c for c in wanted if c in present) or _SNAPSHOT_COLUMNS
    return _snapshot_columns_sql


@dataclass(frozen=True)
class PlayerFilters:
    """Shared filter set for /players, /opportunities, /trade-finder.
    Every field is optional - None means "don't filter on this"."""
    search: Optional[str] = None
    rating_min: Optional[int] = None
    rating_max: Optional[int] = None
    position: Optional[str] = None
    rarity: Optional[str] = None
    club: Optional[str] = None
    league: Optional[str] = None
    nation: Optional[str] = None
    max_price: Optional[int] = None
    min_price: Optional[int] = None
    max_price_age_minutes: Optional[int] = None
    tradeable_only: bool = True


def _apply_common_filters(
    where: List[str], args: List[Any], f: PlayerFilters
) -> None:
    if f.tradeable_only:
        where.append("is_tradeable IS NOT FALSE")
    if f.rating_min is not None:
        args.append(f.rating_min)
        where.append(f"rating >= ${len(args)}")
    if f.rating_max is not None:
        args.append(f.rating_max)
        where.append(f"rating <= ${len(args)}")
    if f.position:
        args.append(f.position)
        where.append(f"(primary_position = ${len(args)} OR ${len(args)} = ANY(alternate_positions))")
    if f.rarity:
        args.append(f.rarity)
        where.append(f"rarity = ${len(args)}")
    if f.club:
        args.append(f.club)
        where.append(f"club = ${len(args)}")
    if f.league:
        args.append(f.league)
        where.append(f"league = ${len(args)}")
    if f.nation:
        args.append(f.nation)
        where.append(f"nation = ${len(args)}")
    if f.max_price is not None:
        args.append(f.max_price)
        where.append(f"current_bin IS NOT NULL AND current_bin <= ${len(args)}")
    if f.min_price is not None:
        args.append(f.min_price)
        where.append(f"current_bin IS NOT NULL AND current_bin >= ${len(args)}")
    if f.max_price_age_minutes is not None:
        args.append(f.max_price_age_minutes)
        # coalesce(bin_checked_at, bin_captured_at) is the SQL twin of
        # futgg_intelligence._price_observed_at, and for the same reason:
        # since change-only writes shipped, bin_captured_at means "the
        # price last moved", not "we last checked". Filtering on it hides
        # every card whose price is simply stable. Migration 052 adds
        # bin_checked_at to the view; a database old enough to lack it is
        # one where migration 050 (identical requires-column list) never
        # applied either, so the view is not at this shape at all.
        age = price_age_sql()
        where.append(
            f"{age} IS NOT NULL AND "
            f"EXTRACT(EPOCH FROM (now() - {age})) / 60.0 <= ${len(args)}"
        )
    if f.search:
        term = f"%{f.search.strip()}%"
        args.append(term)
        where.append(f"name ILIKE ${len(args)}")


class FutggMarketDataProvider(MarketDataProvider):
    """Concrete FUT.GG-backed implementation. Reads only
    futgg_market_snapshot / futgg_bin_history / futgg_sales_history /
    pipeline_heartbeats on the core pool (app/db.py::get_core_pool()) -
    never opens its own connection."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # -------------------------------------------------------------------
    # MarketDataProvider surface
    # -------------------------------------------------------------------

    async def get_player(self, card_id: int) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {await resolve_snapshot_columns(conn)} FROM futgg_market_snapshot "
                f"WHERE source_card_id = $1",
                card_id,
            )
        return dict(row) if row else None

    async def get_players_by_ids(self, card_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Batch lookup keyed by source_card_id - for callers rendering a
        list of already-known ids (e.g. a watchlist) that would otherwise
        N+1 get_player() per row. Empty input returns an empty dict
        without a query."""
        if not card_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {await resolve_snapshot_columns(conn)} FROM futgg_market_snapshot "
                f"WHERE source_card_id = ANY($1::bigint[])",
                list(card_ids),
            )
        return {int(row["source_card_id"]): dict(row) for row in rows}

    async def bump_price_priority(self, card_id: int) -> None:
        """Moves a card to the front of futgg_price_sync.py's due queue -
        called when a user actually opens a card whose price the caller
        has judged stale (see evaluate_card()'s tier-aware freshness
        gate). Viewing a specific card is the clearest "someone might act
        on this" signal the app gets outside of it already being a
        surfaced opportunity, so it's worth re-pricing sooner than its
        tier's normal interval would otherwise allow - the same idea as
        futgg_price_sync.py's own hot-opportunity fast-track, triggered
        by demand instead of by discount.

        Only ever pulls next_price_due_at EARLIER (via LEAST), never
        pushes it later, and only for cards still eligible for regular
        pricing at all (is_active, not confirmed untradeable) - a no-op
        if the card is already due sooner than "now", so calling this on
        every detail-page view is always safe, never fights the price
        worker's own scheduling."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE futgg_players
                SET next_price_due_at = LEAST(COALESCE(next_price_due_at, NOW()), NOW())
                WHERE source_card_id = $1
                  AND is_active
                  AND is_tradeable IS DISTINCT FROM FALSE
                """,
                card_id,
            )

    async def get_current_price(self, card_id: int, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
        # `platform` accepted for interface parity with a future
        # multi-platform provider - FUT.GG's scraper is console/PC
        # agnostic today (no platform column on futgg_bin_history), so it
        # is intentionally unused here rather than silently misapplied.
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT lowest_bin, price_range_low, price_range_high,
                       source_age_text, captured_at
                FROM futgg_bin_history
                WHERE source_card_id = $1
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                card_id,
            )
        if row is None:
            return None
        return dict(row)

    async def get_recent_sales(self, card_id: int, limit: int = MAX_RECENT_SALES) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(limit, MAX_RECENT_SALES))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sold_price, ea_tax, net_price, approximate_sold_at,
                       source_age_text, source_age_seconds
                FROM futgg_sales_history
                WHERE source_card_id = $1
                  -- Same 14-day bound futgg_market_snapshot's bounded_sales
                  -- CTE applies. This feeds the detail view's trend
                  -- assessment (futgg_market.py::get_player_detail), so
                  -- without it the trend gate and the snapshot aggregates it
                  -- gates could be computed over different windows for the
                  -- same card. It also bounds the /players/{id}/sales chart,
                  -- which should not be splicing months-old prints into a
                  -- picture of today's market either.
                  AND approximate_sold_at >= now() - interval '14 days'
                ORDER BY approximate_sold_at DESC
                LIMIT $2
                """,
                card_id, bounded_limit,
            )
        return [dict(r) for r in rows]

    async def get_sales_coverage(self, card_id: int) -> Dict[str, Any]:
        """When we last LOOKED at this card's sales, as distinct from when
        it last sold.

        An empty sales list has two completely different meanings and the
        UI could not tell them apart: "nobody has bought this card" and
        "we have not checked this card recently". For most of the
        catalogue it is the second - futgg_sales_sync.py re-reads by
        rating band on intervals that, for the lower bands, are longer
        than the windows the UI offers. A 78-rated card on a 96-hour
        cadence will show no sales in a 24-hour window essentially always,
        and saying "no completed sales" states that as a fact about the
        market when it is a fact about our own coverage.

        Read straight off futgg_players rather than the snapshot view on
        purpose: these columns are written by the sales worker between
        view refreshes, and adding them to the materialized view would
        mean another migration on the busiest table in the system for
        data that needs no aggregation.
        """
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT last_sales_status, last_sales_status_at,
                           next_sales_due_at, sales_first_read_at, rating
                    FROM futgg_players WHERE source_card_id = $1
                    """,
                    card_id,
                )
            except asyncpg.exceptions.UndefinedColumnError:
                # SalesSync's ensure_schema has not run on this database
                # yet. Report "unknown" rather than failing the endpoint.
                return {"checked_at": None, "never_checked": None, "status": None}
        if row is None:
            return {"checked_at": None, "never_checked": None, "status": None}
        return {
            "checked_at": row["last_sales_status_at"],
            "next_due_at": row["next_sales_due_at"],
            "never_checked": row["sales_first_read_at"] is None,
            "status": row["last_sales_status"],
        }

    async def get_sales_series(
        self, card_id: int, *, days: int, limit: int = MAX_CHART_SALES,
    ) -> List[Dict[str, Any]]:
        """Sales for CHARTING over an explicit window - deliberately separate
        from get_recent_sales(), which serves valuation and must keep its
        exact 50-row window because that is what fair value is defined over.

        The range picker used to be a client-side filter over whatever
        get_recent_sales() happened to return, which was always the newest
        50 prints. On a liquid card that is a few hours of data, so every
        range rendered the same few hours and the picker looked broken.
        Asking the database for the window the user actually selected is
        the only thing that makes the toggle mean anything.
        """
        bounded_days = max(1, min(int(days), MAX_CHART_DAYS))
        bounded_limit = max(1, min(int(limit), MAX_CHART_SALES))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sold_price, ea_tax, net_price, approximate_sold_at,
                       source_age_text, source_age_seconds
                FROM futgg_sales_history
                WHERE source_card_id = $1
                  AND approximate_sold_at >= now() - make_interval(days => $2)
                ORDER BY approximate_sold_at DESC
                LIMIT $3
                """,
                card_id, bounded_days, bounded_limit,
            )
        return [dict(r) for r in rows]

    async def get_price_history(self, card_id: int, period: Optional[str] = None) -> List[Dict[str, Any]]:
        interval = _period_to_interval(period)
        async with self._pool.acquire() as conn:
            if interval:
                rows = await conn.fetch(
                    """
                    SELECT lowest_bin, price_range_low, price_range_high,
                           source_age_text, captured_at
                    FROM futgg_bin_history
                    WHERE source_card_id = $1 AND captured_at >= now() - $2::interval
                    ORDER BY captured_at DESC
                    """,
                    card_id, interval,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT lowest_bin, price_range_low, price_range_high,
                           source_age_text, captured_at
                    FROM futgg_bin_history
                    WHERE source_card_id = $1
                    ORDER BY captured_at DESC
                    """,
                    card_id,
                )
        return [dict(r) for r in rows]

    async def get_sales_by_ids(
        self, card_ids: List[int], limit_per_card: int = MAX_RECENT_SALES,
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Batch raw sales series, keyed by card id - for the trend layer,
        which needs per-sale rows (the snapshot's aggregates cannot
        express a slope) across many cards at once.

        Without this the segmented scanner would issue one query per
        candidate: a 1,500-card pass would mean 1,500 round trips. The
        LATERAL keeps it to a single query while still bounding rows per
        card, which a plain `WHERE source_card_id = ANY(...)` cannot do.
        """
        if not card_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.source_card_id, s.sold_price, s.approximate_sold_at
                FROM unnest($1::bigint[]) AS ids(card_id)
                CROSS JOIN LATERAL (
                    SELECT source_card_id, sold_price, approximate_sold_at
                    FROM futgg_sales_history
                    WHERE source_card_id = ids.card_id
                      -- Same 14-day bound futgg_market_snapshot's
                      -- bounded_sales CTE applies (migration 038 onward).
                      -- Without it this series and the snapshot aggregates
                      -- could describe different periods, so the trend gate
                      -- and the fair value it gates were computed over
                      -- different windows for the same card.
                      AND approximate_sold_at >= now() - interval '14 days'
                    ORDER BY approximate_sold_at DESC
                    LIMIT $2
                ) s
                """,
                list(card_ids), max(1, min(limit_per_card, MAX_RECENT_SALES)),
            )
        out: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            out.setdefault(int(row["source_card_id"]), []).append(
                {
                    "sold_price": row["sold_price"],
                    "approximate_sold_at": row["approximate_sold_at"],
                }
            )
        return out

    async def select_candidates_by_segment(self) -> Dict[str, List[Dict[str, Any]]]:
        """Run the segmented candidate scan.

        Returns {segment_name: [snapshot rows]}. Segments overlap by
        design; the caller de-duplicates. This replaces the single
        `ORDER BY sales_count DESC LIMIT 500` query, which structurally
        confined the entire engine to the 500 most-traded cards - the most
        efficiently priced markets in the game - and never looked at the
        long tail where mispricing actually persists.
        """
        from app.services.futgg_candidate_segments import SEGMENTS

        out: Dict[str, List[Dict[str, Any]]] = {}
        async with self._pool.acquire() as conn:
            columns = await resolve_snapshot_columns(conn)
            for segment in SEGMENTS:
                # segment.where/order_by are static strings from a
                # module-level whitelist - never user input.
                sql = f"""
                    SELECT {columns}
                    FROM futgg_market_snapshot
                    WHERE is_tradeable IS DISTINCT FROM FALSE
                      AND ({segment.where})
                    ORDER BY {segment.order_by}
                    LIMIT $1
                """
                rows = await conn.fetch(sql, segment.limit)
                out[segment.name] = [dict(r) for r in rows]
        return out

    # -------------------------------------------------------------------
    # List / search surface (backs /players, /opportunities, /trade-finder)
    # -------------------------------------------------------------------

    async def search_players(
        self,
        filters: PlayerFilters,
        *,
        limit: int = 25,
        offset: int = 0,
        order_by: str = "rating DESC NULLS LAST",
    ) -> List[Dict[str, Any]]:
        where: List[str] = []
        args: List[Any] = []
        _apply_common_filters(where, args, filters)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        args.append(limit)
        args.append(offset)
        async with self._pool.acquire() as conn:
            sql = f"""
                SELECT {await resolve_snapshot_columns(conn)}
                FROM futgg_market_snapshot
                {where_clause}
                ORDER BY {order_by}
                LIMIT ${len(args) - 1} OFFSET ${len(args)}
            """
            rows = await conn.fetch(sql, *args)

        results = [dict(r) for r in rows]
        if filters.search:
            # ILIKE already narrowed the candidate set at the DB layer
            # (indexed via idx_futgg_market_snapshot_name_lower's
            # lower(name), which ILIKE can use for the common
            # case-folding path); this second pass only adds diacritic
            # folding on top of that already-small result set, never
            # scans the full table itself.
            needle = normalize_search_term(filters.search)
            results = [r for r in results if needle in normalize_search_term(r.get("name") or "")]
        return results

    async def count_players(self, filters: PlayerFilters) -> int:
        where: List[str] = []
        args: List[Any] = []
        _apply_common_filters(where, args, filters)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT count(*) FROM futgg_market_snapshot {where_clause}"
        async with self._pool.acquire() as conn:
            return int(await conn.fetchval(sql, *args))

    # -------------------------------------------------------------------
    # Freshness / heartbeat surface
    # -------------------------------------------------------------------

    async def get_freshness_summary(self) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            heartbeats = await conn.fetch(
                """
                SELECT worker, last_run_at, ok, detail
                FROM pipeline_heartbeats
                -- 'futgg_price_sync' is the retired per-card Playwright
                -- scraper (see Procfile's own comment on the switch) -
                -- kept here only so an old/never-migrated deployment
                -- still shows something, since that worker's own
                -- heartbeat() call remains in its source. The live
                -- price worker is 'futgg_bulk_price_sync' (continuous
                -- bulk-feed service, see Dockerfile.price) - it did not
                -- call heartbeat() at all until this fix, which is why
                -- this panel could show a stale entry under the retired
                -- worker's name while the real worker's actual state
                -- (healthy or stalled) stayed invisible here.
                WHERE worker IN ('futgg_player_sync', 'futgg_price_sync', 'futgg_bulk_price_sync')
                """
            )
            totals = await conn.fetchrow(
                """
                SELECT
                    count(*) FILTER (WHERE is_active) AS discovered,
                    count(*) FILTER (WHERE is_active AND current_bin IS NOT NULL) AS priced,
                    count(*) FILTER (WHERE is_active AND is_tradeable IS NOT FALSE AND current_bin IS NULL) AS no_market,
                    count(*) FILTER (WHERE is_active AND is_tradeable IS FALSE) AS untradeable
                FROM futgg_market_snapshot
                """
            )
            # Single threshold now (see EXPECTED_PRICE_INTERVAL_MINUTES's
            # comment) - grouped by price_tier purely for readability in
            # the response, not because the threshold itself differs by
            # group anymore.
            # Resolve first: price_age_sql() only knows whether
            # bin_checked_at exists once the view has been inspected.
            await resolve_snapshot_columns(conn)
            age = price_age_sql()
            stale_rows = await conn.fetch(
                f"""
                SELECT price_tier, count(*) AS stale_count
                FROM futgg_market_snapshot
                WHERE is_active AND is_tradeable IS NOT FALSE AND current_bin IS NOT NULL
                  AND {age} IS NOT NULL
                  AND EXTRACT(EPOCH FROM (now() - {age})) / 60.0 > $1
                GROUP BY price_tier
                """,
                EXPECTED_PRICE_INTERVAL_MINUTES,
            )
            stale_detail = {r["price_tier"]: int(r["stale_count"]) for r in stale_rows}
            stale_total = sum(stale_detail.values())

            # Sales staleness, genuinely per-tier (unlike price above) -
            # joined against card_priority (Phase 1 of the adaptive
            # priority engine, see priority_recompute.py) where available,
            # falling back to DEFAULT_SALES_TIER for cards not yet scored
            # by it. futgg_players.last_sales_status_at is the real "when
            # was sales data for this card last attempted/refreshed"
            # column (auto_sync/futgg_sales_sync.py writes it) - the
            # snapshot view has no equivalent, so this reads futgg_players
            # directly rather than adding the column to the view for one
            # observability query.
            sales_stale_detail = {}
            try:
                for tier in PRIORITY_TIERS:
                    threshold = PRIORITY_ENGINE_CONFIG.tier_sales_interval_minutes.get(tier, 240)
                    count = await conn.fetchval(
                        """
                        SELECT count(*)
                        FROM futgg_players p
                        LEFT JOIN card_priority cp ON cp.card_id = p.source_card_id
                        WHERE p.is_active AND p.is_tradeable IS NOT FALSE
                          AND p.last_sales_status_at IS NOT NULL
                          AND COALESCE(cp.effective_tier, $1) = $2
                          AND EXTRACT(EPOCH FROM (now() - p.last_sales_status_at)) / 60.0 > $3
                        """,
                        DEFAULT_SALES_TIER, tier, threshold,
                    )
                    sales_stale_detail[tier] = int(count)
            except asyncpg.exceptions.UndefinedTableError:
                # card_priority (migration 044) or futgg_players.
                # last_sales_status_at hasn't been created/backfilled on
                # this database yet - same "degrade, don't 500" tolerance
                # the rest of this module gives a not-yet-bootstrapped
                # auto_sync deployment.
                sales_stale_detail = {}

            latest_errors = await conn.fetch(
                """
                SELECT source_card_id, name, last_price_status
                FROM futgg_market_snapshot
                WHERE last_price_status IS NOT NULL
                  AND last_price_status NOT IN ('success', 'untradeable')
                ORDER BY price_updated_at DESC NULLS LAST
                LIMIT 20
                """
            )

        return {
            "heartbeats": [dict(h) for h in heartbeats],
            "cards_discovered": int(totals["discovered"] or 0),
            "cards_priced": int(totals["priced"] or 0),
            "cards_no_market": int(totals["no_market"] or 0),
            "cards_untradeable": int(totals["untradeable"] or 0),
            "cards_stale": stale_total,
            "stale_by_price_tier": stale_detail,
            "stale_sales_by_priority_tier": sales_stale_detail,
            "latest_source_errors": [dict(e) for e in latest_errors],
        }


def _period_to_interval(period: Optional[str]) -> Optional[timedelta]:
    """Returns a timedelta, not a string: the caller binds this against an
    explicit `$2::interval` cast, which makes asyncpg encode it with its
    interval codec - that codec requires a real timedelta (it reads .days
    off the value), not a "N hours"-style string. See
    futgg_recommendation_store.py::track_record's identical fix for the
    same failure mode."""
    if not period:
        return None
    mapping = {
        "1h": timedelta(hours=1), "6h": timedelta(hours=6),
        "24h": timedelta(hours=24), "1d": timedelta(days=1),
        "7d": timedelta(days=7), "14d": timedelta(days=14), "30d": timedelta(days=30),
    }
    return mapping.get(period)


async def refresh_snapshot(pool: asyncpg.Pool) -> bool:
    """REFRESH MATERIALIZED VIEW CONCURRENTLY futgg_market_snapshot,
    advisory-locked so multiple app instances never race the refresh -
    same convention as fair_value_mv / recommendations_latest (see
    app/services/fair_value.py, recommendation_engine_v2.py's
    run_pass_v2). Returns True if this call actually performed the
    refresh, False if another instance already held the lock."""
    async with pool.acquire() as conn:
        got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", REFRESH_ADVISORY_LOCK_KEY)
        if not got:
            return False
        try:
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY futgg_market_snapshot")
            return True
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", REFRESH_ADVISORY_LOCK_KEY)


async def refresher_loop(pool: asyncpg.Pool, poll_seconds: int = 120) -> None:
    """Self-synchronizing on futgg_players.last_seen_at/price_updated_at's
    own watermark, same pattern as recommendation_engine_v2's
    refresher_loop_v2 keying off fair_value_mv - only actually refreshes
    the (relatively expensive) materialized view when the underlying
    source tables have moved since the last refresh, not on a blind
    fixed-interval REFRESH regardless of whether anything changed."""
    import asyncio

    await asyncio.sleep(15)
    last_watermark: Optional[datetime] = None
    # A refresh this loop declines to perform used to be completely
    # silent: `did` False (another holder of the advisory lock) logged
    # nothing, and a watermark that never moves logged nothing either.
    # Both present downstream as a snapshot frozen in time, which reads
    # like a quiet market rather than a stalled pipeline. Count the
    # consecutive skips and say so.
    consecutive_skips = 0
    while True:
        try:
            async with pool.acquire() as conn:
                watermark = await conn.fetchval(
                    "SELECT greatest(max(last_seen_at), max(price_updated_at)) FROM futgg_players"
                )
            if watermark and watermark != last_watermark:
                did = await refresh_snapshot(pool)
                if did:
                    log.info("futgg_market_snapshot refreshed (watermark=%s)", watermark)
                    consecutive_skips = 0
                else:
                    consecutive_skips += 1
                    # Every time would be noise at a 120s poll; never
                    # would reproduce the outage this comment exists for.
                    if consecutive_skips in (1, 5) or consecutive_skips % 25 == 0:
                        log.warning(
                            "futgg_market_snapshot refresh skipped %d time(s) in a row - "
                            "advisory lock %s is held elsewhere. The snapshot is not "
                            "advancing and every consumer of it is reading stale rows.",
                            consecutive_skips, REFRESH_ADVISORY_LOCK_KEY,
                        )
                last_watermark = watermark
            else:
                consecutive_skips += 1
                if consecutive_skips in (5,) or consecutive_skips % 25 == 0:
                    log.warning(
                        "futgg_market_snapshot watermark has not moved for %d poll(s) "
                        "(watermark=%s) - no refresh performed. Either the price/player "
                        "sync workers are not writing, or this backend is pointed at a "
                        "different database than they are.",
                        consecutive_skips, watermark,
                    )
        except asyncpg.exceptions.UndefinedTableError:
            # futgg_players doesn't exist yet on this database (auto_sync
            # hasn't bootstrapped it) - same "wait, don't crash" tolerance
            # migrations/038's own requires-table guard gives the
            # migration itself.
            pass
        except Exception as e:  # never let the loop die
            log.error("futgg market snapshot refresher iteration failed: %s", e)
        await asyncio.sleep(poll_seconds)
