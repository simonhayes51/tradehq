# app/services/investment_engine/ledger.py
"""
The Investment Ledger: opens, grades, and closes investment_signals rows
(migration 060).

Mirrors app/services/futgg_outcome_grader.py's NO-HINDSIGHT discipline
exactly: grading only ever looks at the CURRENT price/state, the same
information a user would see right now - never a future/best price in a
window, never a future cohort label, never a future-known regime exit.
Winning, losing and flat outcomes are all recorded; nothing here ever
selects only favourable closes.

Tax math is never reimplemented here - every profit/ROI figure comes
from app.services.trading_math (net_profit/net_roi), the repo's single
source of truth for EA's 5% sale tax, per that module's own "nothing
else may reimplement these formulas" contract.

Write failures are best-effort and rate-limited exactly like
futgg_recommendation_store.py's _log_write_failure - a ledger write must
never break the caller (an engine recompute pass), and a systemic outage
should log once loudly, not once per card.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.services import trading_math as tm

Number = Decimal | int | float | str

log = logging.getLogger("investment_ledger")

STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"

OUTCOME_TARGET_HIT = "target_hit"
OUTCOME_PROFITABLE_UNREALISED = "profitable_unrealised"
OUTCOME_FLAT = "flat"
OUTCOME_LOSS_UNREALISED = "loss_unrealised"
OUTCOME_REGIME_EXIT = "regime_exit"
OUTCOME_MAX_HOLD = "max_hold_reached"
OUTCOME_INVALIDATED = "invalidated"

_FLAT_BAND = Decimal("0.005")

_write_failure_counts: Dict[str, int] = {}
_write_failure_last_log: Dict[str, float] = {}
_WRITE_FAILURE_LOG_INTERVAL_SECONDS = 60.0


def _log_write_failure(exc: BaseException, card_id: Any) -> None:
    key = type(exc).__name__
    now = time.monotonic()
    count = _write_failure_counts.get(key, 0) + 1
    _write_failure_counts[key] = count
    last = _write_failure_last_log.get(key)
    if last is None:
        _write_failure_last_log[key] = now
        log.warning(
            "investment ledger write failed (card_id=%s, %s) - further failures of this "
            "type will be summarised, not logged individually", card_id, key, exc_info=True,
        )
        return
    if now - last >= _WRITE_FAILURE_LOG_INTERVAL_SECONDS:
        _write_failure_last_log[key] = now
        log.warning("investment ledger still failing to write: %s x%d since start (most recent card_id=%s)",
                     key, count, card_id)


def _mark_to_market_outcome(roi: Optional[Decimal]) -> str:
    if roi is not None and roi > _FLAT_BAND:
        return OUTCOME_PROFITABLE_UNREALISED
    if roi is not None and roi < -_FLAT_BAND:
        return OUTCOME_LOSS_UNREALISED
    return OUTCOME_FLAT


@dataclass
class ExitDecision:
    outcome: str
    reason: str
    profit: Decimal
    roi_pct: Optional[float]


def evaluate_active_signal_exit(
    *, current_price: Optional[float], entry_price: Number, target_price: Number,
    now, max_hold_at,
) -> Optional[ExitDecision]:
    """The pure NO-HINDSIGHT exit decision at the heart of both
    grade_open_signals() (live, wall-clock `now`) and the FC26 replay
    harness (app/services/investment_engine/replay/, simulated `now`) -
    extracted so the two callers can never drift into two different
    ideas of "should this signal close". Only ever looks at the single
    `current_price`/`now` it's given - never a future/best price, never
    a future max-hold check evaluated against the wrong clock.

    Returns None if nothing warrants closing yet (no price, target not
    hit, max hold not reached)."""
    if current_price is None:
        return None
    if current_price >= float(target_price):
        roi = tm.net_roi(current_price, entry_price)
        return ExitDecision(
            outcome=OUTCOME_TARGET_HIT, reason="profit_target_hit",
            profit=tm.net_profit(current_price, entry_price),
            roi_pct=(float(roi) * 100) if roi is not None else None,
        )
    if now >= max_hold_at:
        roi = tm.net_roi(current_price, entry_price)
        return ExitDecision(
            outcome=_mark_to_market_outcome(roi), reason=OUTCOME_MAX_HOLD,
            profit=tm.net_profit(current_price, entry_price),
            roi_pct=(float(roi) * 100) if roi is not None else None,
        )
    return None


_INSERT_SIGNAL = """
INSERT INTO investment_signals (
    game_year, source_card_id, strategy_key, cohort_key, status, shadow,
    entry_price, max_buy, target_price, expected_roi_pct,
    expected_hold_hours_min, expected_hold_hours_max, confidence,
    liquidity, investability, max_position_cards,
    rationale, risk_flags, market_state_id,
    opened_at, expires_at, max_hold_at, engine_version
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23
)
ON CONFLICT (source_card_id, strategy_key) WHERE status = 'active' DO NOTHING
RETURNING id
"""


async def open_signal(
    conn, *, game_year: int, card_id: int, strategy_key: str, cohort_key: str,
    entry_price: float, max_buy: float, target_price: float,
    expected_roi_pct: Optional[float], expected_hold_hours_min: Optional[float],
    expected_hold_hours_max: Optional[float], confidence: Optional[float],
    liquidity: Optional[str], investability: Optional[str], max_position_cards: Optional[int],
    rationale: Optional[List[str]], risk_flags: Optional[List[str]], market_state_id: Optional[int],
    max_hold_hours: float, engine_version: str, shadow: bool,
) -> Optional[int]:
    """Open one active investment signal. Idempotent by construction: a
    card that already holds an active signal under this strategy is a
    silent no-op via the partial unique index (migration 060), so a
    recompute pass can call this every cycle without ever duplicating an
    open position."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=max_hold_hours)
    try:
        return await conn.fetchval(
            _INSERT_SIGNAL, game_year, card_id, strategy_key, cohort_key, STATUS_ACTIVE, shadow,
            entry_price, max_buy, target_price, expected_roi_pct,
            expected_hold_hours_min, expected_hold_hours_max, confidence,
            liquidity, investability, max_position_cards,
            json.dumps(rationale or []), json.dumps(risk_flags or []), market_state_id,
            now, expires_at, expires_at, engine_version,
        )
    except Exception as exc:
        _log_write_failure(exc, card_id)
        return None


