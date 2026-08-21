# tests/test_investment_state_machine.py
#
# Pure tests for app/services/investment_engine/state_machine.py - no DB, no I/O.
from __future__ import annotations

from app.services.investment_engine import state_machine as sm


def _inputs(**overrides):
    base = dict(
        severity_class="normal",
        is_decelerating=False,
        breadth_spread=None,
        live_confirmation_ok=None,
        overheat_pct=None,
        hours_in_state=0.0,
        regime_max_age_hours=48.0,
    )
    base.update(overrides)
    return sm.RegimeInputs(**base)


class TestNormalPath:
    def test_normal_stays_normal_with_no_signal(self):
        state, reason = sm.next_state(sm.NORMAL, _inputs())
        assert state == sm.NORMAL

    def test_launch_inflation_moves_to_overheated(self):
        state, _ = sm.next_state(sm.NORMAL, _inputs(overheat_pct=50.0))
        assert state == sm.OVERHEATED

    def test_severe_drawdown_from_normal_moves_to_crashing(self):
        state, _ = sm.next_state(sm.NORMAL, _inputs(severity_class="severe"))
        assert state == sm.CRASHING


class TestCrashDoesNotSkipToInvestmentWindow:
    """The core safety property: a cohort cannot go straight from
    CRASHING to INVESTMENT_WINDOW just because drawdown is deep - it must
    pass through WATCH (extreme severity) and BOTTOMING (deceleration)
    first. This is what stops "it's fallen a lot" alone from ever
    becoming a buy signal, per the FC26 research finding that drawdown
    magnitude alone did not predict a good entry."""

    def test_crashing_with_extreme_severity_but_no_deceleration_stays_pre_window(self):
        state, _ = sm.next_state(sm.CRASHING, _inputs(severity_class="extreme"))
        assert state == sm.WATCH

    def test_watch_without_deceleration_does_not_advance(self):
        state, _ = sm.next_state(sm.WATCH, _inputs(severity_class="extreme", is_decelerating=False))
        assert state == sm.WATCH

    def test_watch_with_deceleration_advances_to_bottoming(self):
        state, _ = sm.next_state(sm.WATCH, _inputs(severity_class="extreme", is_decelerating=True))
        assert state == sm.BOTTOMING

    def test_first_green_day_alone_cannot_reach_investment_window_from_crashing(self):
        # Simulate "it happened to have one good day" without genuine
        # multi-period deceleration and without ever reaching WATCH.
        state, _ = sm.next_state(sm.CRASHING, _inputs(severity_class="severe", is_decelerating=True))
        assert state != sm.INVESTMENT_WINDOW
        assert state == sm.CRASHING


class TestBottomingToInvestmentWindow:
    def test_missing_live_confirmation_does_not_block_the_historical_trigger(self):
        """None means 'unavailable' (e.g. FC26 backtest, or too little
        FC27 live data yet) - it must be additive evidence on top of the
        historical trigger, never a hard requirement that blocks it."""
        state, reason = sm.next_state(
            sm.BOTTOMING, _inputs(severity_class="extreme", is_decelerating=True, live_confirmation_ok=None),
        )
        assert state == sm.INVESTMENT_WINDOW

    def test_explicit_negative_live_confirmation_holds_at_bottoming(self):
        state, _ = sm.next_state(
            sm.BOTTOMING, _inputs(severity_class="extreme", is_decelerating=True, live_confirmation_ok=False),
        )
        assert state == sm.BOTTOMING

    def test_positive_live_confirmation_opens_the_window(self):
        state, _ = sm.next_state(
            sm.BOTTOMING, _inputs(severity_class="extreme", is_decelerating=True, live_confirmation_ok=True),
        )
        assert state == sm.INVESTMENT_WINDOW

    def test_losing_deceleration_falls_back_to_watch(self):
        state, _ = sm.next_state(
            sm.BOTTOMING, _inputs(severity_class="extreme", is_decelerating=False),
        )
        assert state == sm.WATCH


class TestRegimeMaxAge:
    def test_investment_window_exits_after_max_age(self):
        state, reason = sm.next_state(
            sm.INVESTMENT_WINDOW,
            _inputs(severity_class="extreme", hours_in_state=49.0, regime_max_age_hours=48.0),
        )
        assert state == sm.EXIT
        assert reason == "max_regime_age_reached"

    def test_rebound_exits_after_max_age_too(self):
        state, _ = sm.next_state(
            sm.REBOUND,
            _inputs(severity_class="extreme", hours_in_state=100.0, regime_max_age_hours=48.0),
        )
        assert state == sm.EXIT

    def test_within_max_age_stays_open(self):
        state, _ = sm.next_state(
            sm.INVESTMENT_WINDOW,
            _inputs(severity_class="extreme", hours_in_state=10.0, regime_max_age_hours=48.0),
        )
        assert state == sm.INVESTMENT_WINDOW


class TestExitAndNormalisation:
    def test_investment_window_exits_when_drawdown_normalises(self):
        state, reason = sm.next_state(sm.INVESTMENT_WINDOW, _inputs(severity_class="normal"))
        assert state == sm.EXIT
        assert reason == "drawdown_normalised"

    def test_exit_returns_to_normal_with_no_active_signal(self):
        state, _ = sm.next_state(sm.EXIT, _inputs())
        assert state == sm.NORMAL


class TestAllStatesHandled:
    def test_every_named_state_produces_a_valid_transition(self):
        for state in sm.ALL_STATES:
            new_state, reason = sm.next_state(state, _inputs())
            assert new_state in sm.ALL_STATES
            assert isinstance(reason, str) and reason
