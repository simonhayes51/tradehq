# app/services/investment_engine/strategies/launch_starter_accumulation.py
"""
LAUNCH_STARTER_ACCUMULATION.

FC26-supported (Phase 8): the desirable_starter cohort (liquid, cohort-
relative mid-price cards - NOT the literal FC26 351-550 coin band, which
is translated via percentile/cohort-relative logic rather than assumed
to apply to FC27's different price scale) did NOT exhibit the premium
cohort's deep V-shaped crash. Its historical edge came from buying EARLY
in the pre-Full-Release window and holding several days, not from timing
a bottom.

This strategy therefore deliberately does NOT use the drawdown/
deceleration crash trigger, does NOT gate on cohort regime state the way
PREMIUM_POST_CRASH_REBOUND does, and is eligible purely on elapsed launch
time (see is_launch_window_eligible) plus the usual liquidity/
tradeability risk controls every strategy shares (ranking.py).
"""
from __future__ import annotations

from app.services.investment_engine.cohorts import COHORT_DESIRABLE_STARTER

KEY = "LAUNCH_STARTER_ACCUMULATION"
COHORT_KEY = COHORT_DESIRABLE_STARTER
USES_CRASH_TRIGGER = False

DESCRIPTION = (
    "Accumulate liquid, cohort-relative mid-price 'desirable starter' "
    "cards during the pre-Full-Release launch window, holding several "
    "days. FC26-supported: this cohort did not exhibit the premium "
    "cohort's deep crash, and its historical edge came from EARLY entry, "
    "not from timing a bottom - this strategy never touches the "
    "drawdown/deceleration crash trigger PREMIUM_POST_CRASH_REBOUND uses."
)


def is_launch_window_eligible(hours_since_launch: float, config) -> bool:
    if hours_since_launch < 0:
        return False
    return hours_since_launch <= config.starter_launch_window_days * 24.0


def target_roi_pct(config) -> float:
    return config.starter_target_roi_pct


def max_hold_hours(config) -> float:
    return config.starter_max_hold_hours
