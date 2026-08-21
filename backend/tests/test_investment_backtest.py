# tests/test_investment_backtest.py
#
# Pure tests for app/services/investment_engine/backtest.py's
# walk_cohort_regime() - no DB. Validates the four assertions the
# product spec explicitly requires of the backtest harness:
#   1. premium cards remain blocked during early hype/crash
#   2. the historical premium rebound regime opens near the previously
#      identified window (not on day 1, not never)
#   3. longer hold periods are not accidentally treated as desirable
#   4. the starter strategy does not use the premium crash trigger
#      (covered in test_investment_strategies.py, referenced here too)
from __future__ import annotations

from datetime import date, timedelta

from app.services.investment_engine import state_machine as sm
from app.services.investment_engine.backtest import summarize, walk_cohort_regime
from app.services.investment_engine.config import InvestmentEngineConfig

CONFIG = InvestmentEngineConfig()


def _series(values, start=date(2025, 9, 17)):
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


# Shape modelled on the real FC26 premium/meta finding (Phase 8/8B/8C):
# Early Access pump, deep crash, decelerating into a bottom, short partial
# rebound. Values are illustrative, not the literal FC26 numbers.
PREMIUM_LIKE_SERIES = [
    100, 118, 135, 150, 165, 176,        # Early Access pump (days 0-5)
    160, 130, 100, 75, 55, 40, 30, 24,   # crash, decelerating by the end (days 6-13)
    25, 27, 29, 28,                      # short partial rebound (days 14-17)
    27, 26, 25, 24, 23,                  # rebound fades / rolls over (days 18-22)
]


class TestPremiumBlockedDuringHypeAndEarlyCrash:
    def test_never_reaches_investment_window_during_the_pump(self):
        days = walk_cohort_regime(_series(PREMIUM_LIKE_SERIES), config=CONFIG, regime_max_age_hours=48.0)
        pump_days = days[:6]
        assert all(d.state not in sm.INVESTABLE_STATES for d in pump_days)

    def test_never_reaches_investment_window_in_the_first_few_days_of_the_crash(self):
        """'It has fallen a lot' alone (early in the crash, before real
        deceleration) must not open the window - this is the FC26
        finding that drawdown magnitude alone did not predict a good
        entry."""
        days = walk_cohort_regime(_series(PREMIUM_LIKE_SERIES), config=CONFIG, regime_max_age_hours=48.0)
        early_crash_days = days[6:9]  # days 6-8: crashing hard, not yet decelerated
        assert all(d.state not in sm.INVESTABLE_STATES for d in early_crash_days)


class TestRebindOpensNearTheHistoricallyIdentifiedWindow:
    def test_regime_eventually_opens_once_the_series_decelerates_and_bottoms(self):
        days = walk_cohort_regime(_series(PREMIUM_LIKE_SERIES), config=CONFIG, regime_max_age_hours=48.0)
        report = summarize(days, "premium_meta", 48.0)
        assert report.regime_opened is True
        # Opens somewhere in the deceleration/bottom region (days 10-15),
        # not on day 0-1 (too early) and not never.
        window_index = next(i for i, d in enumerate(days) if d.state in sm.INVESTABLE_STATES)
        assert 8 <= window_index <= 16

    def test_a_cohort_that_never_decelerates_never_opens_a_window(self):
        still_falling = [100, 90, 80, 70, 55, 40, 25, 15, 8, 4, 2, 1]
        days = walk_cohort_regime(_series(still_falling), config=CONFIG, regime_max_age_hours=48.0)
        report = summarize(days, "premium_meta", 48.0)
        assert report.regime_opened is False


class TestLongerHoldsNotFavoured:
    def test_regime_max_age_forces_an_exit_rather_than_running_indefinitely(self):
        days = walk_cohort_regime(_series(PREMIUM_LIKE_SERIES), config=CONFIG, regime_max_age_hours=48.0)
        window_index = next(i for i, d in enumerate(days) if d.state in sm.INVESTABLE_STATES)
        # Within a handful of days after opening, the regime must have
        # moved on (EXIT or reverted) rather than staying INVESTMENT_
        # WINDOW/REBOUND for the rest of the series - a short 2-day-
        # equivalent regime_max_age_hours (48h = 2 daily periods) means
        # it cannot still be open 6+ days later.
        later = days[window_index + 6:]
        assert all(d.state not in sm.INVESTABLE_STATES for d in later)

    def test_default_config_premium_max_hold_is_short_not_a_multi_week_hold(self):
        """FC26 found 5-7+ day holds deteriorated substantially - the
        shipped default must reflect a short hold, not accidentally
        default to something long."""
        assert CONFIG.premium_max_hold_hours <= 96.0  # 4 days, generous upper bound
        assert CONFIG.premium_regime_max_age_hours <= 96.0
