# app/services/investment_engine/replay/ledger_store.py
"""
ReplayLedgerStore: an in-memory stand-in for the investment_signals
table's lifecycle, isolated per replay run instance - the "isolated
replay namespace" the product spec requires so a replay can never
pollute the real production Investment Ledger (there is no shared state,
DB connection, or table name; a fresh ReplayLedgerStore() is just a
Python object that disappears when the run ends unless explicitly
exported to CSV/JSON via report.py).

Every FIELD on ReplaySignal mirrors migration 060's investment_signals
columns exactly, so the replay ledger reads like a subset of the real
one - same shape as production the same field names shown in
backend/INVESTMENT_ENGINE.md's "minimum output fields" list.

Idempotency mirrors the real partial unique index
(investment_signals_active_uniq: one active signal per (card_id,
strategy_key)) via a plain dict keyed the same way - open() is a no-op
if a signal is already active for that card+strategy, exactly matching
the production ON CONFLICT ... DO NOTHING behaviour.

Exit-decision math is NEVER reimplemented here: closing a signal always
goes through ledger.evaluate_active_signal_exit(), the same pure helper
grade_open_signals() calls in production - see that module's docstring.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from app.services.investment_engine import ledger as ledger_mod


@dataclass
class ReplaySignal:
    id: int
    game_year: int
    source_card_id: int
    strategy_key: str
    cohort_key: str
    status: str  # 'active' | 'closed'
    shadow: bool
    entry_price: float
    max_buy: float
    target_price: float
    expected_roi_pct: Optional[float]
    expected_hold_hours_min: Optional[float]
    expected_hold_hours_max: Optional[float]
    confidence: Optional[float]
    liquidity: Optional[str]
    investability: Optional[str]
    rationale: List[str]
    risk_flags: List[str]
    opened_on: date
    max_hold_on: date
    market_state_snapshot: dict
    closed_on: Optional[date] = None
    close_price: Optional[float] = None
    outcome: Optional[str] = None
    after_tax_profit: Optional[float] = None
    after_tax_roi_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    # Post-hoc evaluation only (never influences the open/close decision
    # itself - see engine.py's "future prices may evaluate a past
    # decision, never influence it" note): return at N days after entry,
    # computed once enough days have elapsed, purely for the outcomes
    # report.
    forward_returns_pct: Dict[int, float] = field(default_factory=dict)


class ReplayLedgerStore:
    def __init__(self) -> None:
        self._signals: Dict[int, ReplaySignal] = {}
        self._next_id = 1
        self._active_index: Dict[Tuple[int, str], int] = {}  # (card_id, strategy_key) -> signal id

    def open(
        self, *, game_year: int, card_id: int, strategy_key: str, cohort_key: str,
        entry_price: float, max_buy: float, target_price: float,
        expected_roi_pct: Optional[float], expected_hold_hours_min: Optional[float],
        expected_hold_hours_max: Optional[float], confidence: Optional[float],
        liquidity: Optional[str], investability: Optional[str],
        rationale: List[str], risk_flags: List[str], opened_on: date,
        max_hold_hours: float, market_state_snapshot: dict, shadow: bool,
    ) -> Optional[int]:
        key = (card_id, strategy_key)
        if key in self._active_index:
            return None  # idempotent no-op, matches investment_signals_active_uniq
        signal_id = self._next_id
        self._next_id += 1
        max_hold_on = opened_on + timedelta(hours=max_hold_hours)
        self._signals[signal_id] = ReplaySignal(
            id=signal_id, game_year=game_year, source_card_id=card_id, strategy_key=strategy_key,
            cohort_key=cohort_key, status=ledger_mod.STATUS_ACTIVE, shadow=shadow,
            entry_price=entry_price, max_buy=max_buy, target_price=target_price,
            expected_roi_pct=expected_roi_pct, expected_hold_hours_min=expected_hold_hours_min,
            expected_hold_hours_max=expected_hold_hours_max, confidence=confidence,
            liquidity=liquidity, investability=investability,
            rationale=list(rationale), risk_flags=list(risk_flags),
            opened_on=opened_on, max_hold_on=max_hold_on, market_state_snapshot=dict(market_state_snapshot),
        )
        self._active_index[key] = signal_id
        return signal_id

    def active_for_strategy(self, strategy_key: str) -> List[ReplaySignal]:
        return [s for s in self._signals.values() if s.status == ledger_mod.STATUS_ACTIVE and s.strategy_key == strategy_key]

    def active_for_cohort_strategy(self, cohort_key: str, strategy_key: str) -> List[ReplaySignal]:
        return [
            s for s in self._signals.values()
            if s.status == ledger_mod.STATUS_ACTIVE and s.strategy_key == strategy_key and s.cohort_key == cohort_key
        ]

    def close(self, signal_id: int, *, on: date, price: float, decision: "ledger_mod.ExitDecision") -> None:
        s = self._signals[signal_id]
        s.status = ledger_mod.STATUS_CLOSED
        s.closed_on = on
        s.close_price = price
        s.outcome = decision.outcome
        s.after_tax_profit = float(decision.profit)
        s.after_tax_roi_pct = decision.roi_pct
        s.exit_reason = decision.reason
        del self._active_index[(s.source_card_id, s.strategy_key)]

    def all_signals(self) -> List[ReplaySignal]:
        return list(self._signals.values())

    def closed_signals(self) -> List[ReplaySignal]:
        return [s for s in self._signals.values() if s.status == ledger_mod.STATUS_CLOSED]
