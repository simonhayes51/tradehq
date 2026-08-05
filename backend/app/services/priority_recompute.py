# app/services/priority_recompute.py
"""
Phase 1 of the adaptive priority engine: computes card_priority for every
active FUT.GG card and writes it, on a periodic loop wired into main.py's
lifespan (same convention as market_data_provider.refresher_loop).

WHAT THIS DOES NOT DO YET (deliberately - see the phased rollout plan)
-----------------------------------------------------------------------
This module only ever writes to card_priority, watchlist_popularity, and
priority_engine_events. It NEVER writes to futgg_players.next_sales_
due_at / next_price_due_at, and does not change what auto_sync's
scrapers actually do. The scoring function needs to be validated against
real production data - tier distribution sane, promotion/demotion firing
correctly, SBC forcing working - before anything is allowed to depend on
it for actual scheduling. That wiring (behind PRIORITY_ENGINE_WRITE_
SCHEDULE, pull-forward-only, mirroring market_data_provider.py's
bump_price_priority() LEAST(...) pattern) is a later phase.

SIGNAL SOURCES USED (and one deliberately NOT used)
-----------------------------------------------------------------------
- futgg_market_snapshot: liquidity/freshness/volatility/card-type inputs.
- The live `watchlist` table (app/routers/watchlist.py, WATCHLIST_
  DATABASE_URL pool, source='futgg' rows) for user-interest - NOT the
  dead watchlist_items table (see migration 042's header).
- sbc_card_matching.get_active_sbc_matched_card_ids() for event-relevance
  forcing.
- Ownership/recent-trade signals are NOT computed in Phase 1: the core
  `trades` table (migration 010) has no card_id column, only free-text
  player/version - reliably joining that to a FUT.GG source_card_id
  would need the same FUTGG<->FUTBIN name reconciliation migration
  038's card_source_map explicitly deferred as its own future work, and
  guessing via fuzzy text match is exactly the kind of unreliable
  shortcut this codebase's "omit rather than fake" rule exists to avoid.
  ownership_count/recent_trade_count are passed as 0 until that
  reconciliation exists.
- historical_opportunity_rate is passed as None (opportunity_component
  stays 0) until Phase 2 wires up migration 040's outcome-tracking join.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import asyncpg

from app.services.priority_config import TIERS
from app.services.priority_engine import compute_priority
from app.services.sbc_card_matching import get_active_sbc_matched_card_ids, sync_requirement_filters

log = logging.getLogger("priority_recompute")

RECOMPUTE_ADVISORY_LOCK_KEY = 7741012  # distinct from market_data_provider's 7741011

_SNAPSHOT_QUERY = """
    SELECT source_card_id, rarity, sales_count, sales_window_span_minutes,
           sales_dispersion_ratio, bin_captured_at
    FROM futgg_market_snapshot
    WHERE is_active
"""


async def _watchlist_popularity_by_card(watchlist_pool: asyncpg.Pool) -> Dict[int, int]:
    """Best-effort: the watchlist table lives on its own pool/deployment
    and this is an observability input, not a correctness-critical one -
    a failure here should degrade user_interest_component to 0 for this
    pass, not take down the whole recompute."""
    try:
        async with watchlist_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT card_id, COUNT(DISTINCT user_id) AS watcher_count
                FROM watchlist
                WHERE source = 'futgg' AND card_id IS NOT NULL
                GROUP BY card_id
                """
            )
        return {int(r["card_id"]): int(r["watcher_count"]) for r in rows}
    except Exception:
        log.warning("watchlist popularity aggregation failed; treating as all-zero this pass", exc_info=True)
        return {}


