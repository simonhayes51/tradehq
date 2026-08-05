# app/services/priority_recompute.py
"""
Milestone 1 (rule-based shadow classification) and Milestone 1.5
(conservative cost-control demotion) of the adaptive priority engine.
Runs on a periodic loop wired into main.py's lifespan (same convention
as market_data_provider.refresher_loop).

MILESTONE 1: classifies every active FUT.GG card into baseline_tier
(what the card IS - no freshness/staleness input at all, see
priority_engine.py's module docstring for why that was migration 042's
bug) and effective_tier (baseline_tier merged with any active,
expiring priority_triggers row). Writes card_priority + full audit rows
to priority_engine_events. Does NOT touch next_sales_due_at.

MILESTONE 1.5: behind PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION
(default off), pushes next_sales_due_at LATER (never earlier) for
cards meeting every one of a strict eligibility list - see
is_eligible_for_conservative_demotion(). This is the one thing in this
module that writes to a column the live SalesSync worker actually
reads, and it is the ONLY direction this milestone ever writes in
(mirrors bump_price_priority()'s pull-forward-only GREATEST(...)
pattern, just in the opposite direction). Anchored to
futgg_players.last_sales_status_at (set by SalesSync itself on every
real scrape attempt), not to now() - see the module-level comment on
_CONSERVATIVE_DEMOTION_UPDATE_SQL for why anchoring to now() instead
would cause the due date to be pushed further out on every single
recompute pass forever, permanently starving the card rather than
demoting it to a longer-but-finite interval.

NEVER writes next_price_due_at at any point - the live PriceSync worker
(futgg_bulk_price_sync.py) never reads that column, so doing so would
have zero effect on anything (see the plan doc's "What already exists").

SIGNAL SOURCES USED (and two deliberately NOT used yet)
-----------------------------------------------------------------------
- futgg_market_snapshot: liquidity/volatility/card-type inputs.
- The live `watchlist` table (WATCHLIST_DATABASE_URL pool,
  source='futgg' rows), windowed to a recent period, NOT the dead
  watchlist_items table.
- priority_triggers (sbc_card_matching.sync_sbc_triggers) for
  event-relevance forcing.
- Ownership/recent-trade signals: NOT computed - the core `trades`
  table has no card_id column, only free-text player/version; reliably
  joining that needs the same FUTGG<->FUTBIN reconciliation migration
  038's card_source_map already deferred as its own future work.
- historical_opportunity_rate: NOT computed - reserved columns exist on
  card_priority (qualified_opportunity_count/eligible_evaluation_count)
  for a future normalized, shrinkage-adjusted rate, deliberately not a
  raw historical count (which would reward a card simply for being
  scraped, and therefore evaluated, more often).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import asyncpg

from app.services.priority_config import PRIORITY_ENGINE_CONFIG, PriorityEngineConfig
from app.services.priority_engine import PriorityResult, compute_priority
from app.services.sbc_card_matching import sync_requirement_filters, sync_sbc_triggers

log = logging.getLogger("priority_recompute")

RECOMPUTE_ADVISORY_LOCK_KEY = 7741012  # distinct from market_data_provider's 7741011

_SNAPSHOT_QUERY = """
    SELECT source_card_id, rarity, sales_count, sales_window_span_minutes, sales_dispersion_ratio
    FROM futgg_market_snapshot
    WHERE is_active