_SELECT_ACTIVE_FOR_STRATEGY = """
    SELECT id, source_card_id, strategy_key, cohort_key, entry_price, target_price, max_hold_at
    FROM investment_signals
    WHERE status = 'active' AND strategy_key = $1
    ORDER BY opened_at
"""

_CLOSE_SIGNAL = """
    UPDATE investment_signals
    SET status = 'closed', closed_at = $2, close_price = $3, outcome = $4,
        after_tax_profit = $5, after_tax_roi_pct = $6, exit_reason = $7, updated_at = now()
    WHERE id = $1
"""

PriceLookup = Callable[[int], Awaitable[Optional[float]]]


async def grade_open_signals(conn, *, strategy_key: str, current_price_lookup: PriceLookup) -> int:
    """Walk every active signal for `strategy_key` and close it if the
    market has moved through its exit conditions (profit target, or max
    hold reached). `current_price_lookup(card_id)` is injected rather
    than queried directly so this function stays testable against a
    fake, and so it never hardcodes which live table backs "current
    price".

    NO HINDSIGHT: only ever looks at the price this call is given -
    never a future/best price. Mirrors futgg_outcome_grader.py exactly.
    """
    rows = await conn.fetch(_SELECT_ACTIVE_FOR_STRATEGY, strategy_key)
    now = datetime.now(timezone.utc)
    closed = 0
    for row in rows:
        try:
            price = await current_price_lookup(row["source_card_id"])
            decision = evaluate_active_signal_exit(
                current_price=price, entry_price=row["entry_price"], target_price=row["target_price"],
                now=now, max_hold_at=row["max_hold_at"],
            )
            if decision is None:
                continue
            await conn.execute(
                _CLOSE_SIGNAL, row["id"], now, price, decision.outcome,
                decision.profit, decision.roi_pct, decision.reason,
            )
            closed += 1
        except Exception:
            log.warning("failed to grade investment_signals.id=%s", row["id"], exc_info=True)
    return closed


