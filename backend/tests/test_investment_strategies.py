# tests/test_investment_strategies.py
#
# Pure tests for app/services/investment_engine/strategies/ - no DB, no I/O.
from __future__ import annotations

from app.services.investment_engine import state_machine as sm
from app.services.investment_engine.config import InvestmentEngineConfig
from app.services.investment_engine.strategies import STRATEGIES
from app.services.investment_engine.strategies import (
    launch_starter_accumulation as starter,
    premium_post_crash_rebound as premium,
)

CONFIG = InvestmentEngineConfig()


class TestStrategyRegistry:
    def test_both_required_strategies_are_registered(self):
        assert premium.KEY in STRATEGIES
        assert starter.KEY in STRATEGIES

    def test_strategy_keys_are_distinct(self):
        assert premium.KEY != starter.KEY

    def test_each_strategy_declares_its_own_cohort(self):
        assert premium.COHORT_KEY == "premium_meta"
        assert starter.COHORT_KEY == "desirable_starter"


class TestPremiumStrategyGating:
    def test_signals_only_eligible_in_investment_window_or_rebound(self):
        assert premium.is_state_eligible_for_signals(sm.INVESTMENT_WINDOW) is True
        assert premium.is_state_eligible_for_signals(sm.REBOUND) is True

    def test_signals_not_eligible_while_still_crashing(self):
        """The essential market-regime gate: a card never independently
        becomes an investment candidate while its cohort is still
        CRASHING, however good its own chart looks."""
        for blocked_state in (sm.NORMAL, sm.OVERHEATED, sm.CRASHING, sm.WATCH, sm.BOTTOMING, sm.EXIT, sm.CLOSED):
            assert premium.is_state_eligible_for_signals(blocked_state) is False

    def test_uses_crash_trigger_flag_is_true(self):
        assert premium.USES_CRASH_TRIGGER is True

    def test_hold_and_target_come_from_config_not_hardcoded_in_the_module(self):
        assert premium.max_hold_hours(CONFIG) == CONFIG.premium_max_hold_hours
        assert premium.target_roi_pct(CONFIG) == CONFIG.premium_target_roi_pct
        assert premium.regime_max_age_hours(CONFIG) == CONFIG.premium_regime_max_age_hours


class TestStarterStrategyDoesNotUseCrashTrigger:
    def test_uses_crash_trigger_flag_is_false(self):
        """FC26 finding this must preserve: the starter cohort's edge
        came from early entry, not from timing a crash/bottom - this
        strategy must never gate on the drawdown/deceleration regime the
        premium strategy uses."""
        assert starter.USES_CRASH_TRIGGER is False

    def test_no_reference_to_state_machine_investable_states(self):
        import inspect
        source = inspect.getsource(starter)
        assert "state_machine" not in source
        assert "INVESTMENT_WINDOW" not in source

    def test_eligibility_is_purely_time_based(self):
        assert starter.is_launch_window_eligible(0.0, CONFIG) is True
        assert starter.is_launch_window_eligible(CONFIG.starter_launch_window_days * 24.0, CONFIG) is True
        assert starter.is_launch_window_eligible(CONFIG.starter_launch_window_days * 24.0 + 1.0, CONFIG) is False

    def test_negative_elapsed_time_is_not_eligible(self):
        assert starter.is_launch_window_eligible(-1.0, CONFIG) is False
