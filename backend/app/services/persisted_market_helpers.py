"""Production replacements for legacy bulk market scoring helpers.

A few old/background consumers still lazily import `_segmented_candidates`,
`_sales_for_rows` and `_score_and_filter` from futgg_market. The web list routes
and Dashboard no longer need those live-evaluation helpers, but Discord's live
publisher does that import inside every poll.

Rather than duplicate Discord's posting/cooldown/budget logic or let it run a
second market-wide recommendation pass, production bootstrap installs the
functions below onto the futgg_market module. They preserve the old helper
signatures while sourcing already-computed CardIntelligence from the scanner's
current-state table.

This installer never touches futgg_scanner. The scanner owns evaluation; these
helpers are read/filter only.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.snapshot_market_surfaces import load_market_surface_pairs

log = logging.getLogger("persisted_market_helpers")

_CI_KEY = "__eafc_persisted_intelligence__"


async def persisted_segmented_candidates(provider) -> List[Dict[str, Any]]:
    """Return current actionable rows carrying their persisted CI privately."""
    pairs = await load_market_surface_pairs(provider)
    rows: List[Dict[str, Any]] = []
    for row, ci in pairs:
        carried = dict(row)
        carried[_CI_KEY] = ci
        rows.append(carried)
    return rows


async def persisted_sales_for_rows(provider, rows):
    """Compatibility no-op: current intelligence already contains the result."""
    return {}


async def persisted_score_and_filter(
    rows: List[Dict[str, Any]], *, risk: Optional[str] = None,
    min_expected_profit: Optional[float] = None, min_roi: Optional[float] = None,
    min_confidence: Optional[float] = None, min_liquidity: Optional[float] = None,
    signals: Optional[set] = None,
    require_confirmed_sales: bool = False,
    max_evidence_age_minutes: Optional[int] = None,
    sales_by_card=None,
    bin_by_card=None,
):
    """Filter persisted CardIntelligence with futgg_market's old semantics.

    `sales_by_card` / `bin_by_card` are accepted only for signature
    compatibility. No evidence is reloaded and evaluate_card() is never called.
    """
    out = []
    for carried in rows:
        ci = carried.get(_CI_KEY)
        if ci is None:
            # Fail closed. A row without persisted intelligence must never fall
            # back to live evaluation through this production replacement.
            continue
        if require_confirmed_sales and (
            ci.valuation_source != "completed_sales" or ci.evidence_quality != "confirmed"
        ):
            continue
        if max_evidence_age_minutes is not None and (
            ci.evidence_age_minutes is None or ci.evidence_age_minutes > max_evidence_age_minutes
        ):
            continue
        if signals is not None and ci.signal not in signals:
            continue
        if risk is not None and ci.risk_level != risk:
            continue
        if min_expected_profit is not None and (
            ci.expected_profit_after_tax is None
            or float(ci.expected_profit_after_tax) < min_expected_profit
        ):
            continue
        if min_roi is not None and (
            ci.expected_roi is None or float(ci.expected_roi) < min_roi
        ):
            continue
        if min_confidence is not None and (
            ci.confidence_score is None or ci.confidence_score < min_confidence
        ):
            continue
        if min_liquidity is not None and (
            ci.liquidity_score is None or ci.liquidity_score < min_liquidity
        ):
            continue
        clean = {key: value for key, value in carried.items() if key != _CI_KEY}
        out.append((clean, ci))
    return out


def install_persisted_market_helpers() -> None:
    """Redirect lazy futgg_market helper imports to scanner-persisted state."""
    from app.routers.v2 import futgg_market as market

    market._segmented_candidates = persisted_segmented_candidates
    market._sales_for_rows = persisted_sales_for_rows
    market._score_and_filter = persisted_score_and_filter
    log.info("installed persisted-only bulk market helpers")