async def close_signals_for_regime_exit(
    conn, *, strategy_key: str, cohort_key: str, current_price_lookup: PriceLookup,
) -> int:
    """Force-close every active signal for a cohort/strategy whose regime
    has left INVESTMENT_WINDOW/REBOUND (see runner.py) - the state
    machine invalidating the regime is itself a valid, price-independent
    exit reason. A card with no current price available is left open
    rather than closed on a guess; it will be picked up again next pass
    or by grade_open_signals's own max-hold path."""
    rows = await conn.fetch(
        "SELECT id, source_card_id, entry_price FROM investment_signals "
        "WHERE status = 'active' AND strategy_key = $1 AND cohort_key = $2",
        strategy_key, cohort_key,
    )
    now = datetime.now(timezone.utc)
    closed = 0
    for row in rows:
        price = await current_price_lookup(row["source_card_id"])
        if price is None:
            continue
        roi = tm.net_roi(price, row["entry_price"])
        profit = tm.net_profit(price, row["entry_price"])
        await conn.execute(
            _CLOSE_SIGNAL, row["id"], now, price, OUTCOME_REGIME_EXIT,
            profit, (roi * 100) if roi is not None else None, "cohort_regime_exit",
        )
        closed += 1
    return closed


_INSERT_RUN = """
INSERT INTO investment_engine_runs (
    game_year, strategy_key, cohort_key, previous_state, new_state,
    eligible_card_count, candidate_count, signals_created, signals_closed,
    signals_suppressed, suppression_reasons, duration_ms, error
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
"""


async def record_run(conn, **kwargs: Any) -> None:
    try:
        await conn.execute(
            _INSERT_RUN, kwargs["game_year"], kwargs["strategy_key"], kwargs["cohort_key"],
            kwargs.get("previous_state"), kwargs["new_state"],
            kwargs.get("eligible_card_count"), kwargs.get("candidate_count"),
            kwargs.get("signals_created", 0), kwargs.get("signals_closed", 0),
            kwargs.get("signals_suppressed", 0),
            json.dumps(kwargs.get("suppression_reasons") or {}),
            kwargs.get("duration_ms"), kwargs.get("error"),
        )
    except Exception:
        log.warning("failed to record investment_engine_runs row", exc_info=True)


_TRACK_RECORD_SQL = """
SELECT
    strategy_key,
    count(*) AS total,
    count(*) FILTER (WHERE status = 'closed') AS closed,
    count(*) FILTER (WHERE outcome = 'target_hit') AS target_hits,
    count(*) FILTER (WHERE status = 'closed' AND after_tax_roi_pct > 0) AS profitable,
    avg(after_tax_roi_pct) FILTER (WHERE status = 'closed') AS avg_roi_pct,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY after_tax_roi_pct)
        FILTER (WHERE status = 'closed') AS median_roi_pct
FROM investment_signals
WHERE shadow = $1 AND opened_at >= now() - $2::interval
GROUP BY strategy_key
"""


async def track_record(pool, *, shadow: bool, window_days: int = 90, min_sample: int = 20) -> Dict[str, Any]:
    """Aggregate closed signals into a track record, gated on a minimum
    sample size exactly like futgg_recommendation_store.track_record() -
    a handful of closed positions is noise, not a track record."""
    interval = timedelta(days=int(window_days))
    async with pool.acquire() as conn:
        rows = await conn.fetch(_TRACK_RECORD_SQL, shadow, interval)

    out: Dict[str, Any] = {}
    for row in rows:
        total = int(row["total"])
        closed = int(row["closed"])
        enough = closed >= min_sample
        out[row["strategy_key"]] = {
            "total_signals": total,
            "closed_signals": closed,
            "has_enough_data": enough,
            "target_hit_rate_pct": round(100.0 * row["target_hits"] / closed, 1) if enough and closed else None,
            "profitable_rate_pct": round(100.0 * row["profitable"] / closed, 1) if enough and closed else None,
            "avg_roi_pct": round(float(row["avg_roi_pct"]), 2) if enough and row["avg_roi_pct"] is not None else None,
            "median_roi_pct": round(float(row["median_roi_pct"]), 2) if enough and row["median_roi_pct"] is not None else None,
        }
    return out
