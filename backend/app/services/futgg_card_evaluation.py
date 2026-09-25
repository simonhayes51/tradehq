"""Canonical fresh, single-card FUT.GG evaluation service.

This module exists for callers that genuinely need a fresh answer for one exact
card: player detail, Companion, Discord, AI tooling, held-position refresh and
operator diagnostics.

It is deliberately NOT a bulk/list abstraction. Opportunities, Trade Finder,
Dashboard and other list surfaces should read scanner-precomputed state rather
than call this repeatedly across hundreds or thousands of cards. Keeping that
boundary explicit prevents a tidy-looking refactor from reintroducing the CPU
and database hot-path regression that snapshot-backed list routes fixed.

The pure trading judgement still lives in futgg_intelligence.evaluate_card().
This service owns only the I/O contract around it so every fresh caller uses the
same current snapshot, completed-sales evidence and BIN-history fallback inputs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.services.futgg_intelligence import CardIntelligence, evaluate_card
from app.services.futgg_sales_window import filter_valuation_sales
from app.services.market_data_provider import FutggMarketDataProvider

log = logging.getLogger("futgg_card_evaluation")


@dataclass(frozen=True)
class FreshCardEvaluation:
    row: Dict[str, Any]
    intelligence: CardIntelligence


def _failed_card_intelligence(card_id: int) -> CardIntelligence:
    """Return an honest non-actionable result when one card cannot evaluate."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return CardIntelligence(
        card_id=int(card_id),
        signal="insufficient_data",
        status="insufficient_data",
        risk_level="high",
        signal_reasons=["Market analysis is temporarily unavailable for this card."],
        reason_codes=["evaluation_failed"],
        reasons=[{
            "code": "evaluation_failed",
            "severity": "blocker",
            "message": "Market analysis is temporarily unavailable for this card.",
        }],
        blocking_codes=["evaluation_failed"],
        evaluated_at=now,
    )


async def evaluate_fresh_card(
    provider: FutggMarketDataProvider,
    card_id: int,
    *,
    requested_by: str = "single_card",
) -> Optional[FreshCardEvaluation]:
    """Evaluate one exact FUT.GG card from the canonical live evidence set.

    Returns None only when the card itself is absent from the current FUT.GG
    market snapshot. Sales/BIN-history failures fail soft because the pure
    engine already has an honest INSUFFICIENT_DATA state; a transient evidence
    read should reduce confidence rather than turn an otherwise valid card page
    into a 500.

    `requested_by` is diagnostic context only. It never changes thresholds or
    calculations, which keeps Discord, Companion, held positions and the player
    page from developing caller-specific recommendation logic.
    """
    row_raw = await provider.get_player(int(card_id))
    if row_raw is None:
        return None
    row = dict(row_raw)

    sales = []
    try:
        sales = filter_valuation_sales(await provider.get_recent_sales(int(card_id)))
    except Exception:
        log.warning(
            "fresh card sales read failed card_id=%s requested_by=%s",
            card_id,
            requested_by,
            exc_info=True,
        )

    bin_observations = None
    try:
        by_card = await provider.get_bin_observations_by_ids([int(card_id)])
        bin_observations = by_card.get(int(card_id))
    except Exception:
        log.warning(
            "fresh card BIN-history read failed card_id=%s requested_by=%s",
            card_id,
            requested_by,
            exc_info=True,
        )

    try:
        intelligence = evaluate_card(
            row,
            sales=sales,
            bin_observations=bin_observations,
        )
    except Exception:
        log.warning(
            "fresh card evaluation failed card_id=%s requested_by=%s",
            card_id,
            requested_by,
            exc_info=True,
        )
        intelligence = _failed_card_intelligence(int(card_id))
    return FreshCardEvaluation(row=row, intelligence=intelligence)
