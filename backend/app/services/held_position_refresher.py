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
import logging
from typing import Any, Optional, Tuple

import asyncpg

from app.services import trading_math as tm
from app.services.futgg_card_evaluation import evaluate_fresh_card
from app.services.futgg_intelligence import CardIntelligence
from app.services.futgg_trend import DOWNTREND, FALLING_KNIFE
from app.services.market_data_provider import FutggMarketDataProvider
from app.services.recommendation_engine_v2 import evaluate_card as evaluate_legacy_card

log = logging.getLogger("held_position_refresher")

REFRESH_LOCK_KEY = 7741010  # distinct from every other REFRESH_LOCK_KEY in this codebase


def _futgg_held_verdict(
    market_row: dict[str, Any],
    ci: CardIntelligence,
    purchase_price: int,
) -> Tuple[str, str]:
    """Convert current FUT.GG intelligence into a conservative HOLD/SELL
    decision for a card the user already owns.

    FUT.GG's engine is deliberately a mispricing detector, not a price
    forecaster, so this must not pretend `recommended_sell_target` is a
    guaranteed future price. A SELL is emitted only for an observable sell
    condition: target reached, engine explicitly says sell/avoid, or a
    profitable position has moved into a confirmed negative trend. Everything
    else stays HOLD rather than fabricating a future-price forecast.
    """
    current_raw = market_row.get("current_bin")
    if current_raw is None:
        return "HOLD", "Live FUT.GG price is temporarily unavailable; no verified sell trigger can be confirmed."

    current = int(current_raw)
    purchase = int(purchase_price)
    current_profit = tm.net_profit(current, purchase)
    current_roi = tm.net_roi(current, purchase)
    target = int(ci.recommended_sell_target) if ci.recommended_sell_target is not None else None

    if target is not None and current >= target:
        return (
            "SELL",
            f"Current BIN {current:,} is above the live sell target of {target:,}. Consider selling now to lock in the available return.",
        )

    if ci.signal in {"sell", "avoid"}:
        detail = " ".join(ci.signal_reasons or [])
        suffix = f" {detail}" if detail else ""
        return "SELL", f"The live FUT.GG engine now rates this position {ci.signal.upper()}.{suffix}".strip()

    if ci.trend_state in {FALLING_KNIFE, DOWNTREND} and current_profit is not None and current_profit > 0:
        roi_text = f" ({float(current_roi) * 100:.1f}% after tax)" if current_roi is not None else ""
        return (
            "SELL",
            f"The position is profitable now{roi_text}, but the live market trend has turned {ci.trend_state.replace('_', ' ')}.",
        )

    target_text = f" Live sell target: {target:,}." if target is not None else ""
    if current_profit is not None:
        pnl_text = f" Current after-tax P/L at {current:,} BIN is {int(current_profit):+,} coins."
    else:
        pnl_text = ""

    if ci.signal == "insufficient_data":
        return (
            "HOLD",
            "No verified sell trigger is present, but current market evidence is insufficient for a stronger call."
            + pnl_text
            + target_text,
        )

    return (
        "HOLD",
        "No live FUT.GG sell trigger is present."
        + pnl_text
        + target_text,
    )


async def _evaluate_futgg_position(
    provider: FutggMarketDataProvider,
    card_id: int,
    purchase_price: int,
) -> Optional[Tuple[str, str]]:
    evaluated = await evaluate_fresh_card(
        provider,
        card_id,
        requested_by="held_position_refresher",
    )
    if evaluated is None:
        return None
    return _futgg_held_verdict(
        evaluated.row,
        evaluated.intelligence,
        purchase_price,
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
    This preserves old positions without allowing a bare numeric id to keep
    choosing a catalogue forever.
    """
    written = 0
    async with pool.acquire() as conn:
        open_trades = await conn.fetch(
            """
            SELECT trade_id, card_id, card_source, buy, platform
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
        status: Optional[str] = None
        reasoning: Optional[str] = None
        resolved_source: Optional[str] = card_source

        try:
            if card_source == "futgg":
                result = await _evaluate_futgg_position(futgg_provider, card_id, purchase_price)
                if result is None:
                    log.warning(
                        "held_position_refresher: skipping trade_id=%s card_id=%s because explicit source=futgg but card is missing from futgg_market_snapshot",
                        trade_id,
                        card_id,
                    )
                    continue
                status, reasoning = result

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
                )
                if futgg_result is not None:
                    status, reasoning = futgg_result
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
                    current_evaluated_at = now()
                WHERE trade_id = $1
                """,
                trade_id,
                status,
                reasoning,
                resolved_source,
            )
        written += 1

    return written


async def refresher_loop(
    pool: asyncpg.Pool,
    player_pool: asyncpg.Pool,
    interval_seconds: int = 900,
) -> None:
    """Background task started from the app lifespan. Same advisory-lock
    pattern as fair_value.refresher_loop / event_impact.refresher_loop."""
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
