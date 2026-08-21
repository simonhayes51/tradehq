# app/services/investment_engine/state_machine.py
"""
The Investment Engine's regime state machine, per cohort per strategy.

    NORMAL -> OVERHEATED -> CRASHING -> WATCH -> BOTTOMING
        -> INVESTMENT_WINDOW -> REBOUND -> EXIT -> CLOSED

Individual investment candidates are only ever generated while a
cohort's own state is INVESTMENT_WINDOW or REBOUND (see runner.py and
strategies/premium_post_crash_rebound.py's ELIGIBLE_SIGNAL_STATES) - a
card never independently becomes an investment candidate just because
its own chart looks attractive while its cohort is still CRASHING. This
is the market-regime gate the product spec calls out as essential.

Pure module: no I/O, no notion of wall-clock time beyond what's passed
in via RegimeInputs.hours_in_state - the caller supplies every input,
this function only decides the transition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

NORMAL = "NORMAL"
OVERHEATED = "OVERHEATED"
CRASHING = "CRASHING"
WATCH = "WATCH"
BOTTOMING = "BOTTOMING"
INVESTMENT_WINDOW = "INVESTMENT_WINDOW"
REBOUND = "REBOUND"
EXIT = "EXIT"
CLOSED = "CLOSED"

ALL_STATES = (
    NORMAL, OVERHEATED, CRASHING, WATCH, BOTTOMING,
    INVESTMENT_WINDOW, REBOUND, EXIT, CLOSED,
)

# States in which individual candidate generation is allowed at all.
INVESTABLE_STATES = (INVESTMENT_WINDOW, REBOUND)


@dataclass(frozen=True)
class RegimeInputs:
    severity_class: str                     # normal | elevated | severe | extreme (drawdown.py)
    is_decelerating: bool                    # momentum.py
    breadth_spread: Optional[float]          # pct_rising - pct_falling, None if unknown
    live_confirmation_ok: Optional[bool]     # None = unavailable (FC26 backtest, or FC27 with too little live data)
    overheat_pct: Optional[float]            # cohort index vs its own recent low; None if unknown
    hours_in_state: float
    regime_max_age_hours: float
    overheat_threshold_pct: float = 40.0
    overheat_fade_threshold_pct: float = 15.0


def next_state(current: str, inputs: RegimeInputs) -> Tuple[str, str]:
    """Returns (new_state, reason). A regime that has overstayed its
    welcome always winds down first, regardless of what the other
    numbers say - FC26 evidence (Phase 8C section 6) found fresh entries
    were already poor by ~2 days past the real bottom, so an
    INVESTMENT_WINDOW/REBOUND that's been open too long is force-exited
    rather than left running indefinitely on stale confirmation."""

    if current in (INVESTMENT_WINDOW, REBOUND) and inputs.hours_in_state >= inputs.regime_max_age_hours:
        return EXIT, "max_regime_age_reached"

    if current in (NORMAL, CLOSED, EXIT, ""):
        if inputs.overheat_pct is not None and inputs.overheat_pct >= inputs.overheat_threshold_pct:
            return OVERHEATED, "launch_inflation_detected"
        if inputs.severity_class in ("severe", "extreme"):
            return CRASHING, "drawdown_severe_on_entry"
        return NORMAL, "no_regime"

    if current == OVERHEATED:
        if inputs.severity_class in ("severe", "extreme"):
            return CRASHING, "overheated_rolled_into_crash"
        if inputs.overheat_pct is not None and inputs.overheat_pct < inputs.overheat_fade_threshold_pct:
            return NORMAL, "inflation_faded"
        return OVERHEATED, "still_overheated"

    if current == CRASHING:
        if inputs.severity_class == "extreme":
            return WATCH, "drawdown_reached_extreme_region"
        if inputs.severity_class in ("normal", "elevated"):
            return NORMAL, "drawdown_recovered"
        return CRASHING, "still_crashing"

    if current == WATCH:
        if inputs.severity_class not in ("severe", "extreme"):
            return CRASHING, "severity_receded_from_watch"
        if inputs.is_decelerating:
            return BOTTOMING, "decline_decelerating"
        return WATCH, "awaiting_deceleration"

    if current == BOTTOMING:
        if not inputs.is_decelerating and inputs.severity_class == "extreme":
            return WATCH, "deceleration_lost"
        if inputs.live_confirmation_ok is False:
            return BOTTOMING, "awaiting_live_confirmation"
        # None (unavailable, e.g. FC26 backtest or too little FC27 live
        # data yet) is treated as "not blocking" rather than "blocking" -
        # the whole point of live_confirmation is to be additive
        # evidence on top of the historical trigger, never a hard
        # requirement the historical trigger alone can't satisfy.
        return INVESTMENT_WINDOW, "cohort_regime_open"

    if current == INVESTMENT_WINDOW:
        if inputs.severity_class == "normal":
            return EXIT, "drawdown_normalised"
        if inputs.breadth_spread is not None and inputs.breadth_spread < 0:
            return REBOUND, "breadth_still_mixed_monitoring"
        return INVESTMENT_WINDOW, "window_open"

    if current == REBOUND:
        if inputs.severity_class == "normal":
            return EXIT, "drawdown_normalised"
        if inputs.breadth_spread is not None and inputs.breadth_spread > 5:
            return INVESTMENT_WINDOW, "breadth_reconfirmed"
        return REBOUND, "monitoring_weak_rebound"

    return NORMAL, "unhandled_state_defaulted_to_normal"
