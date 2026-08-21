# app/services/investment_engine/drawdown.py
"""
Dynamic/relative drawdown severity model.

Explicitly NOT "drawdown >= 40%" or "drawdown >= 50%" as a hardcoded
trigger - FC26 Phase 8C found that region worked for FC26's specific
56.5%-magnitude crash, and flagged in its own conclusions that an
absolute threshold risks being calibrated to that one launch. A milder
FC27 launch might never reach 40%; a harsher one might blow through 60%
before decelerating.

Instead, severity is a blend of:
  1. Where today's drawdown ranks against every drawdown THIS cohort has
     already shown so far in the CURRENT cycle (percentile-of-own-
     history) - this is what makes the model self-calibrating to
     whatever magnitude FC27 actually produces.
  2. Where it sits relative to the FC26 reference range, as a prior -
     weighted down once enough of the cycle's own history exists to
     rank against on its own (see min_cycle_observations_for_percentile
     in config.py), and down further over elapsed launch time (see
     runner.py's FC26-prior-weight concept).

Pure module: no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

SEVERITY_NORMAL = "normal"
SEVERITY_ELEVATED = "elevated"
SEVERITY_SEVERE = "severe"
SEVERITY_EXTREME = "extreme"


@dataclass(frozen=True)
class DrawdownSnapshot:
    current_price: float
    running_peak: float
    drawdown_pct: float  # 0-100, always >= 0


def compute_drawdown(price_history: Sequence[float], current_price: float) -> DrawdownSnapshot:
    """`price_history` must be STRICTLY PRIOR observations for this cycle
    (NO LOOKAHEAD) - the caller is responsible for that boundary. The
    running peak is the max of history and current_price; it can never
    be set by a price that hasn't happened yet from the perspective of
    the day/tick being evaluated.
    """
    peak = max([*price_history, current_price]) if price_history else current_price
    if peak <= 0:
        return DrawdownSnapshot(current_price=current_price, running_peak=peak, drawdown_pct=0.0)
    dd = (peak - current_price) / peak * 100
    return DrawdownSnapshot(current_price=current_price, running_peak=peak, drawdown_pct=max(0.0, dd))


def drawdown_percentile(
    drawdown_history_pct: Sequence[float], current_drawdown_pct: float,
) -> Optional[float]:
    """Percentile [0,100] of `current_drawdown_pct` among every drawdown
    observed so far this cycle (drawdown_history_pct + current itself).
    NO LOOKAHEAD: drawdown_history_pct must be strictly prior
    observations. Returns None if there's too little history (fewer than
    2 total observations) to rank meaningfully - callers should treat
    None as "unknown", never as 0.
    """
    all_obs = [*drawdown_history_pct, current_drawdown_pct]
    if len(all_obs) < 2:
        return None
    rank = sum(1 for x in all_obs if x <= current_drawdown_pct) / len(all_obs) * 100
    return rank


def historical_reference_percentile(
    current_drawdown_pct: float, fc26_low_pct: float, fc26_high_pct: float,
) -> float:
    """Where current_drawdown sits relative to the FC26 premium/meta
    prior range [fc26_low, fc26_high] - a REFERENCE bound, never an
    instruction to wait for a specific number. 0 = at/below the low end
    of what FC26's validated plateau showed; 100 = at/above FC26's own
    realised maximum.
    """
    span = fc26_high_pct - fc26_low_pct
    if span <= 0:
        return 0.0
    return max(0.0, min(100.0, (current_drawdown_pct - fc26_low_pct) / span * 100))


def severity_score_and_class(
    *,
    cycle_percentile: Optional[float],
    historical_reference_percentile_value: float,
    cycle_observation_count: int,
    config,
) -> Tuple[float, str]:
    """Blends cycle-relative severity with the FC26 reference, weighting
    toward the cycle's OWN evolving distribution once enough observations
    exist - that's what actually generalises to a launch of a different
    magnitude than FC26's. Before enough history exists, the FC26
    reference carries most of the weight (there is nothing else to go
    on yet); this mirrors the broader FC26-prior-decay concept the
    runner applies at the strategy level, but scoped to just this one
    number.
    """
    if cycle_percentile is None or cycle_observation_count < config.min_cycle_observations_for_percentile:
        # Not enough of the cycle's own history yet - lean on the FC26
        # reference, but never blindly: still blend in whatever cycle
        # signal exists so an early, and thin, cycle observation isn't
        # entirely ignored.
        cycle_weight = 0.2 if cycle_percentile is not None else 0.0
    else:
        # Once there's enough of the cycle's own history, it dominates:
        # a launch whose magnitude looks nothing like FC26's can still
        # reach "severe"/"extreme" purely from its own worst-so-far
        # drawdown - the FC26 reference stays a light tiebreaker, not a
        # ceiling on how severe a genuinely different-shaped crash can
        # be classified.
        cycle_weight = 0.9
    ref_weight = 1.0 - cycle_weight

    score = ref_weight * historical_reference_percentile_value + cycle_weight * (cycle_percentile or 0.0)

    if score >= config.severity_percentile_extreme * 100:
        cls = SEVERITY_EXTREME
    elif score >= config.severity_percentile_severe * 100:
        cls = SEVERITY_SEVERE
    elif score >= config.severity_percentile_elevated * 100:
        cls = SEVERITY_ELEVATED
    else:
        cls = SEVERITY_NORMAL
    return round(score, 2), cls
