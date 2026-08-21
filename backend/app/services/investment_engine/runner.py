# app/services/investment_engine/runner.py
"""
Orchestrates one full Investment Engine pass: DB I/O wrapping the pure
modules in this package. This is the only module in the package that
talks to Postgres directly (besides ledger.py, which it calls).

Performance discipline (see backend/INVESTMENT_ENGINE.md's performance
section): every query here is either
  (a) scoped to a bounded cohort's card_id list against the already-
      indexed futgg_market_snapshot materialized view (never a raw scan
      of futgg_bin_history/futgg_sales_history across the whole card
      catalogue), or
  (b) a LIMIT N read of this engine's OWN investment_market_states time
      series (migration 059) for one (game_year, strategy, cohort),
      which is what drawdown/momentum history comes from instead of
      re-deriving it from raw price history every pass.

FC26-prior weighting: see _fc26_prior_weight() - implements the "FC26
prior vs FC27 reality" requirement as an elapsed-time decay, mirroring
priority_config.py's demand_decay_bands_hours pattern.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services import trading_math as tm
from app.services.investment_engine import cohorts as ch
from app.services.investment_engine import drawdown as dd
from app.services.investment_engine import ledger
from app.services.investment_engine import live_confirmation as lc
from app.services.investment_engine import momentum as mo
from app.services.investment_engine import ranking as rk
from app.services.investment_engine import state_machine as sm
from app.services.investment_engine.config import INVESTMENT_ENGINE_CONFIG, INVESTMENT_ENGINE_VERSION
from app.services.investment_engine.strategies import STRATEGIES

log = logging.getLogger("investment_engine")

MARKET_STATE_HISTORY_LIMIT = 30  # rows of investment_market_states read back for drawdown/momentum history


def _fc26_prior_weight(hours_since_launch: Optional[float], config) -> float:
    """Elapsed-time decay of how much the FC26 research prior should
    influence a decision - see config.py's fc26_prior_decay_bands_hours.
    Very early launch: weight ~1.0 (FC27 has almost no history of its
    own). Week 2+: weight falls toward fc26_prior_minimal_weight - an
    "established market" no longer resembles a launch."""
    if not config.fc26_prior_enabled or hours_since_launch is None:
        return 0.0
    for band_hours, weight in sorted(config.fc26_prior_decay_bands_hours.items()):
        if hours_since_launch <= band_hours:
            return weight
    return config.fc26_prior_minimal_weight


async def ensure_cohort_membership(conn, *, game_year: int, cards: List[ch.CohortInput], config) -> int:
    """Classify any card not yet assigned a cohort under the current
    classifier_version. Idempotent: ON CONFLICT DO NOTHING off migration
    058's unique index means a card already classified under this
    version is left untouched - a card's reference_price/cohort, once
    set, never silently changes underneath an open or past signal."""
    assignments = ch.classify_cohorts(cards, config, min_group_size=config.min_cohort_size)
    if not assignments:
        return 0
    rows = [
        (game_year, a.card_id, a.cohort_key, a.position_group, a.price_percentile,
         a.reference_price, a.reference_captured_at, ch.CLASSIFIER_VERSION)
        for a in assignments
    ]
    result = await conn.executemany(
        """
        INSERT INTO investment_cohort_memberships (
            game_year, source_card_id, cohort_key, position_group,
            price_percentile, reference_price, reference_captured_at, classifier_version
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (game_year, source_card_id, classifier_version) DO NOTHING
        """,
        rows,
    )
    return len(rows)


async def _cohort_card_ids(conn, *, game_year: int, cohort_key: str) -> List[int]:
    rows = await conn.fetch(
        "SELECT source_card_id FROM investment_cohort_memberships "
        "WHERE game_year = $1 AND cohort_key = $2 AND classifier_version = $3",
        game_year, cohort_key, ch.CLASSIFIER_VERSION,
    )
    return [r["source_card_id"] for r in rows]


async def _current_cohort_prices(conn, card_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """One indexed query against futgg_market_snapshot, scoped to this
    cohort's (bounded) card_id list - never a full-table scan."""
    if not card_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT source_card_id, current_bin, sales_count, is_tradeable,
               price_updated_at, latest_sale_price, sales_median
        FROM futgg_market_snapshot
        WHERE source_card_id = ANY($1::bigint[])
        """,
        card_ids,
    )
    return {r["source_card_id"]: dict(r) for r in rows}


async def _market_state_history(conn, *, game_year: int, strategy_key: str, cohort_key: str) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT drawdown_pct, momentum_current, state, computed_at
        FROM investment_market_states
        WHERE game_year = $1 AND strategy_key = $2 AND cohort_key = $3
        ORDER BY computed_at DESC
        LIMIT $4
        """,
        game_year, strategy_key, cohort_key, MARKET_STATE_HISTORY_LIMIT,
    )
    return [dict(r) for r in reversed(rows)]  # chronological, oldest first


