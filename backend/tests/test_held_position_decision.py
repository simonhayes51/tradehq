from types import SimpleNamespace

from app.services.held_position_decision import decide_held_position


def ci(**overrides):
    base = {
        "recommended_sell_target": None,
        "signal": "buy",
        "trend_state": "stable",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_live_discovery_target_cannot_force_an_after_tax_loss():
    # Production bug case: bought at 2.304m, current 2.4m, recalculated live
    # target 2.2m. 2.4m sounds higher than target, but nets only 2.28m after
    # EA tax and is therefore still below purchase price.
    decision = decide_held_position(
        {"current_bin": 2_400_000},
        ci(recommended_sell_target=2_200_000),
        purchase_price=2_304_000,
        original_target=2_425_500,
    )
    assert decision.verdict == "HOLD"
    assert decision.break_even_bin >= 2_425_000
    assert decision.net_profit_now < 0
    assert decision.trigger == "no_exit_trigger"


def test_negative_market_signal_below_break_even_warns_but_does_not_auto_sell():
    decision = decide_held_position(
        {"current_bin": 90_000},
        ci(signal="avoid", recommended_sell_target=85_000),
        purchase_price=100_000,
        original_target=115_000,
    )
    assert decision.verdict == "HOLD"
    assert decision.trigger == "negative_signal_below_break_even"
    assert decision.underwater_now is True
    assert "no automatic loss-cut rule" in decision.reasoning


def test_recorded_target_reached_profitably_is_sell():
    decision = decide_held_position(
        {"current_bin": 120_000},
        ci(recommended_sell_target=118_000),
        purchase_price=100_000,
        original_target=120_000,
    )
    assert decision.verdict == "SELL"
    assert decision.trigger == "original_target_reached"
    assert decision.net_profit_now > 0


def test_profitable_downtrend_can_exit_before_original_target():
    decision = decide_held_position(
        {"current_bin": 115_000},
        ci(trend_state="downtrend", recommended_sell_target=125_000),
        purchase_price=100_000,
        original_target=130_000,
    )
    assert decision.verdict == "SELL"
    assert decision.trigger == "profitable_negative_trend"
    assert decision.profitable_now is True


def test_price_unavailable_fails_closed_to_hold():
    decision = decide_held_position(
        {"current_bin": None},
        ci(signal="sell"),
        purchase_price=100_000,
    )
    assert decision.verdict == "HOLD"
    assert decision.trigger == "price_unavailable"
    assert decision.current_bin is None