"""


def _env_flags_snapshot() -> str:
    """Recorded onto every card_priority/priority_engine_events row so
    an audit can answer "what config was active when this was decided"
    without cross-referencing deploy history. Returns a JSON string,
    not a dict - asyncpg has no JSONB codec registered on this
    connection (see app/db.py's _init_conn), so every JSONB write in
    this codebase goes through an explicit json.dumps() at the call
    site; doing it once here keeps every caller of this function from
    having to remember that."""
    return json.dumps({
        "PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION": PRIORITY_ENGINE_CONFIG.allow_conservative_demotion,
        "engine_version": PRIORITY_ENGINE_CONFIG.version,
    })


# -----------------------------------------------------------------------
# Watchlist popularity (recent window, distinct active users)
# -----------------------------------------------------------------------

async def _watchlist_popularity_by_card(watchlist_pool: asyncpg.Pool, window_days: int) -> Dict[int, int]:
    """Best-effort: the watchlist table lives on its own pool/deployment
    and this is an observability/scoring input, not correctness-critical
    - a failure here degrades user_interest_component to 0 for this
    pass, not the whole recompute. Windowed to `started_at` (the only
    per-row timestamp the live `watchlist` table has - a row simply
    ceases to exist once a user stops watching, so this is "recent
    additions", the closest available proxy to "recently active
    interest" without a table redesign)."""
    try:
        async with watchlist_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT card_id, COUNT(DISTINCT user_id) AS watcher_count
                FROM watchlist
                WHERE source = 'futgg' AND card_id IS NOT NULL
                  AND started_at > now() - ($1 * interval '1 day')
                GROUP BY card_id
                """,
                window_days,
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


# -----------------------------------------------------------------------
# Milestone 1: classification
# -----------------------------------------------------------------------

async def _active_triggers_by_card(conn: asyncpg.Connection) -> Dict[int, List[str]]:
    rows = await conn.fetch(
        """
        SELECT card_id, forced_min_tier
        FROM priority_triggers
        WHERE expires_at IS NULL OR expires_at > now()
        """
    )
    out: Dict[int, List[str]] = {}
    for r in rows:
        out.setdefault(int(r["card_id"]), []).append(r["forced_min_tier"])
    return out


async def _previous_priority(conn: asyncpg.Connection) -> Dict[int, asyncpg.Record]:
    rows = await conn.fetch(
        "SELECT card_id, baseline_tier, effective_tier, importance_score FROM card_priority"
    )
    return {int(r["card_id"]): r for r in rows}


async def run_classification_pass(core_pool: asyncpg.Pool, watchlist_pool: asyncpg.Pool) -> int:
    """One full Milestone 1 classification pass. Returns the number of
    cards scored. Advisory-locked (same pattern as
    market_data_provider.refresh_snapshot) so concurrent app instances
    never race it."""
    async with core_pool.acquire() as lock_conn:
        got = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", RECOMPUTE_ADVISORY_LOCK_KEY)
        if not got:
            return 0
        try:
            await sync_requirement_filters(core_pool)
            await sync_sbc_triggers(core_pool)

            watcher_counts = await _watchlist_popularity_by_card(
                watchlist_pool, PRIORITY_ENGINE_CONFIG.watchlist_popularity_window_days
            )
            await _write_watchlist_popularity(core_pool, watcher_counts)

            env_flags = _env_flags_snapshot()

            async with core_pool.acquire() as conn:
                snapshot_rows = await conn.fetch(_SNAPSHOT_QUERY)
                triggers_by_card = await _active_triggers_by_card(conn)
                previous = await _previous_priority(conn)

                priority_args = []
                event_args = []
                for row in snapshot_rows:
                    card_id = int(row["source_card_id"])
                    result: PriorityResult = compute_priority(
                        dict(row),
                        watcher_count=watcher_counts.get(card_id, 0),
                        historical_opportunity_rate=None,
                        active_trigger_tiers=triggers_by_card.get(card_id, []),
                    )
                    prev = previous.get(card_id)
                    prev_baseline = prev["baseline_tier"] if prev else None
                    prev_effective = prev["effective_tier"] if prev else None
                    prev_score = prev["importance_score"] if prev else None

                    priority_args.append((
                        result.card_id, result.baseline_tier, result.effective_tier, result.importance_score,
                        result.liquidity_component, result.card_type_component, result.opportunity_component,
                        result.user_interest_component, result.volatility_component, result.event_component,
                        result.reason_codes, prev_baseline, prev_effective,
                        PRIORITY_ENGINE_CONFIG.version, env_flags,
                    ))

                    if prev_baseline != result.baseline_tier or prev_effective != result.effective_tier:
                        if prev_baseline is None:
                            reason = "initial"
                        elif result.applied_trigger_tier is not None and prev_effective != result.effective_tier:
                            # A trigger is applying right now and it just
                            # changed the effective tier this pass.
                            reason = "trigger_activated"
                        elif (
                            result.applied_trigger_tier is None
                            and prev_effective is not None
                            and prev_effective != prev_baseline
                        ):
                            # No trigger applies now, but last pass's
                            # effective tier differed from its baseline -
                            # that gap can only have come from a trigger
                            # that has since expired.
                            reason = "trigger_expired"
                        else:
                            reason = "baseline_change"
                        event_args.append((
                            card_id, prev_baseline, result.baseline_tier, prev_effective, result.effective_tier,
                            reason, result.reason_codes,
                            json.dumps({
                                "liquidity": result.liquidity_component, "card_type": result.card_type_component,
                                "opportunity": result.opportunity_component, "user_interest": result.user_interest_component,
                                "volatility": result.volatility_component, "event": result.event_component,
                            }),
                            result.applied_trigger_tier, prev_score, result.importance_score,
                            env_flags, PRIORITY_ENGINE_CONFIG.version,
                        ))

                if priority_args:
                    await conn.executemany(
                        """
                        INSERT INTO card_priority (
                            card_id, baseline_tier, effective_tier, importance_score,
                            liquidity_component, card_type_component, opportunity_component,
                            user_interest_component, volatility_component, event_component,
                            reason_codes, previous_baseline_tier, previous_effective_tier,
                            baseline_tier_changed_at, effective_tier_changed_at,
                            engine_version, env_flags_snapshot, computed_at
                        )
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                                CASE WHEN $2 IS DISTINCT FROM $12 THEN now() ELSE NULL END,
                                CASE WHEN $3 IS DISTINCT FROM $13 THEN now() ELSE NULL END,
                                $14,$15,now())
                        ON CONFLICT (card_id) DO UPDATE SET
                            baseline_tier = EXCLUDED.baseline_tier,
                            effective_tier = EXCLUDED.effective_tier,
                            importance_score = EXCLUDED.importance_score,
                            liquidity_component = EXCLUDED.liquidity_component,
                            card_type_component = EXCLUDED.card_type_component,
                            opportunity_component = EXCLUDED.opportunity_component,
                            user_interest_component = EXCLUDED.user_interest_component,
                            volatility_component = EXCLUDED.volatility_component,
                            event_component = EXCLUDED.event_component,
                            reason_codes = EXCLUDED.reason_codes,
                            previous_baseline_tier = card_priority.baseline_tier,
                            previous_effective_tier = card_priority.effective_tier,
                            baseline_tier_changed_at = CASE
                                WHEN EXCLUDED.baseline_tier IS DISTINCT FROM card_priority.baseline_tier THEN now()
                                ELSE card_priority.baseline_tier_changed_at
                            END,
                            effective_tier_changed_at = CASE
                                WHEN EXCLUDED.effective_tier IS DISTINCT FROM card_priority.effective_tier THEN now()
                                ELSE card_priority.effective_tier_changed_at
                            END,
                            engine_version = EXCLUDED.engine_version,
                            env_flags_snapshot = EXCLUDED.env_flags_snapshot,
                            computed_at = now()
                        """,
                        priority_args,
                    )
                if event_args:
                    await conn.executemany(
                        """
                        INSERT INTO priority_engine_events (
                            card_id, previous_baseline_tier, new_baseline_tier,
                            previous_effective_tier, new_effective_tier,
                            reason, reason_codes, component_snapshot,
                            trigger_source, score_before, score_after,
                            env_flags_snapshot, engine_version
                        )
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                        """,
                        event_args,
                    )
                    log.info("priority_recompute: %d tier changes this pass", len(event_args))

            return len(priority_args)
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", RECOMPUTE_ADVISORY_LOCK_KEY)


# -----------------------------------------------------------------------
# Milestone 1.5: conservative cost-control demotion
# -----------------------------------------------------------------------

def is_eligible_for_conservative_demotion(
    *,
    card_type_component: float,
    sales_count: int,
    watcher_count: int,
    has_active_trigger: bool,
    first_seen_at: Optional[datetime],
    next_sales_due_at: Optional[datetime],
    last_sales_status_at: Optional[datetime],
    as_of: datetime,
    config: PriorityEngineConfig = PRIORITY_ENGINE_CONFIG,
) -> bool:
    """Pure predicate - every condition must hold. Order matters for
    readability only; all are checked regardless. The
    next_sales_due_at/last_sales_status_at IS NOT NULL checks preserve
    select_due()'s own "never-scraped cards first" backfill invariant
    byte-for-byte: a card that has never been read is never eligible,
    full stop, matching exactly the predicate SalesSync's own due-query
    already uses to mean "has been read"."""
    if next_sales_due_at is None or last_sales_status_at is None:
        return False
    if has_active_trigger:
        return False
    if card_type_component > config.conservative_demotion_max_card_type_score:
        return False
    if sales_count > 0:
        return False
    if watcher_count > config.conservative_demotion_watcher_max:
        return False
    if first_seen_at is None:
        return False
    if (as_of - first_seen_at) < timedelta(days=config.conservative_demotion_min_card_age_days):
        return False
    return True


_CONSERVATIVE_DEMOTION_CANDIDATES_QUERY = """
    SELECT p.source_card_id, p.first_seen_at, p.next_sales_due_at, p.last_sales_status_at,
           cp.card_type_component, COALESCE(ms.sales_count, 0) AS sales_count,
           COALESCE(wp.watcher_count, 0) AS watcher_count
    FROM futgg_players p
    JOIN card_priority cp ON cp.card_id = p.source_card_id
    LEFT JOIN futgg_market_snapshot ms ON ms.source_card_id = p.source_card_id
    LEFT JOIN watchlist_popularity wp ON wp.card_id = p.source_card_id
    WHERE cp.effective_tier = 'DORMANT'
      AND p.is_active
      AND p.next_sales_due_at IS NOT NULL
      AND p.last_sales_status_at IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM priority_triggers t
          WHERE t.card_id = p.source_card_id AND (t.expires_at IS NULL OR t.expires_at > now())
      )
