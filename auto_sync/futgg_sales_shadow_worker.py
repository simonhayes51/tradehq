#!/usr/bin/env python
"""Production SalesSync launcher with information-value rebalancing.

The live collector still uses the same FUT.GG request budget and the same
select_due() baseline. A bounded slice of that already-selected maintenance
capacity can now be reallocated to cards the information-value shadow has
repeatedly shown are worth refreshing sooner: held/watchlisted cards, live
recommendations, material price movers, recent releases and active-market cards.

Two guardrails matter here:

* Never-read backfill rows and explicit failure retries are protected. The
  rebalancer only displaces ordinary successful-maintenance rows.
* No extra FUT.GG requests are added. At a full 60-card production cycle the
  default 15% reserve is 9 cards, taken from the baseline cycle rather than
  appended to it.

The same shadow observation is still written, now against the final live due
set, so Admin can measure whether reallocation is actually fixing repeated
misses rather than merely moving them around.

Execution delegates to futgg_sales_sync.main(), not run_forever() directly, so
the existing one-card diagnostic and bounded-run modes remain intact.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from typing import Any

import asyncpg

import futgg_sales_sync as sales
import futgg_sales_triggers as triggers
from futgg_sales_information_shadow import (
    REASON_HELD_POSITION,
    SELECTOR_VERSION as SHADOW_SELECTOR_VERSION,
    ShadowSelection,
    USER_DRIVEN_REASONS,
    select_shadow,
)

log = logging.getLogger("futgg_sales_shadow_worker")

LIVE_REBALANCE_ENABLED = sales.env_bool(
    "FUTGG_SALES_INFORMATION_REBALANCE_ENABLED", True
)
LIVE_REBALANCE_SHARE = sales.env_float(
    "FUTGG_SALES_INFORMATION_REBALANCE_SHARE", 0.15, minimum=0.0
)
LIVE_REBALANCE_MAX_SLOTS = sales.env_int(
    "FUTGG_SALES_INFORMATION_REBALANCE_MAX_SLOTS", 12, minimum=0
)
LIVE_SELECTOR_VERSION = "information-live-rebalance-1.0"


def _row_value(row: Any, key: str, default=None):
    try:
        value = row[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


def _is_protected_baseline_row(row: Any) -> bool:
    """Rows the information reserve is never allowed to displace."""
    never_read = _row_value(row, "sales_first_read_at") is None
    failures = int(_row_value(row, "sales_consecutive_failures", 0) or 0)
    return never_read or failures > 0


def _reason_rank(card) -> int:
    """Lower is more important inside the bounded live reserve."""
    reasons = set(card.reasons)
    if reasons & USER_DRIVEN_REASONS:
        return 0
    if reasons & {
        triggers.REASON_LIVE_RECOMMENDATION,
        triggers.REASON_DISCORD_CANDIDATE,
        triggers.REASON_PROFIT_OPPORTUNITY,
        triggers.REASON_BULK_PRICE_CHANGE,
    }:
        return 1
    if reasons & {
        triggers.REASON_NEW_RELEASE,
        triggers.REASON_MARKET_ACTIVITY,
    }:
        return 2
    return 3


def rebalance_slot_count(due_count: int) -> int:
    if not LIVE_REBALANCE_ENABLED or due_count <= 0 or LIVE_REBALANCE_MAX_SLOTS <= 0:
        return 0
    if LIVE_REBALANCE_SHARE <= 0:
        return 0
    return min(
        LIVE_REBALANCE_MAX_SLOTS,
        max(1, math.ceil(due_count * LIVE_REBALANCE_SHARE)),
    )


def merge_rebalanced_rows(
    baseline_due: list[Any],
    candidate_rows: list[Any],
    *,
    slots: int,
) -> tuple[list[Any], list[int]]:
    """Replace ordinary baseline maintenance rows, never add requests.

    Backfill and failure-retry rows stay first and are never displaced.
    Information-value rows then run before routine maintenance so a circuit
    breaker cannot repeatedly cut them off at the tail of the cycle.
    """
    if slots <= 0 or not baseline_due or not candidate_rows:
        return list(baseline_due), []

    protected = [row for row in baseline_due if _is_protected_baseline_row(row)]
    maintenance = [row for row in baseline_due if not _is_protected_baseline_row(row)]
    replace_count = min(slots, len(candidate_rows), len(maintenance))
    if replace_count <= 0:
        return list(baseline_due), []

    chosen = candidate_rows[:replace_count]
    kept_maintenance = maintenance[:-replace_count]
    final_due = protected + chosen + kept_maintenance
    chosen_ids = [int(_row_value(row, "source_card_id")) for row in chosen]

    # A hard invariant: rebalancing is budget-neutral per cycle.
    if len(final_due) != len(baseline_due):
        raise AssertionError("SalesSync information rebalancing changed cycle request count")
    return final_due, chosen_ids


async def _hydrate_rebalance_candidates(
    conn: asyncpg.Connection,
    selection: ShadowSelection,
) -> list[Any]:
    """Turn shadow card ids into the exact row shape process_batch expects.

    Cards in failure backoff are excluded so information-value scheduling cannot
    defeat the collector's own retry/circuit-breaker protection. A current BIN
    is required because there is no reason to spend a detailed-sales request on
    a card the live market currently says is unavailable.
    """
    if not selection.cards:
        return []

    card_ids = [int(card.card_id) for card in selection.cards]
    rows = await conn.fetch(
        """
        SELECT p.source_card_id, p.rating, p.next_sales_due_at, p.source_url,
               p.sales_first_read_at, p.sales_consecutive_failures,
               COALESCE(cp.effective_tier, 'NORMAL') AS effective_tier,
               m.current_bin
        FROM futgg_players p
        LEFT JOIN card_priority cp ON cp.card_id = p.source_card_id
        LEFT JOIN futgg_market_snapshot m ON m.source_card_id = p.source_card_id
        WHERE p.source_card_id = ANY($1::bigint[])
          AND p.is_active
          AND p.is_tradeable IS DISTINCT FROM FALSE
          AND COALESCE(p.sales_consecutive_failures, 0) = 0
          AND p.source_url IS NOT NULL
          AND p.source_url <> ''
          AND m.current_bin IS NOT NULL
          AND m.current_bin > 0
        """,
        card_ids,
    )
    by_id = {int(row["source_card_id"]): row for row in rows}
    cards_by_id = {int(card.card_id): card for card in selection.cards}

    eligible_ids = [card_id for card_id in card_ids if card_id in by_id]
    eligible_ids.sort(
        key=lambda card_id: (
            _reason_rank(cards_by_id[card_id]),
            -int(_row_value(by_id[card_id], "rating", 0) or 0),
            -float(_row_value(by_id[card_id], "current_bin", 0) or 0),
            card_id,
        )
    )
    return [by_id[card_id] for card_id in eligible_ids]


async def _record_live_observation(
    conn: asyncpg.Connection,
    *,
    selection: ShadowSelection,
    final_due: list[Any],
    rebalanced_ids: list[int],
) -> None:
    """Record the same shadow evidence plus which candidates went live."""
    card_details = [
        {
            "card_id": int(card.card_id),
            "reasons": list(card.reasons),
            "details": card.details,
            "user_driven": any(reason in USER_DRIVEN_REASONS for reason in card.reasons),
            "rebalanced_live": int(card.card_id) in set(rebalanced_ids),
        }
        for card in selection.cards
    ]
    due_ids = [int(_row_value(row, "source_card_id")) for row in final_due]

    try:
        await conn.execute(
            """
            INSERT INTO futgg_trigger_shadow_observations (
                due_count, triggered_count, triggered_new_count,
                user_driven_count, held_position_count,
                due_card_ids, triggered_card_ids, reason_counts,
                selector_version, notes
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10::jsonb)
            """,
            len(set(due_ids)),
            len(selection.cards),
            len(selection.cards),
            selection.user_driven_count,
            selection.held_position_count,
            due_ids,
            [int(card.card_id) for card in selection.cards],
            json.dumps(selection.reason_counts),
            LIVE_SELECTOR_VERSION if rebalanced_ids else SHADOW_SELECTOR_VERSION,
            json.dumps({
                "live_scheduler_changed": bool(rebalanced_ids),
                "extra_sales_requests": 0,
                "rebalanced_card_ids": rebalanced_ids,
                "cards": card_details,
            }),
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        return
    except Exception:
        log.warning("information-value live observation write failed", exc_info=True)


async def run() -> None:
    original_select_due = sales.select_due
    watchlist_pool = None

    watchlist_dsn = (os.getenv("WATCHLIST_DATABASE_URL") or "").strip()
    core_dsn = (os.getenv("DATABASE_URL") or "").strip()
    watchlist_is_core = bool(watchlist_dsn and core_dsn and watchlist_dsn == core_dsn)

    if watchlist_dsn and not watchlist_is_core:
        try:
            watchlist_pool = await asyncpg.create_pool(
                dsn=watchlist_dsn,
                min_size=1,
                max_size=2,
                command_timeout=20,
            )
            log.info("sales information selector: separate watchlist/alert source enabled")
        except Exception:
            log.warning(
                "sales information selector: watchlist pool unavailable; continuing without alert source",
                exc_info=True,
            )
    elif watchlist_is_core:
        log.info("sales information selector: watchlist source shares core DB; no extra pool opened")

    async def information_rebalanced_select_due(conn):
        baseline_due = list(await original_select_due(conn))
        final_due = baseline_due
        rebalanced_ids: list[int] = []

        try:
            if watchlist_is_core:
                selection = await select_shadow(conn, due_rows=baseline_due, watchlist_conn=conn)
            elif watchlist_pool is None:
                selection = await select_shadow(conn, due_rows=baseline_due, watchlist_conn=None)
            else:
                async with watchlist_pool.acquire() as watchlist_conn:
                    selection = await select_shadow(
                        conn,
                        due_rows=baseline_due,
                        watchlist_conn=watchlist_conn,
                    )

            slots = rebalance_slot_count(len(baseline_due))
            candidates = await _hydrate_rebalance_candidates(conn, selection)
            final_due, rebalanced_ids = merge_rebalanced_rows(
                baseline_due,
                candidates,
                slots=slots,
            )
            await _record_live_observation(
                conn,
                selection=selection,
                final_due=final_due,
                rebalanced_ids=rebalanced_ids,
            )
            log.info(
                "sales information selector: due=%d shadow=%d rebalanced=%d slots=%d ids=%s",
                len(baseline_due),
                len(selection.cards),
                len(rebalanced_ids),
                slots,
                rebalanced_ids,
            )
        except Exception:
            # Fail closed to the mature baseline scheduler. Information-value
            # logic must never be able to stop sales collection.
            final_due = baseline_due
            log.warning(
                "sales information rebalancing failed; baseline due selection retained",
                exc_info=True,
            )
        return final_due

    sales.select_due = information_rebalanced_select_due
    log.info(
        "installed information-value SalesSync rebalancing: enabled=%s share=%.2f max_slots=%d; request budget unchanged",
        LIVE_REBALANCE_ENABLED,
        LIVE_REBALANCE_SHARE,
        LIVE_REBALANCE_MAX_SLOTS,
    )

    try:
        await sales.main()
    finally:
        sales.select_due = original_select_due
        if watchlist_pool is not None:
            await watchlist_pool.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
