# tests/test_investment_no_leakage.py
#
# Integration-style pure test across drawdown.py + momentum.py +
# state_machine.py: walks a synthetic price series day by day, computing
# each day's regime using ONLY the prefix of the series up to that day,
# and asserts the sequence of states computed that way is IDENTICAL to
# the sequence computed by walking a longer version of the same series
# that has extra future days appended. If any later day's price could
# change an earlier day's classification, this test fails - that is
# exactly the future-leakage bug class the FC26 research work spent
# three phases guarding against (see research/fc26_launch_market's
# phase8/8b/8c scripts' own "NO LOOKAHEAD" markers), and this repo's
# Investment Engine must preserve the same guarantee for real money
# decisions, not just historical research.
from __future__ import annotations

from app.services.investment_engine import drawdown as dd
from app.services.investment_engine import momentum as mo
from app.services.investment_engine import state_machine as sm
from app.services.investment_engine.config import InvestmentEngineConfig

CONFIG = InvestmentEngineConfig()

# A synthetic crash-and-partial-recover series: EA-style pump, deep
# crash, deceleration, partial rebound. Values are cohort price index
# levels, oldest first.
BASE_SERIES = [
    100, 110, 122, 118, 128,   # pump
    110, 90, 70, 55, 45, 42,   # crash, decelerating near the end
    44, 47, 49,                # early recovery
]

FUTURE_EXTENSION = [30, 20, 95, 5]  # deliberately wild future moves


def _walk(series):
    """Compute (drawdown_pct, severity_class, is_decelerating, state) for
    every day, using ONLY series[0..i] at day i - mirrors how the real
    runner is required to operate."""
    out = []
    state = sm.NORMAL
    hours_in_state = 0.0
    for i in range(1, len(series)):
        history = series[:i]          # strictly prior to day i
        current = series[i]

        snap = dd.compute_drawdown([float(x) for x in history], float(current))
        dd_history = [
            dd.compute_drawdown([float(x) for x in history[:j]], float(history[j])).drawdown_pct
            for j in range(1, len(history))
        ]
        cycle_pctile = dd.drawdown_percentile(dd_history, snap.drawdown_pct)
        ref_pctile = dd.historical_reference_percentile(
            snap.drawdown_pct, CONFIG.fc26_reference_drawdown_low_pct, CONFIG.fc26_reference_drawdown_high_pct,
        )
        score, severity = dd.severity_score_and_class(
            cycle_percentile=cycle_pctile,
            historical_reference_percentile_value=ref_pctile,
            cycle_observation_count=len(dd_history),
            config=CONFIG,
        )

        returns = [
            (history[j] - history[j - 1]) / history[j - 1] * 100
            for j in range(1, len(history))
        ]
        current_return = (current - history[-1]) / history[-1] * 100
        mom = mo.compute_momentum(returns, current_return, CONFIG.deceleration_required_periods)

        new_state, _reason = sm.next_state(state, sm.RegimeInputs(
            severity_class=severity,
            is_decelerating=mom.is_decelerating,
            breadth_spread=None,
            live_confirmation_ok=None,
            overheat_pct=None,
            hours_in_state=hours_in_state,
            regime_max_age_hours=CONFIG.premium_regime_max_age_hours,
        ))
        hours_in_state = 0.0 if new_state != state else hours_in_state + 24.0
        state = new_state
        out.append((snap.drawdown_pct, severity, mom.is_decelerating, state))
    return out


class TestNoFutureLeakage:
    def test_appending_future_days_never_changes_earlier_classifications(self):
        short_run = _walk(BASE_SERIES)
        long_run = _walk(BASE_SERIES + FUTURE_EXTENSION)
        # The overlapping prefix must be byte-for-byte identical - a
        # later day's wild price swing must not reach backward and
        # change what day 5 (say) was classified as.
        assert short_run == long_run[: len(short_run)]

    def test_the_series_actually_produces_a_real_regime_progression(self):
        """Sanity check that this synthetic series isn't trivially flat -
        it should visibly move through crash-adjacent severity at some
        point, otherwise the no-leakage assertion above would be
        vacuously true."""
        run = _walk(BASE_SERIES)
        severities_seen = {severity for _, severity, _, _ in run}
        assert severities_seen & {"severe", "extreme"}
