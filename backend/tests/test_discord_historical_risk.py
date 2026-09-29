from datetime import date
from types import SimpleNamespace

from app.services import discord_historical_risk as risk


def _prior(*, median=-6.0, negative_rate=0.75, n=100):
    return risk.HistoricalPrior(
        anchor="full_release",
        lifecycle_offset_days=4,
        baseline_date=date(2025, 9, 30),
        target_date=date(2025, 10, 3),
        sample_size=n,
        median_return_pct=median,
        negative_rate=negative_rate,
        cohort="gold_rare:84-86",
    )


def _ci(*, trend="insufficient_trend_data", buy=10000, target=12500):
    return SimpleNamespace(
        trend_state=trend,
        current_executable_buy=buy,
        recommended_sell_target=target,
    )


def test_fc27_full_release_offset_maps_to_fc26_real_full_release():
    # FC27 worldwide launch was Sep 25; FC26 was Sep 26. Four days after
    # FC27 full release must therefore compare Sep 30 -> Oct 3 in FC26.
    match = risk._lifecycle_match(date(2026, 9, 29))
    assert match == (
        "full_release",
        4,
        date(2025, 9, 30),
        date(2025, 10, 3),
    )


def test_severe_fc26_prior_blocks_unconfirmed_launch_buy():
    decision = risk.decide_from_prior(
        _prior(median=-8.0, negative_rate=0.82),
        _ci(trend="insufficient_trend_data", buy=12750, target=23500),
    )
    assert decision.block is True
    assert "strongly bearish" in decision.reason


def test_bearish_prior_blocks_large_target_when_live_market_is_not_recovering():
    decision = risk.decide_from_prior(
        _prior(median=-3.0, negative_rate=0.65),
        _ci(trend="sideways", buy=16500, target=23250),
    )
    assert decision.block is True
    assert "needs 41% upside" in decision.reason


def test_live_recovery_can_override_moderate_one_season_prior():
    decision = risk.decide_from_prior(
        _prior(median=-3.0, negative_rate=0.65),
        _ci(trend="recovering", buy=16500, target=19000),
    )
    assert decision.block is False


def test_non_bearish_prior_does_not_block():
    decision = risk.decide_from_prior(
        _prior(median=2.5, negative_rate=0.40),
        _ci(trend="sideways", buy=10000, target=14000),
    )
    assert decision.block is False