async def _write_watchlist_popularity(core_pool: asyncpg.Pool, popularity: Dict[int, int]) -> None:
    if not popularity:
        return
    async with core_pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO watchlist_popularity (card_id, watcher_count, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (card_id) DO UPDATE
                SET watcher_count = EXCLUDED.watcher_count, updated_at = now()
            """,
            [(card_id, count) for card_id, count in popularity.items()],
        )


def _tier_rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


async def _previous_tiers(conn: asyncpg.Connection) -> Dict[int, tuple[str, float]]:
    rows = await conn.fetch("SELECT card_id, tier, priority_score FROM card_priority")
    return {int(r["card_id"]): (r["tier"], r["priority_score"]) for r in rows}


async def run_recompute_pass(core_pool: asyncpg.Pool, watchlist_pool: asyncpg.Pool) -> int:
    """One full recompute pass. Returns the number of cards scored.
    Safe to call concurrently across app instances (advisory-locked,
    same pattern as market_data_provider.refresh_snapshot) and safe to
    call before auto_sync has bootstrapped futgg_market_snapshot
    (UndefinedTableError is caught by the caller loop, same tolerance
    market_data_provider.refresher_loop already gives that table)."""
    async with core_pool.acquire() as lock_conn:
        got = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", RECOMPUTE_ADVISORY_LOCK_KEY)
        if not got:
            return 0
        try:
            await sync_requirement_filters(core_pool)
            matched = await get_active_sbc_matched_card_ids(core_pool)
            watcher_counts = await _watchlist_popularity_by_card(watchlist_pool)
            await _write_watchlist_popularity(core_pool, watcher_counts)

            async with core_pool.acquire() as conn:
                snapshot_rows: List[asyncpg.Record] = await conn.fetch(_SNAPSHOT_QUERY)
                previous = await _previous_tiers(conn)

                priority_args = []
                event_args = []
                for row in snapshot_rows:
                    card_id = int(row["source_card_id"])
                    result = compute_priority(
                        dict(row),
                        watcher_count=watcher_counts.get(card_id, 0),
                        ownership_count=0,
                        recent_trade_count=0,
                        historical_opportunity_rate=None,
                        forced_by_event_id=matched.get(card_id),
                    )
                    prev_tier, prev_score = previous.get(card_id, (None, None))
                    priority_args.append((
                        result.card_id, result.priority_score, result.tier,
                        result.liquidity_component, result.freshness_component,
                        result.card_type_component, result.opportunity_component,
                        result.user_interest_component, result.volatility_component,
                        result.event_component, result.forced_by_event_id,
                        prev_tier,
                    ))
                    if prev_tier != result.tier:
                        reason = "sbc_match" if result.forced_by_event_id else (
                            "initial" if prev_tier is None else
                            "activity_spike" if _tier_rank(result.tier) < _tier_rank(prev_tier) else
                            "inactivity_demote"
                        )
                        event_args.append((card_id, prev_tier, result.tier, reason, prev_score, result.priority_score))

                if priority_args:
                    await conn.executemany(
                        """
                        INSERT INTO card_priority (
                            card_id, priority_score, tier,
                            liquidity_component, freshness_component, card_type_component,
                            opportunity_component, user_interest_component, volatility_component,
                            event_component, forced_by_event_id, previous_tier, tier_changed_at, computed_at
                        )
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                                CASE WHEN $3 IS DISTINCT FROM $12 THEN now() ELSE
                                    (SELECT tier_changed_at FROM card_priority WHERE card_id = $1) END,
                                now())
                        ON CONFLICT (card_id) DO UPDATE SET
                            priority_score = EXCLUDED.priority_score,
                            tier = EXCLUDED.tier,
                            liquidity_component = EXCLUDED.liquidity_component,
                            freshness_component = EXCLUDED.freshness_component,
                            card_type_component = EXCLUDED.card_type_component,
                            opportunity_component = EXCLUDED.opportunity_component,
                            user_interest_component = EXCLUDED.user_interest_component,
                            volatility_component = EXCLUDED.volatility_component,
                            event_component = EXCLUDED.event_component,
                            forced_by_event_id = EXCLUDED.forced_by_event_id,
                            previous_tier = card_priority.tier,
                            tier_changed_at = CASE
                                WHEN EXCLUDED.tier IS DISTINCT FROM card_priority.tier THEN now()
                                ELSE card_priority.tier_changed_at
                            END,
                            computed_at = now()
                        """,
                        priority_args,
                    )
                if event_args:
                    await conn.executemany(
                        """
                        INSERT INTO priority_engine_events
                            (card_id, from_tier, to_tier, reason, score_before, score_after)
                        VALUES ($1,$2,$3,$4,$5,$6)
                        """,
                        event_args,
                    )
                    log.info("priority_recompute: %d tier changes this pass", len(event_args))

            return len(priority_args)
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", RECOMPUTE_ADVISORY_LOCK_KEY)


async def refresher_loop(core_pool: asyncpg.Pool, watchlist_pool: asyncpg.Pool, poll_seconds: int = 900) -> None:
    """Runs every poll_seconds (default 15min - independent of, and much
    less frequent than, any live scraper's own loop, so a bug here can
    never block SalesSync/PriceSync). Never lets an iteration's failure
    kill the loop - same tolerance market_data_provider.refresher_loop
    already established for this exact "auto_sync hasn't bootstrapped
    yet" scenario."""
    await asyncio.sleep(30)
    while True:
        try:
            scored = await run_recompute_pass(core_pool, watchlist_pool)
            if scored:
                log.info("priority_recompute: scored %d cards", scored)
        except asyncpg.exceptions.UndefinedTableError:
            pass
        except Exception as e:
            log.error("priority_recompute iteration failed: %s", e)
        await asyncio.sleep(poll_seconds)
