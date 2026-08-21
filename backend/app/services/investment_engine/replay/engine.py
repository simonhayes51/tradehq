# app/services/investment_engine/replay/engine.py
"""
The day-by-day FC26 replay orchestrator. Calls the SAME production pure
modules the live runner (runner.py) does - cohorts.classify_cohorts,
drawdown.compute_drawdown/drawdown_percentile/historical_reference_
percentile/severity_score_and_class, momentum.compute_momentum,
state_machine.next_state, ranking.rank_candidates, each strategy's
target_roi_pct/max_hold_hours/eligibility function, and
ledger.evaluate_active_signal_exit for exits. Nothing in this file
re-derives a drawdown, a severity class, a state transition, or a ranking
score - every one of those numbers comes from calling the real module.

What THIS file owns (and only this):
  * the replay clock (iterating calendar days forward)
  * cohort bootstrap (classify newly-available cards each day, freeze
    once classified - mirrors runner.ensure_cohort_membership's ON
    CONFLICT DO NOTHING)
  * per (cohort, strategy) running history bookkeeping (dd_history,
    return_history, state, hours_in_state) - the in-memory equivalent of
    reading back investment_market_states rows in runner.py
  * wiring ranked candidates into ReplayLedgerStore.open()/close()
  * post-hoc forward-return evaluation (STRICTLY separate from the
    decision loop - see run_full_replay()'s final pass)
  * falling-knife / suppression instrumentation

NO LOOKAHEAD: every read from `provider` inside the day loop is bounded
to `as_of=today` (inclusive). The one exception is the clearly-labelled
post-hoc evaluation pass, which runs AFTER the decision loop has fully
finished and only ever grades already-made decisions - it can never
change what was decided.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.services import trading_math as tm
from app.services.investment_engine import cohorts as ch
from app.services.investment_engine import drawdown as dd
from app.services.investment_engine import ledger as ledger_mod
from app.services.investment_engine import momentum as mo
from app.services.investment_engine import ranking as rk
from app.services.investment_engine import state_machine as sm
from app.services.investment_engine.config import InvestmentEngineConfig
from app.services.investment_engine.replay.data_provider import ReplayDataProvider
from app.services.investment_engine.replay.fixtures import WEB_APP_LAUNCH
from app.services.investment_engine.replay.ledger_store import ReplayLedgerStore
from app.services.investment_engine.strategies import STRATEGIES

log = logging.getLogger("investment_engine_replay")

UNAVAILABLE_HISTORICAL_REPLAY = "UNAVAILABLE_HISTORICAL_REPLAY"
MARKET_STATE_HISTORY_LIMIT = 30  # mirrors runner.py's own bound


@dataclass
class CohortStrategyRuntime:
    """Per (cohort_key, strategy_key) running state carried day-to-day -
    the in-memory analogue of reading back the last N
    investment_market_states rows in runner.py."""
    state: str = sm.NORMAL
    days_in_state: int = 0
    dd_history: List[float] = field(default_factory=list)
    return_history: List[float] = field(default_factory=list)
    prev_cohort_index: Optional[float] = None


@dataclass
class DayCohortState:
    day: date
    fr_offset: int
    cohort_key: str
    strategy_key: str
    state: str
    previous_state: str
    transition_reason: str
    cohort_price_index: Optional[float]
    drawdown_pct: Optional[float]
    severity_class: Optional[str]
    momentum_current: Optional[float]
    is_decelerating: bool
    deceleration_count: int
    member_count: int
    candidate_count: int
    signals_opened: int
    signals_closed: int
    avg_confidence: Optional[float]


@dataclass
class SuppressedWindow:
    cohort_key: str
    strategy_key: str
    day: date
    fr_offset: int
    state: str
    cohort_price_index: float


@dataclass
class ReplayResult:
    provider_label: str
    start: date
    end: date
    n_cards_total: int
    n_days: int
    daily_states: List[DayCohortState] = field(default_factory=list)
    cohort_size_by_day: Dict[date, Dict[str, int]] = field(default_factory=dict)
    classification_events: List[Dict[str, Any]] = field(default_factory=list)  # per-card first-classified day
    suppressed_windows: List[SuppressedWindow] = field(default_factory=list)
    ledger: ReplayLedgerStore = field(default_factory=ReplayLedgerStore)
    config_version: str = ""


def _cohort_index_for(provider: ReplayDataProvider, members: List[int], as_of: date) -> Optional[float]:
    prices = [p for p in (provider.price_on(cid, as_of) for cid in members) if p is not None]
    if not prices:
        return None
    prices.sort()
    return prices[len(prices) // 2]


def run_full_replay(
    provider: ReplayDataProvider,
    *,
    config: Optional[InvestmentEngineConfig] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> ReplayResult:
    config = config or InvestmentEngineConfig()
    all_dates = provider.all_dates
    if not all_dates:
        raise ValueError("provider has no price observations")
    start = start or all_dates[0]
    end = end or all_dates[-1]
    calendar = [d for d in all_dates if start <= d <= end]
    if not calendar:
        # Still walk the full inclusive calendar range even if some days
        # have no observations at all (weekends in a real scrape gap,
        # etc.) - a day with zero prices is a legitimate historical
        # state (insufficient coverage), not a reason to skip the date.
        calendar = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    result = ReplayResult(
        provider_label=provider.label, start=start, end=end,
        n_cards_total=len(provider.all_card_ids), n_days=len(calendar),
        config_version=config.version,
    )

    classified: Dict[int, ch.CohortAssignment] = {}
    runtimes: Dict[Tuple[str, str], CohortStrategyRuntime] = {}

    for today in calendar:
        fr_offset = (today - _full_release_estimate(all_dates)).days

        # ---- 1. Cohort bootstrap: classify any newly-available,
        # not-yet-classified card using ONLY prices known up to `today`.
        # Already-classified cards are NEVER reclassified this run -
        # mirrors runner.ensure_cohort_membership's ON CONFLICT DO
        # NOTHING freeze, exercised here exactly as production does it.
        available_ids = provider.cards_first_available_on_or_before(today)
        newly_available = [cid for cid in available_ids if cid not in classified]
        if newly_available:
            # Rank the newly-available cards against the FULL known-so-far
            # universe (already-classified + newly-available) so the
            # percentile is computed against everything the engine could
            # plausibly know about today - but only newly-available cards
            # ever receive an assignment from THIS computation; existing
            # assignments are left untouched (frozen).
            universe_inputs = []
            for cid in available_ids:
                meta = provider.meta(cid)
                first_date = provider.first_observed_date(cid)
                ref_price = provider.price_on(cid, first_date) if first_date else None
                if meta is None or ref_price is None:
                    continue
                universe_inputs.append(ch.CohortInput(
                    card_id=cid, reference_price=ref_price, primary_position=meta.primary_position,
                    reference_captured_at=first_date,
                ))
            assignments = ch.classify_cohorts(universe_inputs, config, min_group_size=config.min_cohort_size)
            by_card = {a.card_id: a for a in assignments}
            for cid in newly_available:
                a = by_card.get(cid)
                if a is None:
                    continue  # position group still below min_cohort_size - insufficient data, not "ordinary"
                classified[cid] = a
                result.classification_events.append({
                    "day": today, "fr_offset": fr_offset, "card_id": cid,
                    "cohort_key": a.cohort_key, "position_group": a.position_group,
                    "price_percentile": round(a.price_percentile, 4),
                })

        cohort_members: Dict[str, List[int]] = {}
        for cid, a in classified.items():
            cohort_members.setdefault(a.cohort_key, []).append(cid)
        result.cohort_size_by_day[today] = {k: len(v) for k, v in cohort_members.items()}

        # ---- 2. Per (cohort, strategy) regime step + candidate ranking
        # + signal open/close, using ONLY today's-and-earlier data.
        for strategy_key, strategy in STRATEGIES.items():
            cohort_key = strategy.COHORT_KEY
            members = cohort_members.get(cohort_key, [])
            rt = runtimes.setdefault((cohort_key, strategy_key), CohortStrategyRuntime())

            cohort_index = _cohort_index_for(provider, members, today)
            if cohort_index is None or len(members) < config.min_cohort_size:
                continue  # insufficient coverage today - matches runner.py's own min_cohort_size gate

            uses_crash_trigger = getattr(strategy, "USES_CRASH_TRIGGER", True)
            if uses_crash_trigger:
                day_state = _step_crash_trigger_strategy(
                    provider, config, strategy, cohort_key, strategy_key, members,
                    today, fr_offset, cohort_index, rt, result,
                )
            else:
                day_state = _step_elapsed_time_strategy(
                    provider, config, strategy, cohort_key, strategy_key, members,
                    today, fr_offset, cohort_index, result,
                )
            result.daily_states.append(day_state)

    # ---- 3. Post-hoc forward-return evaluation (STRICTLY after the
    # decision loop - grades past decisions, never feeds back into them).
    _evaluate_forward_returns(provider, result)

    return result


def _full_release_estimate(all_dates) -> date:
    """FR-offset labelling only (see module docstring: calendar labels,
    never a threshold). If the provider's own dates don't bracket the
    real FC26 Full Release date, fall back to the real WEB_APP_LAUNCH + 9
    days (FC26's actual Web-App-Launch-to-Full-Release gap) so a
    synthetic fixture with a different start date still gets sensible
    relative labels."""
    from app.services.investment_engine.replay.fixtures import FULL_RELEASE
    if all_dates[0] <= FULL_RELEASE <= all_dates[-1]:
        return FULL_RELEASE
    return all_dates[0] + timedelta(days=9)


def _rank_today(
    provider: ReplayDataProvider, config: InvestmentEngineConfig,
    members: List[int], today: date, cohort_return_pct: Optional[float], is_decelerating: bool,
) -> List[rk.RankedCandidate]:
    candidates = []
    for cid in members:
        price = provider.price_on(cid, today)
        if price is None:
            continue
        hist = provider.price_history_up_to(cid, today, inclusive=False)
        prior_price = hist[-1][1] if hist else None
        card_return = (price - prior_price) / prior_price * 100 if prior_price else None
        # Card's own return relative to its cohort's return today - only
        # computable once both are known; None (not 0) when either is
        # missing, so ranking.py's own "no signal" default applies rather
        # than a fabricated "in line with cohort" reading.
        relative_strength = (card_return - cohort_return_pct) if (card_return is not None and cohort_return_pct is not None) else None
        candidates.append(rk.CardCandidate(
            card_id=cid, current_price=price, drawdown_pct=0.0,
            is_decelerating=is_decelerating, relative_strength_pct=relative_strength,
            # FC26's reconstructed daily-chart dataset has no sales-count/
            # tradeability/price-freshness fields at all (see module
            # docstring in live_confirmation.py) - these are honestly
            # None, never fabricated. require_investable=False below is
            # what lets ranking still run on that basis; see
            # backend/INVESTMENT_ENGINE_REPLAY.md for why that's a
            # deliberate, documented replay-mode setting rather than a
            # fork of ranking.py's logic.
            sales_count_recent=None, price_age_minutes=0, is_tradeable=None,
        ))
    return rk.rank_candidates(
        candidates, min_sales_for_investable=config.min_sales_for_investable,
        max_price_age_minutes=config.max_price_age_minutes, require_investable=False,
    )


def _step_crash_trigger_strategy(
    provider, config, strategy, cohort_key, strategy_key, members, today, fr_offset,
    cohort_index, rt: CohortStrategyRuntime, result: ReplayResult,
) -> DayCohortState:
    prev_state = rt.state
    if rt.prev_cohort_index is None:
        # First day this cohort has enough members to evaluate - nothing
        # to compare against yet, matches runner.py's i==0 special case
        # in backtest.walk_cohort_regime.
        rt.prev_cohort_index = cohort_index
        rt.dd_history.append(0.0)
        rt.return_history.append(0.0)
        state_row = DayCohortState(
            day=today, fr_offset=fr_offset, cohort_key=cohort_key, strategy_key=strategy_key,
            state=prev_state, previous_state=prev_state, transition_reason="bootstrap_first_day",
            cohort_price_index=cohort_index, drawdown_pct=0.0, severity_class="normal",
            momentum_current=0.0, is_decelerating=False, deceleration_count=0,
            member_count=len(members), candidate_count=0, signals_opened=0, signals_closed=0,
            avg_confidence=None,
        )
        return state_row

    prior_index_history = _prior_index_history(result, cohort_key, strategy_key, today)
    snap = dd.compute_drawdown(prior_index_history, cohort_index)
    cycle_pctile = dd.drawdown_percentile(rt.dd_history, snap.drawdown_pct)
    ref_pctile = dd.historical_reference_percentile(
        snap.drawdown_pct, config.fc26_reference_drawdown_low_pct, config.fc26_reference_drawdown_high_pct,
    )
    severity_score, severity_class = dd.severity_score_and_class(
        cycle_percentile=cycle_pctile, historical_reference_percentile_value=ref_pctile,
        cycle_observation_count=len(rt.dd_history), config=config,
    )

    current_return = (cohort_index - rt.prev_cohort_index) / rt.prev_cohort_index * 100 if rt.prev_cohort_index else 0.0
    mom = mo.compute_momentum(rt.return_history, current_return, config.deceleration_required_periods)

    new_state, reason = sm.next_state(prev_state, sm.RegimeInputs(
        severity_class=severity_class, is_decelerating=mom.is_decelerating,
        breadth_spread=None, live_confirmation_ok=None,  # FC26: no live BIN/sales data - see module docstring
        overheat_pct=None, hours_in_state=rt.days_in_state * 24.0,
        regime_max_age_hours=getattr(strategy, "regime_max_age_hours", lambda c: 48.0)(config),
    ))
    rt.days_in_state = 0 if new_state != prev_state else rt.days_in_state + 1

    # Falling-knife instrumentation: record every day the cohort sits in
    # CRASHING/OVERHEATED - post-hoc, we'll compute how much further it
    # fell after this point.
    if new_state in (sm.CRASHING, sm.OVERHEATED):
        result.suppressed_windows.append(SuppressedWindow(
            cohort_key=cohort_key, strategy_key=strategy_key, day=today, fr_offset=fr_offset,
            state=new_state, cohort_price_index=cohort_index,
        ))

    rt.dd_history.append(snap.drawdown_pct)
    rt.return_history.append(current_return)
    rt.prev_cohort_index = cohort_index
    rt.state = new_state

    candidate_count = 0
    signals_opened = 0
    signals_closed = 0
    confidences: List[float] = []

    is_eligible_fn = getattr(strategy, "is_state_eligible_for_signals", None)
    if is_eligible_fn and is_eligible_fn(new_state):
        ranked = _rank_today(provider, config, members, today, current_return, mom.is_decelerating)
        candidate_count = len(ranked)
        target_roi = strategy.target_roi_pct(config)
        max_hold = strategy.max_hold_hours(config)
        for rc in ranked[: config.max_signals_per_cohort_per_run]:
            price = provider.price_on(rc.card_id, today)
            if price is None:
                continue
            target_price = float(tm.strategy_target_price(price, target_roi / 100.0))
            signal_id = result.ledger.open(
                game_year=26, card_id=rc.card_id, strategy_key=strategy_key, cohort_key=cohort_key,
                entry_price=price, max_buy=price, target_price=target_price,
                expected_roi_pct=target_roi, expected_hold_hours_min=max_hold / 3, expected_hold_hours_max=max_hold,
                confidence=rc.rank_score, liquidity=rc.liquidity, investability=rc.investability,
                rationale=[reason, f"cohort_state={new_state}", f"liquidity={rc.liquidity}",
                           f"live_confirmation={UNAVAILABLE_HISTORICAL_REPLAY}"],
                risk_flags=[UNAVAILABLE_HISTORICAL_REPLAY],
                opened_on=today, max_hold_hours=max_hold,
                market_state_snapshot={"state": new_state, "severity_class": severity_class,
                                        "drawdown_pct": snap.drawdown_pct, "fr_offset": fr_offset},
                shadow=True,
            )
            if signal_id is not None:
                signals_opened += 1
                confidences.append(rc.rank_score)

    # Exit checks for currently-active signals under this strategy.
    for s in result.ledger.active_for_strategy(strategy_key):
        price = provider.price_on(s.source_card_id, today)
        decision = ledger_mod.evaluate_active_signal_exit(
            current_price=price, entry_price=s.entry_price, target_price=s.target_price,
            now=today, max_hold_at=s.max_hold_on,
        )
        if decision is not None:
            result.ledger.close(s.id, on=today, price=price, decision=decision)
            signals_closed += 1

    # Regime-exit force-close: cohort left an investable state.
    if new_state == sm.EXIT and prev_state in sm.INVESTABLE_STATES:
        for s in result.ledger.active_for_cohort_strategy(cohort_key, strategy_key):
            price = provider.price_on(s.source_card_id, today)
            if price is None:
                continue
            roi = tm.net_roi(price, s.entry_price)
            profit = tm.net_profit(price, s.entry_price)
            decision = ledger_mod.ExitDecision(
                outcome=ledger_mod.OUTCOME_REGIME_EXIT, reason="cohort_regime_exit",
                profit=profit, roi_pct=(float(roi) * 100) if roi is not None else None,
            )
            result.ledger.close(s.id, on=today, price=price, decision=decision)
            signals_closed += 1

    return DayCohortState(
        day=today, fr_offset=fr_offset, cohort_key=cohort_key, strategy_key=strategy_key,
        state=new_state, previous_state=prev_state, transition_reason=reason,
        cohort_price_index=cohort_index, drawdown_pct=snap.drawdown_pct, severity_class=severity_class,
        momentum_current=mom.current_return_pct, is_decelerating=mom.is_decelerating,
        deceleration_count=mom.deceleration_count, member_count=len(members),
        candidate_count=candidate_count, signals_opened=signals_opened, signals_closed=signals_closed,
        avg_confidence=(sum(confidences) / len(confidences)) if confidences else None,
    )


def _prior_index_history(result: ReplayResult, cohort_key: str, strategy_key: str, today: date) -> List[float]:
    return [
        d.cohort_price_index for d in result.daily_states
        if d.cohort_key == cohort_key and d.strategy_key == strategy_key
        and d.day < today and d.cohort_price_index is not None
    ][-MARKET_STATE_HISTORY_LIMIT:]


def _step_elapsed_time_strategy(
    provider, config, strategy, cohort_key, strategy_key, members, today, fr_offset,
    cohort_index, result: ReplayResult,
) -> DayCohortState:
    """LAUNCH_STARTER_ACCUMULATION and any future USES_CRASH_TRIGGER=False
    strategy: deliberately never touches state_machine.py - eligibility
    is purely elapsed real time since launch, exactly matching
    runner.run_launch_window_pass's production behaviour."""
    hours_since_launch = (today - WEB_APP_LAUNCH).total_seconds() / 3600.0
    eligible = strategy.is_launch_window_eligible(hours_since_launch, config)
    new_state = "LAUNCH_WINDOW_OPEN" if eligible else "LAUNCH_WINDOW_CLOSED"
    reason = "within_launch_window" if eligible else "past_launch_window"

    candidate_count = 0
    signals_opened = 0
    signals_closed = 0
    confidences: List[float] = []

    if eligible:
        prior_history = _prior_index_history(result, cohort_key, strategy_key, today)
        cohort_return = (cohort_index - prior_history[-1]) / prior_history[-1] * 100 if prior_history else None
        ranked = _rank_today(provider, config, members, today, cohort_return, is_decelerating=False)
        candidate_count = len(ranked)
        target_roi = strategy.target_roi_pct(config)
        max_hold = strategy.max_hold_hours(config)
        for rc in ranked[: config.max_signals_per_cohort_per_run]:
            price = provider.price_on(rc.card_id, today)
            if price is None:
                continue
            target_price = float(tm.strategy_target_price(price, target_roi / 100.0))
            signal_id = result.ledger.open(
                game_year=26, card_id=rc.card_id, strategy_key=strategy_key, cohort_key=cohort_key,
                entry_price=price, max_buy=price, target_price=target_price,
                expected_roi_pct=target_roi, expected_hold_hours_min=max_hold / 3, expected_hold_hours_max=max_hold,
                confidence=rc.rank_score, liquidity=rc.liquidity, investability=rc.investability,
                rationale=[reason, f"hours_since_launch={hours_since_launch:.0f}", f"liquidity={rc.liquidity}",
                           f"live_confirmation={UNAVAILABLE_HISTORICAL_REPLAY}"],
                risk_flags=[UNAVAILABLE_HISTORICAL_REPLAY],
                opened_on=today, max_hold_hours=max_hold,
                market_state_snapshot={"state": new_state, "fr_offset": fr_offset},
                shadow=True,
            )
            if signal_id is not None:
                signals_opened += 1
                confidences.append(rc.rank_score)

    for s in result.ledger.active_for_strategy(strategy_key):
        price = provider.price_on(s.source_card_id, today)
        decision = ledger_mod.evaluate_active_signal_exit(
            current_price=price, entry_price=s.entry_price, target_price=s.target_price,
            now=today, max_hold_at=s.max_hold_on,
        )
        if decision is not None:
            result.ledger.close(s.id, on=today, price=price, decision=decision)
            signals_closed += 1

    return DayCohortState(
        day=today, fr_offset=fr_offset, cohort_key=cohort_key, strategy_key=strategy_key,
        state=new_state, previous_state=new_state, transition_reason=reason,
        cohort_price_index=cohort_index, drawdown_pct=None, severity_class=None,
        momentum_current=None, is_decelerating=False, deceleration_count=0,
        member_count=len(members), candidate_count=candidate_count,
        signals_opened=signals_opened, signals_closed=signals_closed,
        avg_confidence=(sum(confidences) / len(confidences)) if confidences else None,
    )


def _evaluate_forward_returns(provider: ReplayDataProvider, result: ReplayResult) -> None:
    """POST-HOC ONLY: computes N-day-forward returns for every signal
    after the entire decision loop has finished. This function is never
    called from inside the day loop and never feeds any value back into
    a state/ranking/open/close decision - it only annotates already-made
    decisions for the outcomes report, matching the explicit "future
    prices may evaluate a past decision, they may not influence it"
    requirement."""
    horizons = (1, 2, 3, 5, 7)
    for s in result.ledger.all_signals():
        for h in horizons:
            future_price = provider.price_on(s.source_card_id, s.opened_on + timedelta(days=h))
            if future_price is None:
                continue
            roi = tm.net_roi(future_price, s.entry_price)
            if roi is not None:
                s.forward_returns_pct[h] = float(roi) * 100.0
