# app/services/held_position_refresher.py
#
# Refreshes the live HOLD/SELL verdict for every open portfolio trade.
#
# FUT.GG source_card_id and legacy FUTBIN fut_players.card_id are separate
# namespaces. New trades carry trades.card_source explicitly. Historical rows
# may still have card_source=NULL; only those rows use the compatibility probe,
# and when a source is proven the row is classified so future passes do not
# have to guess again.
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Tuple

import asyncpg

from app.services.futgg_card_evaluation import evaluate_fresh_card
from app.services.held_position_decision import HeldPositionDecision, decide_held_position
from app.services.market_data_provider import FutggMarketDataProvider
from app.services.recommendation_engine_v2 import evaluate_card as evaluate_legacy_card

log = logging.getLogger("held_position_refresher")

REFRESH_LOCK_KEY = 7741010  # distinct from every other REFRESH_LOCK_KEY in this codebase


async def _evaluate_futgg_position(
    provider: FutggMarketDataProvider,
    card_id: int,
    purchase_price: int,
    original_target: Optional[int],
) -> Optional[HeldPositionDecision]:
    evaluated = await evaluate_fresh_card(
        provider,
        card_id,
        requested_by="held_position_refresher",
    )
    if evaluated is None:
        return None
    return decide_held_position(
        evaluated.row,
        evaluated.intelligence,
        purchase_price=purchase_price,
        original_target=original_target,
    )


async def _evaluate_legacy_position(
    player_pool: asyncpg.Pool,
    *,
    card_id: int,
    platform: str,
    purchase_price: int,
) -> Optional[Tuple[str, Optional[str]]]:
    async with player_pool.acquire() as eval_conn:
        exists = await eval_conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM fut_players WHERE card_id = $1)",
            card_id,
        )
        if not exists:
            return None

        legacy = await evaluate_legacy_card(
            eval_conn,
            card_id,
            platform=platform,
            is_held=True,
            held_purchase_price=purchase_price,
            requested_by="held_position_refresher",
        )
        if legacy is None:
            return None
        return (
            legacy.held_decision or legacy.status,
            " ".join(legacy.held_decision_reasons)
            if legacy.held_decision_reasons else None,
        )


async def refresh_held_positions(pool: asyncpg.Pool, player_pool: asyncpg.Pool) -> int:
    """One pass over every open trade with a known card_id.

    Explicit card_source is authoritative. Historical NULL-source rows are
    probed FUT.GG-first, then legacy, and classified after a successful probe.
    FUT.GG positions use purchase-aware exit logic; discovery targets alone can
    no longer turn an after-tax loss into an automatic SELL.
    """
    written = 0
    async with pool.acquire() as conn:
        open_trades = await conn.fetch(
            """
            SELECT trade_id, card_id, card_source, buy, platform, target_sell
            FROM trades
            WHERE status = 'open' AND card_id IS NOT NULL AND buy IS NOT NULL
            """
        )

    futgg_provider = FutggMarketDataProvider(pool)

    for row in open_trades:
        trade_id = row["trade_id"]
        card_id = int(row["card_id"])
        card_source = row["card_source"]
        platform = row["platform"] or "ps"
        purchase_price = int(row["buy"])
        original_target = int(row["target_sell"]) if row["target_sell"] else None
        status: Optional[str] = None
        reasoning: Optional[str] = None
        resolved_source: Optional[str] = card_source
        analysis_json: Optional[str] = None

        try:
            if card_source == "futgg":
                result = await _evaluate_futgg_position(
                    futgg_provider, card_id, purchase_price, original_target
                )
                if result is None:
                    log.warning(
                        "held_position_refresher: skipping trade_id=%s card_id=%s because explicit source=futgg but card is missing from futgg_market_snapshot",
                        trade_id,
                        card_id,
                    )
                    continue
                status, reasoning = result.verdict, result.reasoning
                analysis_json = json.dumps(result.as_dict())

            elif card_source == "futbin":
                result = await _evaluate_legacy_position(
                    player_pool,
                    card_id=card_id,
                    platform=platform,
                    purchase_price=purchase_price,
                )
                if result is None:
                    log.warning(
                        "held_position_refresher: skipping trade_id=%s card_id=%s because explicit source=futbin but card is missing from legacy fut_players",
                        trade_id,
                        card_id,
                    )
                    continue
                status, reasoning = result

            else:
                # Compatibility path for historical positions created before
                # the source namespace was persisted. Probe, then remember the
                # proven source so this ambiguity disappears after one pass.
                futgg_result = await _evaluate_futgg_position(
                    futgg_provider,
                    card_id,
                    purchase_price,
                    original_target,
                )
                if futgg_result is not None:
                    status, reasoning = futgg_result.verdict, futgg_result.reasoning
                    analysis_json = json.dumps(futgg_result.as_dict())
                    resolved_source = "futgg"
                else:
                    legacy_result = await _evaluate_legacy_position(
                        player_pool,
                        card_id=card_id,
                        platform=platform,
                        purchase_price=purchase_price,
                    )
                    if legacy_result is None:
                        log.warning(
                            "held_position_refresher: skipping trade_id=%s card_id=%s because unclassified card exists in neither FUT.GG nor legacy FUTBIN catalogue",
                            trade_id,
                            card_id,
                        )
                        continue
                    status, reasoning = legacy_result
                    resolved_source = "futbin"

        except Exception:
            log.exception(
                "held_position_refresher: evaluation failed for trade_id=%s card_id=%s source=%s",
                trade_id,
                card_id,
                card_source or "unclassified",
            )
            continue

        if status is None:
            continue

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE trades
                SET card_source = COALESCE(card_source, $4),
                    current_recommendation_status = $2,
                    current_recommendation_reasoning = $3,
                    current_position_analysis = $5::jsonb,
                    current_evaluated_at = now()
                WHERE trade_id = $1
                """,
                trade_id,
                status,
                reasoning,
                resolved_source,
                analysis_json,
            )
        written += 1

    return written


async def refresher_loop(
    pool: asyncpg.Pool,
    player_pool: asyncpg.Pool,
    interval_seconds: int = 900,
) -> None:
    """Background task started from the app lifespan. Same advisory-lock
    pattern as the other guarded refresh loops."""
    await asyncio.sleep(10)
    while True:
        try:
            async with pool.acquire() as conn:
                got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", REFRESH_LOCK_KEY)
                if got:
                    try:
                        n = await refresh_held_positions(pool, player_pool)
                        if n:
                            log.info("held_position_refresher: updated %d open trades", n)
                    finally:
                        await conn.execute("SELECT pg_advisory_unlock($1)", REFRESH_LOCK_KEY)
        except Exception as e:  # never let the loop die
            log.error("held_position_refresher iteration failed: %s", e)
        await asyncio.sleep(interval_seconds)
