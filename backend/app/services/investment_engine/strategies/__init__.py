# app/services/investment_engine/strategies/
"""
Strategy registry. Each strategy is its own module exposing a small,
consistent shape (KEY, COHORT_KEY, USES_CRASH_TRIGGER, DESCRIPTION, plus
whatever entry/exit helper functions it needs) rather than a class
hierarchy - adding a new strategy (pre-promo sell-off rebound, fodder
accumulation, SBC rating investments, etc., all explicitly NOT built yet
per the product spec) means adding a new module and one line here, not
touching runner.py, the state machine, or any other strategy.
"""
from __future__ import annotations

from typing import Dict

from . import launch_starter_accumulation, premium_post_crash_rebound

STRATEGIES: Dict[str, object] = {
    premium_post_crash_rebound.KEY: premium_post_crash_rebound,
    launch_starter_accumulation.KEY: launch_starter_accumulation,
}