_INSERT_MARKET_STATE = """
INSERT INTO investment_market_states (
    game_year, strategy_key, cohort_key, state, previous_state, transition_reason,
    cohort_price_index, running_peak, reference_price, drawdown_pct,
    drawdown_percentile_current_cycle, historical_reference_percentile,
    severity_score, severity_class, momentum_current, momentum_previous,
    deceleration_count, deceleration_strength, pct_rising, pct_falling,
    breadth_spread, live_confirmation, fc26_prior, fc26_prior_weight,
    confidence, eligible_card_count, candidate_count, engine_version
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28)
RETURNING id
"""


async def run_cohort_pass(
    conn, *, game_year: int, strategy, config=INVESTMENT_ENGINE_CONFIG,
    hours_since_launch: Optional[float] = None,
) -> Dict[str, Any]:
    """One recompute pass for one strategy's cohort. Returns a summary
    dict (also written to investment_engine_runs by the caller)."""
    started = time.monotonic()
    cohort_key = strategy.COHORT_KEY
    strategy_key = strategy.KEY
    suppression_reasons: Dict[str, int] = {}

    card_ids = await _cohort_card_ids(conn, game_year=game_year, cohort_key=cohort_key)
    snapshots = await _current_cohort_prices(conn, card_ids)
    priced = {cid: float(s["current_bin"]) for cid, s in snapshots.items() if s.get("current_bin")}

    if len(priced) < config.min_cohort_size:
        log.info("investment_engine strategy=%s cohort=%s: only %d priced cards (min %d) - skipping pass",
                  strategy_key, cohort_key, len(priced), config.min_cohort_size)
        return {"new_state": sm.NORMAL, "eligible_card_count": len(priced), "candidate_count": 0,
                "signals_created": 0, "signals_closed": 0, "signals_suppressed": 0,
                "suppression_reasons": {"insufficient_cohort_coverage": 1}, "duration_ms": None}

    cohort_index = sorted(priced.values())[len(priced) // 2]  # median

    history = await _market_state_history(conn, game_year=game_year, strategy_key=strategy_key, cohort_key=cohort_key)
    dd_history_values = [float(h["drawdown_pct"]) for h in history if h.get("drawdown_pct") is not None]
    momentum_history = [float(h["momentum_current"]) for h in history if h.get("momentum_current") is not None]
    prev_state = history[-1]["state"] if history else sm.NORMAL
    prev_computed_at = history[-1]["computed_at"] if history else None

    reference_prices = [float(v) for v in dd_history_values]  # proxy peak history from drawdown series
    running_peak = max([*[float(h.get("cohort_price_index") or 0) for h in history if h.get("cohort_price_index")], cohort_index]) \
        if history else cohort_index
    snap = dd.compute_drawdown(
        [float(h["cohort_price_index"]) for h in history if h.get("cohort_price_index")], cohort_index,
    )
    cycle_pctile = dd.drawdown_percentile(dd_history_values, snap.drawdown_pct)
    ref_pctile = dd.historical_reference_percentile(
        snap.drawdown_pct, config.fc26_reference_drawdown_low_pct, config.fc26_reference_drawdown_high_pct,
    )
    severity_score, severity_class = dd.severity_score_and_class(
        cycle_percentile=cycle_pctile, historical_reference_percentile_value=ref_pctile,
        cycle_observation_count=len(dd_history_values), config=config,
    )

    prev_index = float(history[-1]["cohort_price_index"]) if history and history[-1].get("cohort_price_index") else cohort_index
    current_return = (cohort_index - prev_index) / prev_index * 100 if prev_index else 0.0
    mom = mo.compute_momentum(momentum_history, current_return, config.deceleration_required_periods)

    hours_in_state = 0.0
    if prev_computed_at is not None:
        hours_in_state = (datetime.now(timezone.utc) - prev_computed_at).total_seconds() / 3600.0

    # ---- FC27 live confirmation (only when the regime is plausibly near
    # opening - cheap short-circuit, since it needs per-card queries) ---
    live_conf: Optional[lc.LiveConfirmation] = None
    if severity_class in ("severe", "extreme") and card_ids:
        sample_ids = card_ids[:50]  # bounded sample, not the whole cohort
        bin_rows = await conn.fetch(
            "SELECT source_card_id, lowest_bin, captured_at FROM futgg_bin_history "
            "WHERE source_card_id = ANY($1::bigint[]) ORDER BY captured_at DESC LIMIT 500",
            sample_ids,
        )
        sales_rows = await conn.fetch(
            "SELECT source_card_id, sold_price, approximate_sold_at FROM futgg_sales_history "
            "WHERE source_card_id = ANY($1::bigint[]) AND approximate_sold_at >= now() - interval '48 hours' "
            "ORDER BY approximate_sold_at DESC LIMIT 500",
            sample_ids,
        )
        bin_series = sorted([(float(r["lowest_bin"]), r["captured_at"]) for r in bin_rows], key=lambda p: p[1])
        sales_series = sorted([(float(r["sold_price"]), r["approximate_sold_at"]) for r in sales_rows], key=lambda p: p[1])
        live_conf = lc.evaluate_live_confirmation(
            bin_history_recent=bin_series, sales_recent=sales_series,
            min_sales_count=config.min_sales_count_recent,
        )

    pct_rising = pct_falling = breadth_spread = None
    prior_priced = {cid: float(s["current_bin"]) for cid, s in snapshots.items()
                     if s.get("current_bin") and s.get("sales_median")}
    if prior_priced:
        rising = sum(1 for cid, p in priced.items() if snapshots[cid].get("sales_median") and p > float(snapshots[cid]["sales_median"]))
        falling = sum(1 for cid, p in priced.items() if snapshots[cid].get("sales_median") and p < float(snapshots[cid]["sales_median"]))
        total = rising + falling
        if total:
            pct_rising = round(rising / total * 100, 1)
            pct_falling = round(falling / total * 100, 1)
            breadth_spread = round(pct_rising - pct_falling, 1)

    fc26_weight = _fc26_prior_weight(hours_since_launch, config)

    overheat_pct = None  # requires a distinct "post-entry low" reference; left unset (unknown) until
                          # enough of the cycle's own low has been observed - see OVERHEATED gating note below.

    new_state, reason = sm.next_state(prev_state, sm.RegimeInputs(
        severity_class=severity_class,
        is_decelerating=mom.is_decelerating,
        breadth_spread=breadth_spread,
        live_confirmation_ok=(live_conf.confirmed if live_conf else None),
        overheat_pct=overheat_pct,
        hours_in_state=hours_in_state,
        regime_max_age_hours=getattr(strategy, "regime_max_age_hours", lambda c: 48.0)(config),
    ))

    market_state_id = await conn.fetchval(
        _INSERT_MARKET_STATE,
        game_year, strategy_key, cohort_key, new_state, prev_state, reason,
        cohort_index, snap.running_peak, reference_prices[0] if reference_prices else cohort_index,
        snap.drawdown_pct, cycle_pctile, ref_pctile, severity_score, severity_class,
        mom.current_return_pct, mom.previous_return_pct, mom.deceleration_count, mom.deceleration_strength,
        pct_rising, pct_falling, breadth_spread,
        json.dumps(live_conf.__dict__ if live_conf else {}),
        json.dumps({"low": config.fc26_reference_drawdown_low_pct,
                     "high": config.fc26_reference_drawdown_high_pct}),
        fc26_weight, None, len(priced), 0, INVESTMENT_ENGINE_VERSION,
    )

    signals_created = 0
    candidate_count = 0
    is_eligible_fn = getattr(strategy, "is_state_eligible_for_signals", None)
    if is_eligible_fn and is_eligible_fn(new_state):
        candidates = []
        for cid, price in priced.items():
            snap_row = snapshots.get(cid, {})
            sales_count = snap_row.get("sales_count")
            price_age_min = None
            if snap_row.get("price_updated_at"):
                price_age_min = (datetime.now(timezone.utc) - snap_row["price_updated_at"]).total_seconds() / 60.0
            candidates.append(rk.CardCandidate(
                card_id=cid, current_price=price, drawdown_pct=snap.drawdown_pct,
                is_decelerating=mom.is_decelerating, relative_strength_pct=None,
                sales_count_recent=sales_count, price_age_minutes=price_age_min,
                is_tradeable=snap_row.get("is_tradeable"),
            ))
        ranked = rk.rank_candidates(
            candidates, min_sales_for_investable=config.min_sales_for_investable,
            max_price_age_minutes=config.max_price_age_minutes,
        )
        candidate_count = len(ranked)
        target_roi = strategy.target_roi_pct(config) if hasattr(strategy, "target_roi_pct") else 5.0
        max_hold = strategy.max_hold_hours(config) if hasattr(strategy, "max_hold_hours") else 72.0

        for rc in ranked[: config.max_signals_per_cohort_per_run]:
            price = priced[rc.card_id]
            # trading_math.strategy_target_price() is the repo's single
            # source of truth for tax/ROI math (EA_TAX=5%) and EA
            # listing-increment rounding - never reimplemented here.
            target_price = float(tm.strategy_target_price(price, target_roi / 100.0))
            signal_id = await ledger.open_signal(
                conn, game_year=game_year, card_id=rc.card_id, strategy_key=strategy_key,
                cohort_key=cohort_key, entry_price=price, max_buy=price, target_price=target_price,
                expected_roi_pct=target_roi, expected_hold_hours_min=max_hold / 3, expected_hold_hours_max=max_hold,
                confidence=rc.rank_score, liquidity=rc.liquidity, investability=rc.investability,
                max_position_cards=None,
                rationale=[reason, f"cohort_state={new_state}", f"liquidity={rc.liquidity}"],
                risk_flags=[] if fc26_weight < 0.5 else ["fc26_prior_still_heavily_weighted"],
                market_state_id=market_state_id, max_hold_hours=max_hold,
                engine_version=INVESTMENT_ENGINE_VERSION,
                shadow=not config.publish_signals,
            )
            if signal_id is not None:
                signals_created += 1
            else:
                suppression_reasons["duplicate_or_write_failure"] = suppression_reasons.get("duplicate_or_write_failure", 0) + 1

    signals_closed = 0
    if new_state == sm.EXIT and prev_state in sm.INVESTABLE_STATES:
        async def _lookup(cid: int) -> Optional[float]:
            row = await conn.fetchrow("SELECT current_bin FROM futgg_market_snapshot WHERE source_card_id = $1", cid)
            return float(row["current_bin"]) if row and row["current_bin"] else None
        signals_closed = await ledger.close_signals_for_regime_exit(
            conn, strategy_key=strategy_key, cohort_key=cohort_key, current_price_lookup=_lookup,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "investment_engine strategy=%s cohort=%s %s -> %s (%s) drawdown=%.1f%% severity=%s decel=%s "
        "eligible=%d candidates=%d signals_created=%d signals_closed=%d duration_ms=%d",
        strategy_key, cohort_key, prev_state, new_state, reason, snap.drawdown_pct, severity_class,
        mom.is_decelerating, len(priced), candidate_count, signals_created, signals_closed, duration_ms,
    )

    return {
        "previous_state": prev_state, "new_state": new_state,
        "eligible_card_count": len(priced), "candidate_count": candidate_count,
        "signals_created": signals_created, "signals_closed": signals_closed,
        "signals_suppressed": sum(suppression_reasons.values()), "suppression_reasons": suppression_reasons,
        "duration_ms": duration_ms,
    }


LAUNCH_WINDOW_OPEN = "LAUNCH_WINDOW_OPEN"
LAUNCH_WINDOW_CLOSED = "LAUNCH_WINDOW_CLOSED"

_INSERT_MARKET_STATE_MINIMAL = """
INSERT INTO investment_market_states (
    game_year, strategy_key, cohort_key, state, previous_state, transition_reason,
    cohort_price_index, fc26_prior_weight, confidence, eligible_card_count,
    candidate_count, engine_version
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
RETURNING id
"""


async def run_launch_window_pass(
    conn, *, game_year: int, strategy, config=INVESTMENT_ENGINE_CONFIG,
    hours_since_launch: Optional[float] = None,
) -> Dict[str, Any]:
    """One recompute pass for a strategy with USES_CRASH_TRIGGER = False
    (currently only LAUNCH_STARTER_ACCUMULATION). Deliberately does NOT
    touch drawdown.py, momentum.py, or state_machine.py - this strategy's
    own module docstring and test_no_reference_to_state_machine_
    investable_states assert it never uses the premium crash trigger or
    cohort regime state. Eligibility here is purely elapsed-launch-time
    (is_launch_window_eligible) plus the same liquidity/tradeability risk
    controls every strategy shares via ranking.py."""
    started = time.monotonic()
    cohort_key = strategy.COHORT_KEY
    strategy_key = strategy.KEY
    suppression_reasons: Dict[str, int] = {}

    card_ids = await _cohort_card_ids(conn, game_year=game_year, cohort_key=cohort_key)
    snapshots = await _current_cohort_prices(conn, card_ids)
    priced = {cid: float(s["current_bin"]) for cid, s in snapshots.items() if s.get("current_bin")}

    if len(priced) < config.min_cohort_size:
        log.info("investment_engine strategy=%s cohort=%s: only %d priced cards (min %d) - skipping pass",
                  strategy_key, cohort_key, len(priced), config.min_cohort_size)
        return {"new_state": LAUNCH_WINDOW_CLOSED, "eligible_card_count": len(priced), "candidate_count": 0,
                "signals_created": 0, "signals_closed": 0, "signals_suppressed": 0,
                "suppression_reasons": {"insufficient_cohort_coverage": 1}, "duration_ms": None}

    cohort_index = sorted(priced.values())[len(priced) // 2]  # median, for observability only
    fc26_weight = _fc26_prior_weight(hours_since_launch, config)

    eligible = (
        hours_since_launch is not None
        and strategy.is_launch_window_eligible(hours_since_launch, config)
    )
    new_state = LAUNCH_WINDOW_OPEN if eligible else LAUNCH_WINDOW_CLOSED
    reason = "within_launch_window" if eligible else (
        "hours_since_launch_unknown" if hours_since_launch is None else "past_launch_window"
    )

    signals_created = 0
    candidate_count = 0
    if eligible:
        candidates = []
        for cid, price in priced.items():
            snap_row = snapshots.get(cid, {})
            sales_count = snap_row.get("sales_count")
            price_age_min = None
            if snap_row.get("price_updated_at"):
                price_age_min = (datetime.now(timezone.utc) - snap_row["price_updated_at"]).total_seconds() / 60.0
            candidates.append(rk.CardCandidate(
                card_id=cid, current_price=price, drawdown_pct=None,
                is_decelerating=False, relative_strength_pct=None,
                sales_count_recent=sales_count, price_age_minutes=price_age_min,
                is_tradeable=snap_row.get("is_tradeable"),
            ))
        ranked = rk.rank_candidates(
            candidates, min_sales_for_investable=config.min_sales_for_investable,
            max_price_age_minutes=config.max_price_age_minutes,
        )
        candidate_count = len(ranked)
        target_roi = strategy.target_roi_pct(config)
        max_hold = strategy.max_hold_hours(config)

        market_state_id = await conn.fetchval(
            _INSERT_MARKET_STATE_MINIMAL,
            game_year, strategy_key, cohort_key, new_state, None, reason,
            cohort_index, fc26_weight, None, len(priced), candidate_count, INVESTMENT_ENGINE_VERSION,
        )

        for rc in ranked[: config.max_signals_per_cohort_per_run]:
            price = priced[rc.card_id]
            target_price = float(tm.strategy_target_price(price, target_roi / 100.0))
            signal_id = await ledger.open_signal(
                conn, game_year=game_year, card_id=rc.card_id, strategy_key=strategy_key,
                cohort_key=cohort_key, entry_price=price, max_buy=price, target_price=target_price,
                expected_roi_pct=target_roi, expected_hold_hours_min=max_hold / 3, expected_hold_hours_max=max_hold,
                confidence=rc.rank_score, liquidity=rc.liquidity, investability=rc.investability,
                max_position_cards=None,
                rationale=[reason, f"hours_since_launch={hours_since_launch}", f"liquidity={rc.liquidity}"],
                risk_flags=[] if fc26_weight < 0.5 else ["fc26_prior_still_heavily_weighted"],
                market_state_id=market_state_id, max_hold_hours=max_hold,
                engine_version=INVESTMENT_ENGINE_VERSION,
                shadow=not config.publish_signals,
            )
            if signal_id is not None:
                signals_created += 1
            else:
                suppression_reasons["duplicate_or_write_failure"] = suppression_reasons.get("duplicate_or_write_failure", 0) + 1
    else:
        await conn.fetchval(
            _INSERT_MARKET_STATE_MINIMAL,
            game_year, strategy_key, cohort_key, new_state, None, reason,
            cohort_index, fc26_weight, None, len(priced), 0, INVESTMENT_ENGINE_VERSION,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "investment_engine strategy=%s cohort=%s -> %s (%s) eligible=%d candidates=%d "
        "signals_created=%d duration_ms=%d",
        strategy_key, cohort_key, new_state, reason, len(priced), candidate_count, signals_created, duration_ms,
    )

    return {
        "previous_state": None, "new_state": new_state,
        "eligible_card_count": len(priced), "candidate_count": candidate_count,
        "signals_created": signals_created, "signals_closed": 0,
        "signals_suppressed": sum(suppression_reasons.values()), "suppression_reasons": suppression_reasons,
        "duration_ms": duration_ms,
    }


async def run_all_strategies(pool, *, game_year: int, config=INVESTMENT_ENGINE_CONFIG,
                              hours_since_launch: Optional[float] = None) -> None:
    if not config.enabled:
        return
    for strategy_key, strategy in STRATEGIES.items():
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    if getattr(strategy, "USES_CRASH_TRIGGER", True):
                        summary = await run_cohort_pass(
                            conn, game_year=game_year, strategy=strategy, config=config,
                            hours_since_launch=hours_since_launch,
                        )
                    else:
                        summary = await run_launch_window_pass(
                            conn, game_year=game_year, strategy=strategy, config=config,
                            hours_since_launch=hours_since_launch,
                        )
                    await ledger.record_run(
                        conn, game_year=game_year, strategy_key=strategy_key,
                        cohort_key=strategy.COHORT_KEY, **summary,
                    )
        except Exception:
            log.warning("investment_engine pass failed for strategy=%s", strategy_key, exc_info=True)
            try:
                async with pool.acquire() as conn:
                    await ledger.record_run(
                        conn, game_year=game_year, strategy_key=strategy_key,
                        cohort_key=getattr(strategy, "COHORT_KEY", "unknown"),
                        new_state=sm.NORMAL, error="pass_failed_see_logs",
                    )
            except Exception:
                pass


async def refresher_loop(pool, poll_seconds: int, *, game_year: int) -> None:
    """Registered in main.py's lifespan exactly like the other refresher
    loops (fair_value, futgg_market_snapshot, priority_recompute, etc.) -
    see that file's own INVESTMENT_ENGINE_INTERVAL_SECONDS wiring."""
    import asyncio as _asyncio

    await _asyncio.sleep(30)
    while True:
        try:
            await run_all_strategies(pool, game_year=game_year)
        except Exception:
            log.warning("investment engine refresher pass failed", exc_info=True)
        await _asyncio.sleep(poll_seconds)
