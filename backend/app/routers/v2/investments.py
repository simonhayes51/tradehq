# app/routers/v2/investments.py
"""
Investment Engine API - separate surface from every existing trade/
opportunity endpoint (GET /api/v2/opportunities, /api/v2/trade-finder,
/api/market/fair-value/*). Answers "is this market segment likely to
appreciate over the next 1-3 days" (market cycles, cohort regimes,
accumulation windows), never "is there money in this one listing right
now" - see app/services/investment_engine/__init__.py's module docstring
for the full Trade-vs-Investment distinction.

Ungated (no entitlement check), mirroring the precedent already set by
GET /api/v2/market/regime and this file's sibling futgg_market.py: new,
provider-neutral surface, not a replacement for any gated feature. If
product wants a paid split later, follow fair_value.py's _teaser()
convention on top of this router - nothing below needs to change for
that.

Publishing: GET /signals and /candidates only ever return rows with
shadow = false (i.e. INVESTMENT_ENGINE_PUBLISH_SIGNALS was on when the
signal was created) UNLESS the caller is an admin (see require_admin) -
shadow-mode calculations stay internal-only, exactly as the shadow-mode
requirement specifies, without needing a second endpoint or a second
table.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.db import get_core_pool
from app.routers.admin import require_admin
from app.services.investment_engine import ledger
from app.services.investment_engine.config import INVESTMENT_ENGINE_CONFIG
from app.services.investment_engine.strategies import STRATEGIES

router = APIRouter(tags=["v2-investments"])
log = logging.getLogger("investments_router")

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _row_to_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "card_id": row["source_card_id"],
        "name": row.get("name"),
        "strategy": row["strategy_key"],
        "cohort": row["cohort_key"],
        "status": row["status"],
        "current_price": row.get("current_bin"),
        "max_buy": float(row["max_buy"]) if row["max_buy"] is not None else None,
        "target_price": float(row["target_price"]) if row["target_price"] is not None else None,
        "expected_roi_pct": float(row["expected_roi_pct"]) if row["expected_roi_pct"] is not None else None,
        "expected_hold_hours_min": row["expected_hold_hours_min"],
        "expected_hold_hours_max": row["expected_hold_hours_max"],
        "confidence": row["confidence"],
        "liquidity": row["liquidity"],
        "investability": row["investability"],
        "rationale": row["rationale"],
        "risk_flags": row["risk_flags"],
        "opened_at": row["opened_at"],
        "expires_at": row["expires_at"],
        "max_hold_at": row["max_hold_at"],
        "closed_at": row.get("closed_at"),
        "close_price": float(row["close_price"]) if row.get("close_price") is not None else None,
        "outcome": row.get("outcome"),
        "after_tax_profit": float(row["after_tax_profit"]) if row.get("after_tax_profit") is not None else None,
        "after_tax_roi_pct": float(row["after_tax_roi_pct"]) if row.get("after_tax_roi_pct") is not None else None,
        "exit_reason": row.get("exit_reason"),
    }


@router.get("/investments/strategies")
async def list_strategies() -> Dict[str, Any]:
    """Static list of implemented strategies - see
    app/services/investment_engine/strategies/__init__.py."""
    return {
        "strategies": [
            {
                "key": mod.KEY,
                "cohort": mod.COHORT_KEY,
                "uses_crash_trigger": mod.USES_CRASH_TRIGGER,
                "description": mod.DESCRIPTION,
            }
            for mod in STRATEGIES.values()
        ]
    }


@router.get("/investments/market-state")
async def get_market_state(
    strategy: Optional[str] = Query(None),
    cohort: Optional[str] = Query(None),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    """Latest regime snapshot per (strategy, cohort) - powers the simple
    'Premium Gold Market: CRASHING / BOTTOMING / INVESTMENT WINDOW OPEN'
    UI indicator. No statistical jargon in this response by design; the
    frontend maps `state` to plain-language copy itself."""
    where = ["TRUE"]
    args: List[Any] = []
    if strategy:
        args.append(strategy)
        where.append(f"strategy_key = ${len(args)}")
    if cohort:
        args.append(cohort)
        where.append(f"cohort_key = ${len(args)}")

    query = f"""
        SELECT DISTINCT ON (strategy_key, cohort_key)
            strategy_key, cohort_key, state, previous_state, transition_reason,
            drawdown_pct, severity_class, severity_score, momentum_current,
            deceleration_count, breadth_spread, confidence,
            eligible_card_count, candidate_count, computed_at
        FROM investment_market_states
        WHERE {' AND '.join(where)}
        ORDER BY strategy_key, cohort_key, computed_at DESC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return {"states": [dict(r) for r in rows]}


