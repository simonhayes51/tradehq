"""Conservative exit logic for cards a user already owns.

Discovery intelligence answers "is this card mispriced now?". A held position
needs a different question: "given what I actually paid, tax, my recorded target
and today's market state, is there a defensible reason to exit?"

This module is intentionally deterministic and does not forecast future prices.
It never recommends crystallising a loss merely because a newly recalculated
market/discovery target sits below the user's purchase break-even.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Optional

from app.services import trading_math as tm
from app.services.futgg_intelligence import CardIntelligence
from app.services.futgg_trend import DOWNTREND, FALLING_KNIFE


@dataclass(frozen=True)
class HeldPositionDecision:
    verdict: str
    trigger: str
    reasoning: str
    current_bin: Optional[int]
    purchase_price: int
    break_even_bin: int
    original_target: Optional[int]
    live_market_target: Optional[int]
    net_profit_now: Optional[int]
    net_roi_now: Optional[float]
    profitable_now: bool
    underwater_now: bool
    trend_state: Optional[str]
    market_signal: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _money(value: Optional[int]) -> str:
    return "—" if value is None else f"{int(value):,}"


def decide_held_position(
    market_row: dict[str, Any],
    ci: CardIntelligence,
    *,
    purchase_price: int,
    original_target: Optional[int] = None,
) -> HeldPositionDecision:
    purchase = int(purchase_price)
    break_even = int(tm.break_even_sale_price(purchase))
    current_raw = market_row.get("current_bin")
    current = int(current_raw) if current_raw is not None else None
    recorded_target = int(original_target) if original_target else None
    live_target = int(ci.recommended_sell_target) if ci.recommended_sell_target is not None else None

    profit_decimal: Optional[Decimal] = tm.net_profit(current, purchase) if current is not None else None
    roi_decimal = tm.net_roi(current, purchase) if current is not None else None
    net_profit = int(profit_decimal) if profit_decimal is not None else None
    net_roi = float(roi_decimal) if roi_decimal is not None else None
    profitable = bool(profit_decimal is not None and profit_decimal > 0)
    underwater = bool(profit_decimal is not None and profit_decimal < 0)

    common = dict(
        current_bin=current,
        purchase_price=purchase,
        break_even_bin=break_even,
        original_target=recorded_target,
        live_market_target=live_target,
        net_profit_now=net_profit,
        net_roi_now=net_roi,
        profitable_now=profitable,
        underwater_now=underwater,
        trend_state=ci.trend_state,
        market_signal=ci.signal,
    )

    if current is None:
        return HeldPositionDecision(
            verdict="HOLD",
            trigger="price_unavailable",
            reasoning="Live FUT.GG price is temporarily unavailable, so no verified exit can be confirmed.",
            **common,
        )

    # The user's recorded target is the first-class plan. It is only a SELL
    # trigger when it also clears tax break-even; a bad historical target must
    # not turn a loss into a success label.
    if recorded_target is not None and current >= recorded_target and current >= break_even:
        return HeldPositionDecision(
            verdict="SELL",
            trigger="original_target_reached",
            reasoning=(
                f"Current BIN {_money(current)} has reached the recorded target of {_money(recorded_target)} "
                f"and clears the tax break-even price of {_money(break_even)}."
            ),
            **common,
        )

    # A live recalculated target can support an exit, but never below the
    # position's actual break-even. This is the protection the old Mbappé path
    # lacked: 2.4m current vs 2.2m live target was still a loss after tax.
    if live_target is not None and current >= live_target and current >= break_even:
        return HeldPositionDecision(
            verdict="SELL",
            trigger="live_target_reached_profitably",
            reasoning=(
                f"Current BIN {_money(current)} is above the live market target of {_money(live_target)} "
                f"and above your {_money(break_even)} break-even. Available after-tax P/L is {net_profit:+,} coins."
            ),
            **common,
        )

    # A negative market call is actionable automatically only when the user can
    # exit without crystallising a loss. Underwater positions surface the risk
    # but remain HOLD because EAFC.Live has no user-defined stop-loss mandate.
    if ci.signal in {"sell", "avoid"}:
        if current >= break_even:
            return HeldPositionDecision(
                verdict="SELL",
                trigger="negative_market_signal_profitable_exit",
                reasoning=(
                    f"The live engine now rates the card {ci.signal.upper()} and the current BIN {_money(current)} "
                    f"still clears your {_money(break_even)} break-even."
                ),
                **common,
            )
        return HeldPositionDecision(
            verdict="HOLD",
            trigger="negative_signal_below_break_even",
            reasoning=(
                f"The live engine now rates the card {ci.signal.upper()}, but selling at {_money(current)} would "
                f"realise {abs(net_profit or 0):,} coins of after-tax loss. Break-even is {_money(break_even)}; "
                "no automatic loss-cut rule is configured for this position."
            ),
            **common,
        )

    if ci.trend_state in {FALLING_KNIFE, DOWNTREND}:
        if profitable:
            roi_text = f" ({net_roi * 100:.1f}% after tax)" if net_roi is not None else ""
            return HeldPositionDecision(
                verdict="SELL",
                trigger="profitable_negative_trend",
                reasoning=(
                    f"The position is profitable now{roi_text}, while the live trend has turned "
                    f"{ci.trend_state.replace('_', ' ')}."
                ),
                **common,
            )
        return HeldPositionDecision(
            verdict="HOLD",
            trigger="negative_trend_below_break_even",
            reasoning=(
                f"The trend is {ci.trend_state.replace('_', ' ')}, but current BIN {_money(current)} remains below "
                f"your {_money(break_even)} break-even. Risk is elevated, but no automatic stop-loss is configured."
            ),
            **common,
        )

    target_parts = []
    if recorded_target is not None:
        target_parts.append(f"recorded target {_money(recorded_target)}")
    if live_target is not None:
        target_parts.append(f"live market target {_money(live_target)}")
    target_text = f" Next reference: {', '.join(target_parts)}." if target_parts else ""
    pnl_text = f" Current after-tax P/L is {net_profit:+,} coins." if net_profit is not None else ""

    if ci.signal == "insufficient_data":
        reason = "No verified exit trigger is present and current market evidence is insufficient for a stronger call."
        trigger = "insufficient_market_evidence"
    else:
        reason = "No verified exit trigger is present."
        trigger = "no_exit_trigger"

    return HeldPositionDecision(
        verdict="HOLD",
        trigger=trigger,
        reasoning=reason + pnl_text + target_text,
        **common,
    )