"""

# Anchored to last_sales_status_at (set by SalesSync itself on every
# real scrape attempt), NOT now(). Anchoring to now() would recompute a
# strictly-larger target on every single recompute pass (now() always
# advances), so GREATEST(current, now()+ceiling) would push the due
# date further out forever, permanently starving the card rather than
# giving it a long-but-finite interval - the exact opposite of the
# "hard discovery ceiling" this milestone is required to preserve.
# Anchoring to the last real scrape instead makes the target STABLE
# between recompute passes: it only grows again once SalesSync has
# actually re-scraped the card and moved last_sales_status_at forward,
# which is exactly the "still boring after a real check" signal that
# should extend the interval again.
_CONSERVATIVE_DEMOTION_UPDATE_SQL = """
    UPDATE futgg_players
    SET next_sales_due_at = GREATEST(
        next_sales_due_at,
        last_sales_status_at + ($2 * interval '1 minute')
    )
    WHERE source_card_id = ANY($1::bigint[])
      AND last_sales_status_at IS NOT NULL
      AND next_sales_due_at IS NOT NULL
"""


async def run_conservative_demotion_pass(core_pool: asyncpg.Pool) -> int:
    """Milestone 1.5. No-op (returns 0 immediately) unless
    PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION is set. Logs every
    affected card to priority_engine_events regardless."""
    config = PRIORITY_ENGINE_CONFIG
    if not config.allow_conservative_demotion:
        return 0

    as_of = datetime.now(timezone.utc)
    async with core_pool.acquire() as conn:
        candidates = await conn.fetch(_CONSERVATIVE_DEMOTION_CANDIDATES_QUERY)
        eligible_ids = []
        event_args = []
        env_flags = _env_flags_snapshot()
        for row in candidates:
            eligible = is_eligible_for_conservative_demotion(
                card_type_component=row["card_type_component"] or 0.0,
                sales_count=int(row["sales_count"]),
                watcher_count=int(row["watcher_count"]),
                has_active_trigger=False,  # already excluded by the candidates query's NOT EXISTS
                first_seen_at=row["first_seen_at"],
                next_sales_due_at=row["next_sales_due_at"],
                last_sales_status_at=row["last_sales_status_at"],
                as_of=as_of,
                config=config,
            )
            if not eligible:
                continue
            card_id = int(row["source_card_id"])
            eligible_ids.append(card_id)
            new_due_at = row["last_sales_status_at"] + timedelta(minutes=config.conservative_demotion_max_interval_minutes)
            if new_due_at <= row["next_sales_due_at"]:
                continue  # already extended at least this far - no-op, don't log a no-op event
            event_args.append((
                card_id, "DORMANT", "DORMANT", "DORMANT", "DORMANT",
                "conservative_demotion", ["CONSERVATIVE_DEMOTION_APPLIED"], "{}",
                None, row["next_sales_due_at"], new_due_at, env_flags, config.version,
            ))

        if not eligible_ids:
            return 0

        await conn.execute(_CONSERVATIVE_DEMOTION_UPDATE_SQL, eligible_ids, config.conservative_demotion_max_interval_minutes)

        if event_args:
            await conn.executemany(
                """
                INSERT INTO priority_engine_events (
                    card_id, previous_baseline_tier, new_baseline_tier,
                    previous_effective_tier, new_effective_tier,
                    reason, reason_codes, component_snapshot,
                    trigger_source, previous_due_at, new_due_at,
                    env_flags_snapshot, engine_version
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                """,
                event_args,
            )
            log.info("priority_recompute: conservative demotion pushed next_sales_due_at for %d cards", len(event_args))

    return len(event_args)


# -----------------------------------------------------------------------
# Loop
# -----------------------------------------------------------------------

async def refresher_loop(core_pool: asyncpg.Pool, watchlist_pool: asyncpg.Pool, poll_seconds: int = 900) -> None:
    """Runs every poll_seconds (default 15min - independent of, and
    much less frequent than, any live scraper's own loop). Never lets
    an iteration's failure kill the loop - same tolerance
    market_data_provider.refresher_loop already established for "auto_
    sync hasn't bootstrapped yet"."""
    await asyncio.sleep(30)
    while True:
        try:
            scored = await run_classification_pass(core_pool, watchlist_pool)
            if scored:
                log.info("priority_recompute: classified %d cards", scored)
            demoted = await run_conservative_demotion_pass(core_pool)
            if demoted:
                log.info("priority_recompute: conservative demotion touched %d cards", demoted)
        except asyncpg.exceptions.UndefinedTableError:
            pass
        except Exception as e:
            log.error("priority_recompute iteration failed: %s", e)
        await asyncio.sleep(poll_seconds)
