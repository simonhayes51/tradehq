# app/services/investment_engine/strategies/premium_post_crash_rebound.py
"""
PREMIUM_POST_CRASH_REBOUND.

FC26-supported (Phase 8, 8B, 8C): premium/meta golds were heavily
inflated during Early Access, declined into and after Full Release,
suffered a deep V-shaped crash (FC26's realised max ~56.5%, NOT
hardcoded as a trigger - see drawdown.py), then had a short rebound.
The strongest trailing-only trigger found was deep cohort drawdown +
decline deceleration, entering ~1 day after the realised bottom with a
short (2-3 day) useful hold; longer holds deteriorated substantially.

This strategy therefore:
  * uses the drawdown + deceleration regime trigger (state_machine.py),
  * only generates candidates while the cohort is INVESTMENT_WINDOW or
    REBOUND (never independently of cohort state),
  * targets a short hold with a fast profit-target-style exit bias,
    since FC26 found trailing reversal-confirmation exits underperformed
    a simple short fixed/target-based exit.
"""
from __future__ import annotations

from app.services.investment_engine import state_machine as sm
from app.services.investment_engine.cohorts import COHORT_PREMIUM_META

KEY = "PREMIUM_POST_CRASH_REBOUND"
COHORT_KEY = COHORT_PREMIUM_META
USES_CRASH_TRIGGER = True
ELIGIBLE_SIGNAL_STATES = sm.INVESTABLE_STATES

DESCRIPTION = (
    "Buy the premium/meta cohort's qualifying cards once its post-launch "
    "crash has decelerated within an historically extreme drawdown region "
    "(relative to the current cycle's own history, referenced against but "
    "not fixed to FC26's ~40-56.5% plateau), targeting a short 2-3 day "
    "rebound. FC26-supported: Phase 8B/8C found deep drawdown + "
    "deceleration reliably preceded a short rebound in the one cohort "
    "that crashed hard; longer holds (5-7+ days) historically deteriorated."
)


def is_state_eligible_for_signals(state: str) -> bool:
    return state in ELIGIBLE_SIGNAL_STATES


def target_roi_pct(config) -> float:
    return config.premium_target_roi_pct


def max_hold_hours(config) -> float:
    return config.premium_max_hold_hours


def regime_max_age_hours(config) -> float:
    return config.premium_regime_max_age_hours