@router.get("/investments/signals")
async def list_signals(
    request: Request,
    strategy: Optional[str] = Query(None),
    cohort: Optional[str] = Query(None),
    status: str = Query("active", pattern="^(active|closed|all)$"),
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    budget: Optional[float] = Query(None, gt=0, description="If set, only signals with max_buy <= budget are returned"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    include_shadow: bool = Query(False, description="Admin-only: include shadow-mode (unpublished) signals"),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    # Shadow-mode signals (calculated but never meant to reach users -
    # see INVESTMENT_ENGINE_PUBLISH_SIGNALS) must never leak to a normal
    # caller just because they passed include_shadow=true. require_admin
    # raises 401/403 itself on failure, so this call is the actual gate -
    # not a Depends default that could silently no-op.
    if include_shadow:
        await require_admin(request)

    where = ["TRUE"]
    args: List[Any] = []

    if not include_shadow:
        where.append("s.shadow = FALSE")

    if status != "all":
        args.append(status)
        where.append(f"s.status = ${len(args)}")
    if strategy:
        args.append(strategy)
        where.append(f"s.strategy_key = ${len(args)}")
    if cohort:
        args.append(cohort)
        where.append(f"s.cohort_key = ${len(args)}")
    if min_confidence is not None:
        args.append(min_confidence)
        where.append(f"s.confidence >= ${len(args)}")
    if budget is not None:
        args.append(budget)
        where.append(f"s.max_buy <= ${len(args)}")

    args.extend([page_size, (page - 1) * page_size])
    query = f"""
        SELECT s.*, COALESCE(p.display_name, p.name) AS name, p.rating, p.primary_position, m.current_bin
        FROM investment_signals s
        LEFT JOIN futgg_players p ON p.source_card_id = s.source_card_id
        LEFT JOIN futgg_market_snapshot m ON m.source_card_id = s.source_card_id
        WHERE {' AND '.join(where)}
        ORDER BY s.confidence DESC NULLS LAST, s.opened_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return {"signals": [_row_to_signal(dict(r)) for r in rows], "page": page, "page_size": page_size}


@router.get("/investments/signals/{signal_id}")
async def get_signal(
    signal_id: int,
    request: Request,
    include_shadow: bool = Query(False, description="Admin-only: include a shadow-mode signal by id"),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    if include_shadow:
        await require_admin(request)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.*, COALESCE(p.display_name, p.name) AS name, p.rating, p.primary_position, m.current_bin
            FROM investment_signals s
            LEFT JOIN futgg_players p ON p.source_card_id = s.source_card_id
            LEFT JOIN futgg_market_snapshot m ON m.source_card_id = s.source_card_id
            WHERE s.id = $1
              AND ($2::boolean OR s.shadow = FALSE)
            """,
            signal_id,
            include_shadow,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="signal not found")
    return _row_to_signal(dict(row))


@router.get("/investments/ledger")
async def get_ledger(
    strategy: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    """Every closed signal, winners and losers alike - the Investment
    Ledger. Never filtered to only favourable outcomes."""
    where = ["s.status = 'closed'", "s.shadow = FALSE"]
    args: List[Any] = []
    if strategy:
        args.append(strategy)
        where.append(f"s.strategy_key = ${len(args)}")
    if outcome:
        args.append(outcome)
        where.append(f"s.outcome = ${len(args)}")
    args.extend([page_size, (page - 1) * page_size])
    query = f"""
        SELECT s.*, COALESCE(p.display_name, p.name) AS name, p.rating, p.primary_position, NULL AS current_bin
        FROM investment_signals s
        LEFT JOIN futgg_players p ON p.source_card_id = s.source_card_id
        WHERE {' AND '.join(where)}
        ORDER BY s.closed_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return {"ledger": [_row_to_signal(dict(r)) for r in rows], "page": page, "page_size": page_size}


@router.get("/investments/summary")
async def get_summary(
    window_days: int = Query(90, ge=1, le=365),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    """Track record per strategy, gated on a minimum sample size exactly
    like GET /api/v2/market/track-record - a handful of closed positions
    is not a track record."""
    return {"published": await ledger.track_record(pool, shadow=False, window_days=window_days)}


@router.get("/investments/shadow-report")
async def shadow_report(
    request: Request,
    window_hours: int = Query(168, ge=1, le=24 * 90),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    """Admin-only FC27 shadow-mode diagnostics: what the engine decided,
    and how those decisions turned out.

    Answers the questions that matter while the engine is running in
    shadow and nothing is user-visible - including, crucially, how the
    SUPPRESSED decisions performed. A suppression followed by a further
    decline is the engine working; the same suppression followed by a
    rally is the engine being too cautious. Neither is visible from the
    signals table alone, which is why investment_decision_log exists.
    """
    await require_admin(request)
    # Passed as a bound parameter rather than interpolated. window_hours
    # is already an int validated by Query(ge=1, le=...), so the literal
    # form was safe - but parameterising removes the possibility that a
    # future edit loosens the validation and quietly makes it unsafe,
    # and matches decision_log.py's own interval-binding pattern.
    interval = timedelta(hours=int(window_hours))

    async with pool.acquire() as conn:
        try:
            decisions = await conn.fetch(
                """
                SELECT strategy_key, cohort_key, decision, reason,
                       count(*) AS n,
                       avg(roi_after_24h_pct) AS avg_roi_24h,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY roi_after_24h_pct) AS median_roi_24h,
                       avg(roi_after_72h_pct) AS avg_roi_72h,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY roi_after_72h_pct) AS median_roi_72h,
                       count(*) FILTER (WHERE roi_after_24h_pct IS NOT NULL) AS graded_24h,
                       count(*) FILTER (WHERE roi_after_72h_pct IS NOT NULL) AS graded_72h
                FROM investment_decision_log
                WHERE decided_at >= now() - $1::interval
                GROUP BY strategy_key, cohort_key, decision, reason
                ORDER BY decision, n DESC
                """,
                interval,
            )
        except Exception:
            decisions = []

        try:
            by_state = await conn.fetch(
                """
                SELECT market_state, decision, count(*) AS n,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY roi_after_24h_pct) AS median_roi_24h
                FROM investment_decision_log
                WHERE decided_at >= now() - $1::interval AND market_state IS NOT NULL
                GROUP BY market_state, decision ORDER BY market_state
                """,
                interval,
            )
        except Exception:
            by_state = []

        signals = await conn.fetch(
            """
            SELECT strategy_key, cohort_key, shadow, status,
                   count(*) AS n,
                   count(*) FILTER (WHERE outcome = 'target_hit') AS target_hits,
                   count(*) FILTER (WHERE after_tax_roi_pct > 0) AS profitable,
                   avg(after_tax_roi_pct) AS avg_roi,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY after_tax_roi_pct) AS median_roi
            FROM investment_signals
            WHERE opened_at >= now() - $1::interval
            GROUP BY strategy_key, cohort_key, shadow, status
            ORDER BY strategy_key
            """,
            interval,
        )

    def _f(v):
        return round(float(v), 2) if v is not None else None

    return {
        "window_hours": window_hours,
        "engine": {
            "enabled": INVESTMENT_ENGINE_CONFIG.enabled,
            "publishing": INVESTMENT_ENGINE_CONFIG.publish_signals,
            "mode": (
                "DISABLED" if not INVESTMENT_ENGINE_CONFIG.enabled
                else "PUBLISHING" if INVESTMENT_ENGINE_CONFIG.publish_signals
                else "SHADOW"
            ),
        },
        "decisions": [
            {
                "strategy": r["strategy_key"], "cohort": r["cohort_key"],
                "decision": r["decision"], "reason": r["reason"], "count": r["n"],
                "graded_24h": r["graded_24h"], "graded_72h": r["graded_72h"],
                "median_roi_24h_pct": _f(r["median_roi_24h"]),
                "avg_roi_24h_pct": _f(r["avg_roi_24h"]),
                "median_roi_72h_pct": _f(r["median_roi_72h"]),
                "avg_roi_72h_pct": _f(r["avg_roi_72h"]),
            }
            for r in decisions
        ],
        "by_market_state": [
            {
                "market_state": r["market_state"], "decision": r["decision"],
                "count": r["n"], "median_roi_24h_pct": _f(r["median_roi_24h"]),
            }
            for r in by_state
        ],
        "signals": [
            {
                "strategy": r["strategy_key"], "cohort": r["cohort_key"],
                "shadow": r["shadow"], "status": r["status"], "count": r["n"],
                "target_hits": r["target_hits"], "profitable": r["profitable"],
                "avg_roi_pct": _f(r["avg_roi"]), "median_roi_pct": _f(r["median_roi"]),
            }
            for r in signals
        ],
    }


@router.get("/investments/budget-plan")
async def budget_plan(
    amount: float = Query(..., gt=0),
    strategy: Optional[str] = Query(None),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    """'I have N coins, where can I deploy it' - simple ranking + max-buy
    filter, not a portfolio optimizer (per explicit product guidance to
    prefer the simple version for a first pass). Greedily allocates the
    budget across active, published signals ordered by confidence, never
    putting the whole budget into one illiquid card - each signal caps
    its own allocation at max_buy * a modest multiple, bounded further by
    liquidity tier."""
    where = ["shadow = FALSE", "status = 'active'"]
    args: List[Any] = []
    if strategy:
        args.append(strategy)
        where.append(f"strategy_key = ${len(args)}")
    query = f"""
        SELECT s.*, COALESCE(p.display_name, p.name) AS name
        FROM investment_signals s
        LEFT JOIN futgg_players p ON p.source_card_id = s.source_card_id
        WHERE {' AND '.join(where)}
        ORDER BY s.confidence DESC NULLS LAST
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    liquidity_multiplier = {"high": 6, "medium": 3, "low": 1}
    remaining = amount
    allocations: List[Dict[str, Any]] = []
    for row in rows:
        if remaining <= 0:
            break
        max_buy = float(row["max_buy"])
        if max_buy <= 0:
            continue
        mult = liquidity_multiplier.get(row["liquidity"], 1)
        card_cap = max_buy * mult
        allocation = min(remaining, card_cap)
        copies = int(allocation // max_buy)
        if copies < 1:
            continue
        spend = copies * max_buy
        remaining -= spend
        allocations.append({
            **_row_to_signal(dict(row)),
            "suggested_copies": copies,
            "suggested_spend": spend,
        })

    return {
        "budget": amount,
        "allocated": amount - remaining,
        "unallocated": remaining,
        "allocations": allocations,
    }
