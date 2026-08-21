# tests/test_investment_drawdown.py
#
# Pure tests for app/services/investment_engine/drawdown.py - no DB, no I/O.
from __future__ import annotations

from app.services.investment_engine import drawdown as dd
from app.services.investment_engine.config import InvestmentEngineConfig

CONFIG = InvestmentEngineConfig()


class TestComputeDrawdown:
    def test_no_history_means_no_drawdown(self):
        snap = dd.compute_drawdown([], 1000.0)
        assert snap.running_peak == 1000.0
        assert snap.drawdown_pct == 0.0

    def test_drawdown_from_prior_peak(self):
        # price_history is STRICTLY PRIOR - current_price (400) is not
        # itself included when establishing the peak.
        snap = dd.compute_drawdown([1000.0, 900.0, 950.0], 400.0)
        assert snap.running_peak == 1000.0
        assert round(snap.drawdown_pct, 1) == 60.0

    def test_new_high_produces_zero_drawdown(self):
        snap = dd.compute_drawdown([500.0, 600.0], 700.0)
        assert snap.running_peak == 700.0
        assert snap.drawdown_pct == 0.0

    def test_drawdown_never_negative(self):
        snap = dd.compute_drawdown([100.0], 100.0)
        assert snap.drawdown_pct >= 0.0


class TestDrawdownPercentile:
    def test_insufficient_history_returns_none(self):
        assert dd.drawdown_percentile([], 10.0) is None

    def test_current_worst_drawdown_ranks_at_top(self):
        pct = dd.drawdown_percentile([10.0, 20.0, 30.0], 40.0)
        assert pct == 100.0

    def test_current_best_drawdown_ranks_low(self):
        pct = dd.drawdown_percentile([10.0, 20.0, 30.0], 5.0)
        assert pct == 25.0  # only itself (5<=5) out of 4 total observations


class TestHistoricalReferencePercentile:
    def test_below_fc26_low_end_is_zero(self):
        assert dd.historical_reference_percentile(10.0, 40.0, 56.5) == 0.0

    def test_at_fc26_realised_max_is_one_hundred(self):
        assert dd.historical_reference_percentile(56.5, 40.0, 56.5) == 100.0

    def test_beyond_fc26_max_clamped_at_one_hundred(self):
        assert dd.historical_reference_percentile(90.0, 40.0, 56.5) == 100.0

    def test_midpoint_is_fifty(self):
        mid = (40.0 + 56.5) / 2
        assert round(dd.historical_reference_percentile(mid, 40.0, 56.5), 1) == 50.0


class TestSeverityScoreAndClass:
    def test_thin_cycle_history_leans_on_fc26_reference(self):
        # cycle_percentile provided but observation count below the
        # config minimum -> mostly reference-weighted.
        score, cls = dd.severity_score_and_class(
            cycle_percentile=100.0,
            historical_reference_percentile_value=0.0,
            cycle_observation_count=1,
            config=CONFIG,
        )
        # Reference weight dominates (0.8) when cycle history is thin,
        # so a 0 reference percentile pulls the blended score well below
        # what the (thin) cycle percentile alone would suggest.
        assert score < 50.0

    def test_rich_cycle_history_leans_on_cycle_percentile(self):
        score, cls = dd.severity_score_and_class(
            cycle_percentile=100.0,
            historical_reference_percentile_value=0.0,
            cycle_observation_count=50,
            config=CONFIG,
        )
        assert score > 50.0

    def test_extreme_classification_at_high_score(self):
        score, cls = dd.severity_score_and_class(
            cycle_percentile=100.0,
            historical_reference_percentile_value=100.0,
            cycle_observation_count=50,
            config=CONFIG,
        )
        assert score >= CONFIG.severity_percentile_extreme * 100
        assert cls == dd.SEVERITY_EXTREME

    def test_normal_classification_at_low_score(self):
        score, cls = dd.severity_score_and_class(
            cycle_percentile=0.0,
            historical_reference_percentile_value=0.0,
            cycle_observation_count=50,
            config=CONFIG,
        )
        assert cls == dd.SEVERITY_NORMAL

    def test_no_absolute_fc26_number_is_a_hard_gate(self):
        """A launch that never reaches FC26's 40-56.5% reference band can
        still classify as extreme, purely from its OWN cycle-relative
        percentile - this is the whole point of the dynamic model (see
        module docstring): no absolute drawdown % is a magic number."""
        score, cls = dd.severity_score_and_class(
            cycle_percentile=100.0,               # worst drawdown this cycle has shown
            historical_reference_percentile_value=0.0,  # nowhere near FC26's range (e.g. only a 15% crash)
            cycle_observation_count=50,             # plenty of the cycle's own history
            config=CONFIG,
        )
        assert cls in (dd.SEVERITY_SEVERE, dd.SEVERITY_EXTREME)
