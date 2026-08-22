# app/services/investment_engine/backtest.py
"""
Runs the SAME pure engine logic (cohorts.py, drawdown.py, momentum.py,
state_machine.py) against FC26's reconstructed fc26_historical_daily_
prices (see research/fc26_launch_market), to validate that the
production Investment Engine still reproduces the FC26 research findings
it's built on - not a new analysis, a regression check on the engine
code itself.

Deliberately does NOT use live_confirmation.py: FC26's dataset is
reconstructed daily chart prices only - no BIN depth, no individual
sales, no intraday resolution - so live_confirmation_ok is always passed
as None ("unavailable"), exactly as state_machine.py already treats it
in production before enough FC27 live data exists (see
state_machine.py's BOTTOMING -> INVESTMENT_WINDOW transition: None never
blocks the historical trigger). This is the one place the module
docstring's "separate historical-rule evaluation from FC27-only live
confirmation" requirement is enforced structurally, not just by
convention.

Run via the same Coolify one-off pattern as the research_fc26_*.py
scripts (see research/fc26_launch_market/tools/README.md):

    python -m app.services.investment_engine.backtest

against a database that has fc26_historical_daily_prices/futgg_players
populated (the Phase 5+ research dataset). Also covered by
backend/tests/test_investment_backtest.py, which runs the same pure
walk-forward logic against small synthetic fixtures with no DB at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

from app.services.investment_engine import cohorts as ch
from app.services.investment_engine import drawdown as dd
from app.services.investment_engine import momentum as mo
from app.services.investment_engine import state_machine as sm
from app.services.investment_engine.config import InvestmentEngineConfig

log = logging.getLogger("investment_backtest")


@dataclass(frozen=True)
class BacktestDay:
    day: date
    state: str
    severity_class: str
    drawdown_pct: float
    is_decelerating: bool


def walk_cohort_regime(
    price_series: Dict[date, float], *, config: InvestmentEngineConfig,
    regime_max_age_hours: float,
) -> List[BacktestDay]:
    """Pure walk-forward over one cohort's daily price series. Each day
    uses ONLY prices strictly before it (NO LOOKAHEAD) - the same
    invariant research/fc26_launch_market's phase8/8b/8c scripts
    enforced, now exercised against the production engine code itself
    rather than research-only duplicates of this logic.
    """
    days = sorted(price_series)
    out: List[BacktestDay] = []
    state = sm.NORMAL
    hours_in_state = 0.0
    dd_history: List[float] = []
    return_history: List[float] = []

    for i, day in enumerate(days):
        if i == 0:
            out.append(BacktestDay(day, state, "normal", 0.0, False))
            continue

        prior_days = days[:i]
        prior_prices = [price_series[d] for d in prior_days]
        current_price = price_series[day]

        snap = dd.compute_drawdown(prior_prices, current_price)
        cycle_pctile = dd.drawdown_percentile(dd_history, snap.drawdown_pct)
        ref_pctile = dd.historical_reference_percentile(
            snap.drawdown_pct, config.fc26_reference_drawdown_low_pct, config.fc26_reference_drawdown_high_pct,
        )
        severity_score, severity_class = dd.severity_score_and_class(
            cycle_percentile=cycle_pctile, historical_reference_percentile_value=ref_pctile,
            cycle_observation_count=len(dd_history), config=config,
        )

        prev_price = price_series[prior_days[-1]]
        current_return = (current_price - prev_price) / prev_price * 100 if prev_price else 0.0
        mom = mo.compute_momentum(return_history, current_return, config.deceleration_required_periods)

        new_state, _reason = sm.next_state(state, sm.RegimeInputs(
            severity_class=severity_class,
            is_decelerating=mom.is_decelerating,
            breadth_spread=None,
            live_confirmation_ok=None,  # FC26 has no live BIN/sales data - see module docstring
            overheat_pct=None,
            hours_in_state=hours_in_state,
            regime_max_age_hours=regime_max_age_hours,
        ))
        hours_in_state = 0.0 if new_state != state else hours_in_state + 24.0
        state = new_state

        dd_history.append(snap.drawdown_pct)
        return_history.append(current_return)
        out.append(BacktestDay(day, state, severity_class, snap.drawdown_pct, mom.is_decelerating))

    return out


@dataclass(frozen=True)
class BacktestReport:
    cohort_key: str
    first_investment_window_day: Optional[date]
    days_in_crashing_before_window: int
    max_hold_hours_used: float
    regime_opened: bool


def summarize(days: Sequence[BacktestDay], cohort_key: str, regime_max_age_hours: float) -> BacktestReport:
    first_window = next((d.day for d in days if d.state in sm.INVESTABLE_STATES), None)
    crashing_days = sum(1 for d in days if d.state == sm.CRASHING)
    return BacktestReport(
        cohort_key=cohort_key, first_investment_window_day=first_window,
        days_in_crashing_before_window=crashing_days,
        max_hold_hours_used=regime_max_age_hours, regime_opened=first_window is not None,
    )


async def run_backtest(pool, *, config: InvestmentEngineConfig) -> Dict[str, BacktestReport]:
    """DB-backed entry point: loads fc26_historical_daily_prices +
    futgg_players, classifies cohorts with the SAME classify_cohorts()
    the live runner uses, and walks each cohort's median daily price
    through walk_cohort_regime(). Requires the FC26 research dataset
    (see research/fc26_launch_market) to already be populated - this is
    a validation harness, not a data pipeline."""
    async with pool.acquire() as conn:
        price_rows = await conn.fetch(
            "SELECT card_id, observed_date, price FROM fc26_historical_daily_prices ORDER BY card_id, observed_date"
        )
        meta_rows = await conn.fetch(
            "SELECT source_card_id, primary_position, price_tier FROM futgg_players "
            "WHERE price_tier IN ('gold_common', 'gold_rare')"
        )

    prices: Dict[int, Dict[date, float]] = {}
    for r in price_rows:
        prices.setdefault(r["card_id"], {})[r["observed_date"]] = float(r["price"])
    meta = {r["source_card_id"]: r for r in meta_rows if r["source_card_id"] in prices}

    cards = [
        ch.CohortInput(
            card_id=cid, reference_price=min(series.items(), key=lambda kv: kv[0])[1],
            primary_position=meta[cid]["primary_position"],
        )
        for cid, series in prices.items() if cid in meta
    ]
    assignments = {a.card_id: a.cohort_key for a in ch.classify_cohorts(cards, config, min_group_size=10)}

    by_cohort: Dict[str, List[Dict[date, float]]] = {}
    for cid, series in prices.items():
        cohort_key = assignments.get(cid)
        if cohort_key:
            by_cohort.setdefault(cohort_key, []).append(series)

    reports: Dict[str, BacktestReport] = {}
    for cohort_key, all_series in by_cohort.items():
        all_days = sorted({d for series in all_series for d in series})
        median_series: Dict[date, float] = {}
        for day in all_days:
            vals = sorted(series[day] for series in all_series if day in series)
            if vals:
                median_series[day] = vals[len(vals) // 2]
        days = walk_cohort_regime(median_series, config=config, regime_max_age_hours=48.0)
        report = summarize(days, cohort_key, 48.0)
        reports[cohort_key] = report
        log.info("backtest cohort=%-20s regime_opened=%s first_window_day=%s crashing_days=%d",
                  cohort_key, report.regime_opened, report.first_investment_window_day, report.days_in_crashing_before_window)

    return reports


if __name__ == "__main__":
    import asyncio
    import os
    import asyncpg  # local import: only needed for the CLI entry point

    logging.basicConfig(level=logging.INFO)

    async def _main() -> None:
        dsn = os.environ["PLAYER_DATABASE_URL"]
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
        try:
            await run_backtest(pool, config=InvestmentEngineConfig())
        finally:
            await pool.close()

    asyncio.run(_main())
