# app/services/investment_engine/momentum.py
"""
Decline-deceleration tracking.

Concretely: given a chronological chain of period-over-period returns,
is each recent return LESS NEGATIVE (or more positive) than the one
before it, for N consecutive periods? This is the FC26-validated
"decline decelerating for N consecutive days" family from Phase 8B/8C,
generalised from daily-only resolution to whatever rolling window the
caller passes in - FC27 has live intraday data, so the production runner
is expected to feed this short/medium/long windows (e.g. hourly,
4-hourly, daily-equivalent) rather than being restricted to one calendar
day per period the way the FC26 backtest necessarily is.

Deliberately NOT implementing (per FC26 research findings on what did
NOT work):
  * "first green day" as a standalone bottom signal - Phase 8B/8C found
    this specifically underperformed a still-declining baseline.
  * "recovered X% off a trailing low" - fired too early during the
    crash itself.
  * a bare "2 positive [return] periods" rule with no deceleration
    context - fired on Early-Access-style pump noise in the FC26 data.
Deceleration is about the SHAPE of the decline slowing, not a level or a
single green reading, and that distinction is why this module exists as
its own thing rather than a simple "is the latest return positive" check.

Pure module: no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class MomentumSnapshot:
    current_return_pct: float
    previous_return_pct: Optional[float]
    is_decelerating: bool
    deceleration_count: int
    deceleration_strength: Optional[float]  # how much the return chain improved, in pct points


def compute_momentum(
    return_history_pct: Sequence[float], current_return_pct: float, required_periods: int,
) -> MomentumSnapshot:
    """`return_history_pct` must be STRICTLY PRIOR period-over-period
    returns for this cohort (NO LOOKAHEAD) - the caller is responsible
    for that boundary. Deceleration counts the length of the trailing
    strictly-improving chain ending at current_return_pct; is_decelerating
    is true once that chain reaches `required_periods`.
    """
    chain = [*return_history_pct, current_return_pct]
    previous = return_history_pct[-1] if return_history_pct else None

    count = 0
    for i in range(len(chain) - 1, 0, -1):
        if chain[i] > chain[i - 1]:
            count += 1
        else:
            break

    strength = None
    if count > 0:
        strength = chain[-1] - chain[len(chain) - 1 - count]

    return MomentumSnapshot(
        current_return_pct=current_return_pct,
        previous_return_pct=previous,
        is_decelerating=count >= required_periods,
        deceleration_count=count,
        deceleration_strength=strength,
    )
