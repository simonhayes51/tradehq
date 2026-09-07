#!/usr/bin/env python
"""Read-only shadow selector for information-value detailed-sales scheduling.

This deliberately sits beside, not inside, futgg_sales_sync.select_due(). The
live scheduler keeps choosing exactly the same cards and making exactly the same
number of FUT.GG requests. After that choice, this module asks which additional
cards current product/user signals would have prioritised and records the answer
for later analysis.

Unlike futgg_sales_triggers.select_triggered(), the shadow path NEVER marks price
change events or explicit refresh requests consumed. Observing a request is not
fulfilling it.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

import asyncpg

import futgg_sales_triggers as triggers

log = logging.getLogger("futgg_sales_information_shadow")

SELECTOR_VERSION = "information-shadow-1.0"

REASON_HELD_POSITION = "held_position"
USER_DRIVEN_REASONS = frozenset(set(triggers.USER_DRIVEN_REASONS) | {REASON_HELD_POSITION})


@dataclass(frozen=True)
class ShadowSelection:
    cards: tuple[triggers.TriggeredCard, ...]
    due_ids: tuple[int, ...]

    @property
    def reason_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for card in self.cards:
            counts.update(card.reasons)
        return dict(counts)

    @property
    def user_driven_count(self) -> int:
        return sum(1 for card in self.cards if any(r in USER_DRIVEN_REASONS for r in card.reasons))

    @property
    def held_position_count(self) -> int:
        return sum(1 for card in self.cards if REASON_HELD_POSITION in card.reasons)


async def _peek_price_change_events(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, card_id, previous_price, new_price
        FROM futgg_price_change_events
        WHERE consumed_at IS NULL
          AND detected_at >= now() - ($1 * interval '1 minute')
        ORDER BY detected_at DESC
        """,
        triggers.PRICE_CHANGE_LOOKBACK_MINUTES,
    )
    return [dict(row) for row in rows]


async def _peek_priority_requests(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, card_id, requested_by, requested_at
        FROM futgg_sales_priority_requests
        WHERE consumed_at IS NULL
        ORDER BY requested_at
        """
    )
    return [dict(row) for row in rows]


async def triggers_from_held_positions(conn: asyncpg.Connection) -> list[triggers.TriggerHit]:
    """Open positions are direct user exposure and deserve fresh evidence.

    card_source was added specifically to remove the FUT.GG/FUTBIN id-space
    ambiguity. Fail soft during a partially-migrated deploy rather than ever
    guessing that an unlabelled historical id is FUT.GG.
    """
    try:
        rows = await conn.fetch(
            """
            SELECT card_id, count(*) AS position_count
            FROM trades
            WHERE status = 'open'
              AND card_source = 'futgg'
              AND card_id IS NOT NULL
            GROUP BY card_id
            """
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        return []
    except Exception:
        log.warning("held-position shadow query failed; degrading to none", exc_info=True)
        return []

    return [
        triggers.TriggerHit(
            int(row["card_id"]),
            REASON_HELD_POSITION,
            {"position_count": int(row["position_count"] or 0)},
        )
        for row in rows
    ]


def combine_information_hits(
    hits: Iterable[triggers.TriggerHit],
    *,
    exclude_ids: Iterable[int] = (),
    limit: int = triggers.TRIGGERED_SELECT_LIMIT,
) -> list[triggers.TriggeredCard]:
    """Same capped combinator semantics, with held positions user-driven."""
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

    def is_user(card_id: int) -> bool:
        return any(reason in USER_DRIVEN_REASONS for reason in by_card[card_id])

    user_ids = [card_id for card_id in order if is_user(card_id)]
    background_ids = [card_id for card_id in order if not is_user(card_id)]
    selected = user_ids[:limit]
    selected += background_ids[: max(0, limit - len(selected))]

    return [
        triggers.TriggeredCard(
            card_id=card_id,
            reasons=tuple(sorted(by_card[card_id])),
            details=by_card[card_id],
        )
        for card_id in selected
    ]


async def select_shadow(
    core_conn: asyncpg.Connection,
    *,
    due_rows: Iterable[Any],
    watchlist_conn: asyncpg.Connection | None = None,
    limit: int = triggers.TRIGGERED_SELECT_LIMIT,
) -> ShadowSelection:
    due_rows = list(due_rows)
    due_ids = tuple(int(row["source_card_id"]) for row in due_rows)

    price_rows = await _peek_price_change_events(core_conn)
    request_rows = await _peek_priority_requests(core_conn)

    hits: list[triggers.TriggerHit] = []
    hits += triggers.triggers_from_price_change_events(price_rows)
    hits += triggers.triggers_from_priority_requests(request_rows)
    hits += await triggers.triggers_from_watchlist(core_conn)
    hits += await triggers.triggers_from_recommendations(core_conn)
    hits += await triggers.triggers_from_new_releases(core_conn)
    hits += await triggers.triggers_from_market_activity(core_conn)
    hits += await triggers.triggers_from_stale_evidence(core_conn)
    hits += await triggers.triggers_from_active_alerts(watchlist_conn)
    hits += await triggers_from_held_positions(core_conn)

    cards = combine_information_hits(hits, exclude_ids=due_ids, limit=limit)
    return ShadowSelection(cards=tuple(cards), due_ids=due_ids)


async def record_shadow(conn: asyncpg.Connection, selection: ShadowSelection) -> None:
    """Best-effort observability only. Never affects the live selection."""
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
            len(set(selection.due_ids)),
            len(selection.cards),
            len(selection.cards),
            selection.user_driven_count,
            selection.held_position_count,
            list(selection.due_ids),
            [card.card_id for card in selection.cards],
            json.dumps(selection.reason_counts),
            SELECTOR_VERSION,
            json.dumps({"live_scheduler_changed": False, "extra_sales_requests": 0}),
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        # Migration 087 may still be landing while the worker starts.
        return
    except Exception:
        log.warning("information-value shadow observation write failed", exc_info=True)


async def observe_after_due_selection(
    conn: asyncpg.Connection,
    due_rows: Iterable[Any],
    *,
    watchlist_conn: asyncpg.Connection | None = None,
) -> ShadowSelection:
    selection = await select_shadow(
        conn,
        due_rows=due_rows,
        watchlist_conn=watchlist_conn,
    )
    await record_shadow(conn, selection)
    log.info(
        "sales information shadow: due=%d triggered_new=%d user_driven=%d held=%d reasons=%s",
        len(set(selection.due_ids)),
        len(selection.cards),
        selection.user_driven_count,
        selection.held_position_count,
        selection.reason_counts,
    )
    return selection
