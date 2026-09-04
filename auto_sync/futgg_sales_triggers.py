#!/usr/bin/env python
"""Triggered detailed-sales selection - Part 5 of the FUT.GG price-detail
infrastructure work.

WHY THIS EXISTS
---------------
select_due() in futgg_sales_sync.py schedules by tier cadence alone: every
active/tradeable card gets a detailed sales read on a fixed interval
determined by its card_priority.effective_tier, regardless of whether
anything about that specific card currently warrants one. That is a
reasonable BASELINE, but it means a card only reaches CRITICAL's fast
cadence by first being classified CRITICAL - a card no one is watching,
with no active alert, no live recommendation, and no genuine price move
gets exactly the same treatment as an identical card that just crossed a
profit threshold with three users watching it, provided both happen to
land in the same tier.

This module is a SEPARATE, ADDITIONAL selection: a small set of specific,
falsifiable reasons a particular card deserves a detailed read RIGHT NOW,
each backed by a real signal already computed elsewhere in this codebase
(bulk price deltas, watchlists, alerts, recommendations, Discord posting
candidates, new releases, market activity, and evidence staleness) plus
one explicit hook for a user-requested refresh. It does not replace or
modify select_due()'s tier-floor logic, and by itself changes NOTHING
about what futgg_sales_sync.py actually fetches - select_triggered() is
wired into a shadow-mode comparison only (futgg_sales_shadow.py, Part 6),
not the live scheduler.

AUDIT NOTE - priority_triggers was deliberately NOT used
----------------------------------------------------------
priority_triggers (migration 044) declares a trigger_type CHECK
constraint enumerating 'sbc_match', 'sales_spike', 'price_spike',
'new_release', 'user_position', 'manual_override' - several of those
names look almost exactly like reasons below, and it is a real,
already-built "reason-tagged forced-priority" table. It is deliberately
NOT the integration point here: it feeds card_priority.effective_tier,
which is precisely the tier-floor axis select_due() already schedules
from. Writing to it would be a tier-floor/scheduling change, which this
work was explicitly told not to make. This module is wholly separate and
additive instead - it does not read or write card_priority or
priority_triggers at all.

RESERVED USER CAPACITY
-----------------------
User-driven reasons (watchlist, active alert, user-requested - see
USER_DRIVEN_REASONS) are always selected before background reasons
(bulk price change, profit opportunity, recommendation, Discord
candidate, new release, market activity, stale evidence) and are never
truncated to make room for them - background only ever fills whatever of
`limit` user-driven candidates did not use. That ordering is itself the
guarantee the task asked for ("reserve part of the allowance ... so
background work cannot exhaust all capacity"): background cannot displace
a single user-driven candidate, structurally, regardless of volume on
either side. TRIGGERED_USER_RESERVED_SHARE is exported for Part 6's
shadow-mode capacity reporting (the fraction of TRIGGERED_SELECT_LIMIT
notionally set aside for user-driven work) rather than being an extra
cap inside select_triggered() itself - the ordering already enforces it.

TWO REASONS HAVE NO EXISTING DATA SOURCE
------------------------------------------
"Bulk price changed materially" and "user explicitly requested current
analysis" have no existing persisted signal to read:

  * futgg_bulk_price_sync.py computes _last_price_changes in-process,
    but it and futgg_sales_sync.py are separate deployed processes with
    no shared memory - the database is the only channel between them.
    futgg_price_change_events (migration 080) is that channel.
  * There is no existing "refresh this card's sales now" request queue
    anywhere in the codebase (bump_price_priority() in
    market_data_provider.py bumps PRICE priority only). futgg_sales_
    priority_requests (migration 080) is a minimal queue a future
    endpoint can insert into.

Both are read-and-consume: a row is marked consumed_at once considered,
never deleted, so a "why did this fire" question later has an answer.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import asyncpg

from futgg_sales_sync import ENGINE_REJECT_SALES_OLDER_THAN_MINUTES, env_float, env_int

log = logging.getLogger("futgg_sales_triggers")

# ---------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------

REASON_BULK_PRICE_CHANGE = "bulk_price_change"
REASON_PROFIT_OPPORTUNITY = "profit_opportunity"
REASON_WATCHLIST = "watchlist"
REASON_ACTIVE_ALERT = "active_alert"
REASON_LIVE_RECOMMENDATION = "live_recommendation"
REASON_DISCORD_CANDIDATE = "discord_candidate"
REASON_NEW_RELEASE = "new_release"
REASON_MARKET_ACTIVITY = "market_activity"
REASON_STALE_EVIDENCE = "stale_evidence_critical"
REASON_USER_REQUESTED = "user_requested"

#: Reasons that represent a specific user's own action or interest,
#: rather than a background heuristic over the whole catalogue. See the
#: RESERVED USER CAPACITY note above.
USER_DRIVEN_REASONS = frozenset(
    {REASON_WATCHLIST, REASON_ACTIVE_ALERT, REASON_USER_REQUESTED}
)

ALL_REASONS = frozenset(
    {
        REASON_BULK_PRICE_CHANGE, REASON_PROFIT_OPPORTUNITY, REASON_WATCHLIST,
        REASON_ACTIVE_ALERT, REASON_LIVE_RECOMMENDATION, REASON_DISCORD_CANDIDATE,
        REASON_NEW_RELEASE, REASON_MARKET_ACTIVITY, REASON_STALE_EVIDENCE,
        REASON_USER_REQUESTED,
    }
)

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

TRIGGERED_SELECT_LIMIT = env_int("FUTGG_TRIGGERED_SELECT_LIMIT", 40, minimum=0)
TRIGGERED_USER_RESERVED_SHARE = env_float(
    "FUTGG_TRIGGERED_USER_RESERVED_SHARE", 0.4, minimum=0.0,
)

PRICE_CHANGE_MATERIAL_PCT_FLOOR = env_float(
    "FUTGG_TRIGGER_PRICE_CHANGE_PCT_FLOOR", 0.03, minimum=0.0,
)
PRICE_CHANGE_LOOKBACK_MINUTES = env_int(
    "FUTGG_TRIGGER_PRICE_CHANGE_LOOKBACK_MIN", 120, minimum=1,
)

#: Mirrors backend MEANINGFUL_PROFIT_ABS_FLOOR / MEANINGFUL_PROFIT_PCT_OF_
#: PRICE (app/routers/v2/futgg_market.py). Duplicated, not imported - this
#: worker deploys separately from the backend and cannot import
#: app.routers (same reason ENGINE_REJECT_SALES_OLDER_THAN_MINUTES is
#: duplicated in futgg_sales_sync.py, imported here rather than
#: re-duplicated a second time since this module DOES share a deployment
#: with futgg_sales_sync.py). Kept honest by
#: test_futgg_sales_triggers.py::TestMirroredConstants.
#:
#: Deliberately a HIGHER bar than PRICE_CHANGE_MATERIAL_PCT_FLOOR above -
#: "materially changed" (bulk_price_change) and "crossed a profit
#: threshold" (profit_opportunity) read the same futgg_price_change_events
#: rows but assert different things: the first says the move is big
#: enough that stale sales evidence risks describing a card wrongly; the
#: second says the move itself is big enough to plausibly BE a profitable
#: flip.
MEANINGFUL_PROFIT_ABS_FLOOR = env_int("FUTGG_TRIGGER_PROFIT_ABS_FLOOR", 300, minimum=0)
MEANINGFUL_PROFIT_PCT_OF_PRICE = env_float(
    "FUTGG_TRIGGER_PROFIT_PCT_OF_PRICE", 0.015, minimum=0.0,
)

NEW_RELEASE_WINDOW_DAYS = env_int("FUTGG_TRIGGER_NEW_RELEASE_WINDOW_DAYS", 14, minimum=1)

MARKET_ACTIVITY_MIN_SALES_COUNT = env_int(
    "FUTGG_TRIGGER_MARKET_ACTIVITY_MIN_SALES", 5, minimum=1,
)
MARKET_ACTIVITY_STALE_MINUTES = env_int(
    "FUTGG_TRIGGER_MARKET_ACTIVITY_STALE_MIN", 240, minimum=1,
)

#: Added on top of ENGINE_REJECT_SALES_OLDER_THAN_MINUTES so this fires
#: strictly after the engine has already started rejecting the card, not
#: at exactly the same instant (see cadence_gate_conflicts()'s own
#: "exactly at the limit still counts as a conflict" note for why an
#: equal-not-strict boundary is the wrong default elsewhere in this
#: codebase).
STALE_EVIDENCE_MARGIN_MINUTES = env_int(
    "FUTGG_TRIGGER_STALE_EVIDENCE_MARGIN_MIN", 0, minimum=0,
)

ALERT_LOOKBACK_MINUTES = env_int("FUTGG_TRIGGER_ALERT_LOOKBACK_MIN", 1440, minimum=1)


def _meaningful_profit_floor(price: float) -> float:
    return max(MEANINGFUL_PROFIT_ABS_FLOOR, (price or 0) * MEANINGFUL_PROFIT_PCT_OF_PRICE)


# ---------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerHit:
    """One reason one card was selected. `detail` carries whatever
    numbers justified it (e.g. {"previous_price": ..., "new_price": ...})
    purely for shadow-mode reporting (Part 6) - never read back into
    selection logic itself."""

    card_id: int
    reason: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TriggeredCard:
    card_id: int
    reasons: tuple[str, ...]
    details: dict[str, dict]

    @property
    def is_user_driven(self) -> bool:
        return any(r in USER_DRIVEN_REASONS for r in self.reasons)


# ---------------------------------------------------------------------
# Trigger #1 / #2: bulk price change + profit opportunity
#
# Split into a DB read-and-consume step and a pure classifier so both
# reasons score the exact same set of rows, read and marked consumed
# exactly once, and so the classifier itself is testable with no DB.
# ---------------------------------------------------------------------


async def _fetch_and_consume_price_change_events(
    conn: asyncpg.Connection | None,
) -> list[dict[str, Any]]:
    if conn is None:
        return []
    rows = await conn.fetch(
        """
        SELECT id, card_id, previous_price, new_price
        FROM futgg_price_change_events
        WHERE consumed_at IS NULL
          AND detected_at >= now() - ($1 * interval '1 minute')
        ORDER BY detected_at DESC
        """,
        PRICE_CHANGE_LOOKBACK_MINUTES,
    )
    out = [dict(r) for r in rows]
    ids = [int(r["id"]) for r in out]
    if ids:
        await conn.execute(
            "UPDATE futgg_price_change_events SET consumed_at = now() "
            "WHERE id = ANY($1::bigint[])",
            ids,
        )
    return out


def triggers_from_price_change_events(rows: Iterable[dict[str, Any]]) -> list[TriggerHit]:
    hits: list[TriggerHit] = []
    for r in rows:
        previous = r.get("previous_price")
        new = r.get("new_price")
        card_id = int(r["card_id"])
        if new is None:
            continue
        if previous is not None and previous > 0:
            pct = abs(new - previous) / previous
            if pct >= PRICE_CHANGE_MATERIAL_PCT_FLOOR:
                hits.append(TriggerHit(
                    card_id, REASON_BULK_PRICE_CHANGE,
                    {"previous_price": previous, "new_price": new, "pct_change": round(pct, 4)},
                ))
        if previous is not None and abs(new - previous) >= _meaningful_profit_floor(new):
            hits.append(TriggerHit(
                card_id, REASON_PROFIT_OPPORTUNITY,
                {"previous_price": previous, "new_price": new, "delta": new - previous},
            ))
    return hits


# ---------------------------------------------------------------------
# Trigger #3: watchlist
# ---------------------------------------------------------------------


async def triggers_from_watchlist(
    conn: asyncpg.Connection | None, *, min_watchers: int = 1,
) -> list[TriggerHit]:
    """watchlist_popularity (card_id, watcher_count, updated_at) is
    already aggregated onto the CORE pool by priority_recompute.py's own
    refresher - no cross-pool query needed for this one reason."""
    if conn is None:
        return []
    rows = await conn.fetch(
        "SELECT card_id, watcher_count FROM watchlist_popularity WHERE watcher_count >= $1",
        min_watchers,
    )
    return [
        TriggerHit(int(r["card_id"]), REASON_WATCHLIST, {"watcher_count": int(r["watcher_count"])})
        for r in rows
    ]


# ---------------------------------------------------------------------
# Trigger #4: active alert
# ---------------------------------------------------------------------


async def triggers_from_active_alerts(
    watchlist_conn: asyncpg.Connection | None,
) -> list[TriggerHit]:
    """alerts_log lives on the separate WATCHLIST_DATABASE_URL pool (see
    backend/app/routers/watchlist.py's own module docstring).
    watchlist_conn is None whenever that pool isn't configured for this
    deployment, or the query itself fails - both degrade to no
    active-alert triggers rather than failing the whole selection pass,
    the same soft-degrade priority_recompute.py's own cross-pool calls
    already use."""
    if watchlist_conn is None:
        return []
    try:
        rows = await watchlist_conn.fetch(
            """
            SELECT DISTINCT card_id
            FROM alerts_log
            WHERE source = 'futgg'
              AND dismissed_at IS NULL
              AND sent_at >= now() - ($1 * interval '1 minute')
            """,
            ALERT_LOOKBACK_MINUTES,
        )
    except Exception:
        log.warning("active-alert trigger query failed; degrading to none", exc_info=True)
        return []
    return [TriggerHit(int(r["card_id"]), REASON_ACTIVE_ALERT, {}) for r in rows]


# ---------------------------------------------------------------------
# Trigger #5 / #6: live recommendation + Discord feed candidate
# ---------------------------------------------------------------------


async def triggers_from_recommendations(
    conn: asyncpg.Connection | None,
) -> list[TriggerHit]:
    """futgg_recommendation_snapshots only ever persists 'buy'/
    'strong_buy'/'watch' signals (PERSISTED_SIGNALS in
    futgg_recommendation_store.py) - every row here is already a live
    recommendation by construction. discord_trading_feed.py's own
    publish_live_opportunities filters signal IN ('buy','strong_buy')
    AND invalidated_at IS NULL (see its open_count query) - mirrored
    here rather than querying discord_server_posts directly, since that
    table's card id lives only inside a jsonb payload, not a queryable
    column."""
    if conn is None:
        return []
    rows = await conn.fetch(
        """
        SELECT source_card_id, signal
        FROM (
            SELECT DISTINCT ON (source_card_id) source_card_id, signal, invalidated_at, expires_at
            FROM futgg_recommendation_snapshots
            ORDER BY source_card_id, evaluated_at DESC
        ) latest
        WHERE latest.invalidated_at IS NULL
          AND (latest.expires_at IS NULL OR latest.expires_at > now())
        """
    )
    hits: list[TriggerHit] = []
    for r in rows:
        card_id = int(r["source_card_id"])
        signal = r["signal"]
        hits.append(TriggerHit(card_id, REASON_LIVE_RECOMMENDATION, {"signal": signal}))
        if signal in ("buy", "strong_buy"):
            hits.append(TriggerHit(card_id, REASON_DISCORD_CANDIDATE, {"signal": signal}))
    return hits


# ---------------------------------------------------------------------
# Trigger #7: newly released promo card
# ---------------------------------------------------------------------


async def triggers_from_new_releases(
    conn: asyncpg.Connection | None,
) -> list[TriggerHit]:
    """futgg_players.released_at (migration 063) - FUT.GG's own "Added
    On" value, in the correct (source_card_id) id space. Not
    auto_sync/promo_event_detector.py's cluster detector: that reads
    fut_players.version/first_seen_at, the legacy FUTBIN id space, and
    is not usable directly against futgg_players.source_card_id."""
    if conn is None:
        return []
    rows = await conn.fetch(
        """
        SELECT source_card_id, released_at
        FROM futgg_players
        WHERE released_at IS NOT NULL
          AND released_at >= now() - ($1 * interval '1 day')
          AND is_active
          AND is_tradeable IS DISTINCT FROM FALSE
        """,
        NEW_RELEASE_WINDOW_DAYS,
    )
    return [
        TriggerHit(
            int(r["source_card_id"]), REASON_NEW_RELEASE,
            {"released_at": r["released_at"].isoformat() if r["released_at"] else None},
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# Trigger #8: meaningful current market activity
# ---------------------------------------------------------------------


async def triggers_from_market_activity(
    conn: asyncpg.Connection | None,
) -> list[TriggerHit]:
    """futgg_market_snapshot.sales_count (refreshed by the bulk feed)
    shows real listing activity; futgg_players.last_sales_status_at
    shows whether OUR detailed evidence has kept up with it. A card can
    be busy right now with sales evidence that predates the activity -
    that gap, not sales_count alone, is the trigger."""
    if conn is None:
        return []
    rows = await conn.fetch(
        """
        SELECT ms.source_card_id, ms.sales_count
        FROM futgg_market_snapshot ms
        JOIN futgg_players p ON p.source_card_id = ms.source_card_id
        WHERE ms.sales_count >= $1
          AND p.is_active
          AND p.is_tradeable IS DISTINCT FROM FALSE
          AND (
              p.last_sales_status_at IS NULL
              OR p.last_sales_status_at <= now() - ($2 * interval '1 minute')
          )
        """,
        MARKET_ACTIVITY_MIN_SALES_COUNT, MARKET_ACTIVITY_STALE_MINUTES,
    )
    return [
        TriggerHit(
            int(r["source_card_id"]), REASON_MARKET_ACTIVITY,
            {"sales_count": int(r["sales_count"])},
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# Trigger #9: existing sales evidence needs refreshing
# ---------------------------------------------------------------------


async def triggers_from_stale_evidence(
    conn: asyncpg.Connection | None,
) -> list[TriggerHit]:
    """A card whose last successful sales read is older than
    ENGINE_REJECT_SALES_OLDER_THAN_MINUTES is not merely "due" under its
    tier's cadence - select_due() already handles that ordinary case via
    next_sales_due_at. This fires on a strictly stronger, independent
    claim: the evidence has already crossed the point where the
    valuation engine refuses to use it at all."""
    if conn is None:
        return []
    threshold_minutes = ENGINE_REJECT_SALES_OLDER_THAN_MINUTES + STALE_EVIDENCE_MARGIN_MINUTES
    rows = await conn.fetch(
        """
        SELECT source_card_id, last_sales_status_at
        FROM futgg_players
        WHERE is_active
          AND is_tradeable IS DISTINCT FROM FALSE
          AND last_sales_status_at IS NOT NULL
          AND last_sales_status_at <= now() - ($1 * interval '1 minute')
        """,
        threshold_minutes,
    )
    return [
        TriggerHit(
            int(r["source_card_id"]), REASON_STALE_EVIDENCE,
            {"last_sales_status_at": r["last_sales_status_at"].isoformat()},
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# Trigger #10: user explicitly requested current analysis
# ---------------------------------------------------------------------


async def _fetch_and_consume_priority_requests(
    conn: asyncpg.Connection | None,
) -> list[dict[str, Any]]:
    if conn is None:
        return []
    rows = await conn.fetch(
        """
        SELECT id, card_id, requested_by, requested_at
        FROM futgg_sales_priority_requests
        WHERE consumed_at IS NULL
        ORDER BY requested_at
        """
    )
    out = [dict(r) for r in rows]
    ids = [int(r["id"]) for r in out]
    if ids:
        await conn.execute(
            "UPDATE futgg_sales_priority_requests SET consumed_at = now() "
            "WHERE id = ANY($1::bigint[])",
            ids,
        )
    return out


def triggers_from_priority_requests(rows: Iterable[dict[str, Any]]) -> list[TriggerHit]:
    return [
        TriggerHit(int(r["card_id"]), REASON_USER_REQUESTED, {"requested_by": r.get("requested_by")})
        for r in rows
    ]


# ---------------------------------------------------------------------
# Combinator
# ---------------------------------------------------------------------


async def select_triggered(
    core_conn: asyncpg.Connection,
    *,
    watchlist_conn: asyncpg.Connection | None = None,
    exclude_ids: Iterable[int] = (),
    limit: int = TRIGGERED_SELECT_LIMIT,
) -> list[TriggeredCard]:
    """Combine every trigger source into one capped, reason-tagged
    selection, additional to (never overlapping - see `exclude_ids`)
    whatever select_due() already picked this cycle.

    User-driven reasons (see USER_DRIVEN_REASONS) are selected first,
    unconditionally, up to `limit`; background reasons only fill
    whatever of `limit` remains. See the RESERVED USER CAPACITY note in
    this module's docstring for why that ordering alone is the
    guarantee, with no separate forfeited-if-unused floor needed.
    """
    exclude = {int(x) for x in exclude_ids}

    price_change_rows = await _fetch_and_consume_price_change_events(core_conn)
    priority_request_rows = await _fetch_and_consume_priority_requests(core_conn)

    all_hits: list[TriggerHit] = []
    all_hits += triggers_from_price_change_events(price_change_rows)
    all_hits += triggers_from_priority_requests(priority_request_rows)
    all_hits += await triggers_from_watchlist(core_conn)
    all_hits += await triggers_from_recommendations(core_conn)
    all_hits += await triggers_from_new_releases(core_conn)
    all_hits += await triggers_from_market_activity(core_conn)
    all_hits += await triggers_from_stale_evidence(core_conn)
    all_hits += await triggers_from_active_alerts(watchlist_conn)

    return combine_trigger_hits(all_hits, exclude_ids=exclude, limit=limit)


def combine_trigger_hits(
    hits: Iterable[TriggerHit],
    *,
    exclude_ids: Iterable[int] = (),
    limit: int = TRIGGERED_SELECT_LIMIT,
) -> list[TriggeredCard]:
    """Pure combinator - group hits by card, apply the reserved-capacity
    cap, return capped output in stable (first-seen) order. Split out
    from select_triggered() so this, the part with actual decision
    logic, is testable without a DB at all."""
    exclude = {int(x) for x in exclude_ids}
    by_card: dict[int, dict[str, dict]] = {}
    order: list[int] = []
    for hit in hits:
        if hit.card_id in exclude:
            continue
        if hit.card_id not in by_card:
            by_card[hit.card_id] = {}
            order.append(hit.card_id)
        by_card[hit.card_id][hit.reason] = hit.detail

    def _is_user_driven(card_id: int) -> bool:
        return any(r in USER_DRIVEN_REASONS for r in by_card[card_id])

    user_ids = [cid for cid in order if _is_user_driven(cid)]
    background_ids = [cid for cid in order if not _is_user_driven(cid)]

    selected_user = user_ids[:limit] if limit > 0 else []
    background_capacity = max(0, limit - len(selected_user))
    selected_background = background_ids[:background_capacity]

    return [
        TriggeredCard(card_id=cid, reasons=tuple(sorted(by_card[cid])), details=by_card[cid])
        for cid in selected_user + selected_background
    ]


#: Exported for Part 6's shadow-mode capacity reporting - the number of
#: TRIGGERED_SELECT_LIMIT slots notionally set aside for user-driven
#: reasons. select_triggered() itself enforces the guarantee via
#: ordering (see above), not via this number, but a report of "how much
#: capacity was reserved for users this interval" needs a concrete
#: figure to compare actual user-driven selections against.
def reserved_user_capacity(limit: int = TRIGGERED_SELECT_LIMIT) -> int:
    return math.ceil(limit * TRIGGERED_USER_RESERVED_SHARE)
