# tests/test_investment_momentum.py
#
# Pure tests for app/services/investment_engine/momentum.py - no DB, no I/O.
from __future__ import annotations

from app.services.investment_engine import momentum as m


class TestComputeMomentum:
    def test_two_period_deceleration_is_detected(self):
        # -12% -> -8% -> -4%: each step less negative than the last.
        snap = m.compute_momentum([-12.0, -8.0], -4.0, required_periods=2)
        assert snap.is_decelerating is True
        assert snap.deceleration_count == 2

    def test_still_accelerating_is_not_decelerating(self):
        # -4% -> -8% -> -12%: getting WORSE, not better.
        snap = m.compute_momentum([-4.0, -8.0], -12.0, required_periods=2)
        assert snap.is_decelerating is False
        assert snap.deceleration_count == 0

    def test_single_improving_step_does_not_satisfy_two_period_requirement(self):
        snap = m.compute_momentum([-12.0], -8.0, required_periods=2)
        assert snap.deceleration_count == 1
        assert snap.is_decelerating is False

    def test_three_period_requirement_needs_three_consecutive_improvements(self):
        snap = m.compute_momentum([-20.0, -12.0, -8.0], -4.0, required_periods=3)
        assert snap.deceleration_count == 3
        assert snap.is_decelerating is True

    def test_chain_breaks_on_first_non_improving_step_walking_backward(self):
        # ... -20, -8, -12, -4: last step improves (-12 -> -4), the one
        # before it does NOT (-8 -> -12 is worse), so the chain stops
        # there - count is 1, not "2 out of the last 3".
        snap = m.compute_momentum([-20.0, -8.0, -12.0], -4.0, required_periods=2)
        assert snap.deceleration_count == 1
        assert snap.is_decelerating is False

    def test_no_history_still_returns_a_snapshot(self):
        snap = m.compute_momentum([], -5.0, required_periods=2)
        assert snap.previous_return_pct is None
        assert snap.deceleration_count == 0
        assert snap.is_decelerating is False

    def test_deceleration_strength_reflects_total_improvement(self):
        snap = m.compute_momentum([-12.0, -8.0], -4.0, required_periods=2)
        # From -12 (start of the 2-step improving chain) to -4 (current).
        assert snap.deceleration_strength == 8.0

    def test_naive_first_green_day_alone_is_not_treated_as_bottoming(self):
        """FC26 research finding this module must not reproduce: a bare
        'first positive day' should not, by itself, satisfy a >=2-period
        deceleration requirement unless it's actually part of a genuine
        improving chain of that length."""
        # Deep in an accelerating decline, then one green day.
        snap = m.compute_momentum([-30.0, -25.0, -35.0], 2.0, required_periods=2)
        # Only the single last step (-35 -> 2) improved; the one before
        # it (-25 -> -35) did not, so this is NOT decelerating under a
        # 2-period requirement.
        assert snap.deceleration_count == 1
        assert snap.is_decelerating is False
