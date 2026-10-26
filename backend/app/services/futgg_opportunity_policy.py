"""One policy contract for app and Discord live BUY opportunities.

Presentation and delivery may differ by surface; the market decision and
its conservative economics must not. Any server-specific floor may make
the feed stricter, never looser than these shared defaults.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Optional

from app.services import trading_math as tm
from app.services.futgg_config import ENGINE_CONFIG

MIN_OPPORTUNITY_CONFIDENCE = float(ENGINE_CONFIG.min_confidence_for_buy_signal)
MIN_OPPORTUNITY_ROI = float(ENGINE_CONFIG.min_net_roi_for_buy)
MIN_OPPORTUNITY_LIQUIDITY = float(
    os.getenv("LIVE_OPPORTUNITY_MIN_LIQUIDITY_SCORE", "0.30")
)
MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES = int(
    os.getenv("MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES", str(48 * 60))
)
# Bulk BIN checks run every ~5 minutes; the materialized view refreshes
# asynchronously. A 3-minute limit creates predictable gaps where every
# candidate looks stale despite healthy ingestion. Bound at 10 minutes.
MAX_OPPORTUNITY_PRICE_AGE_MINUTES = int(
    os.getenv("LIVE_OPPORTUNITY_MAX_PRICE_AGE_MINUTES", "10")
)


def minimum_profit_at_max_buy(ci: Any) -> Optional[Decimal]:
    """Net profit if bought at the advertised ceiling and sold at target."""
    entry = getattr(ci, "recommended_buy_max", None)
    target = getattr(ci, "recommended_sell_target", None)
    if entry is None or target is None:
        return None
    return tm.net_profit(Decimal(str(target)), Decimal(str(entry)))


def roi_at_max_buy(ci: Any) -> Optional[Decimal]:
    """Net ROI under the same conservative entry assumption as the ceiling."""
    entry = getattr(ci, "recommended_buy_max", None)
    target = getattr(ci, "recommended_sell_target", None)
    if entry is None or target is None:
        return None
    return tm.net_roi(Decimal(str(target)), Decimal(str(entry)))
