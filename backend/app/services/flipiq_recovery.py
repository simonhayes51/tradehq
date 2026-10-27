"""Empirical BIN-price recovery analysis, with no fabricated win probabilities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence, Tuple


def recovery_evidence(
    observations: Sequence[Tuple[int, datetime]],
    *, drop_pct: float = 16.0, lookback_hours: int = 6,
    recovery_hours: int = 9, now: datetime | None = None,
) -> dict:
    """Count *completed*, nonoverlapping historical drop/recovery episodes.

    Samples at most one BIN observation per hour, avoiding false repeated
    'trades' from the five-minute price polling cadence. A successful
    recovery means a later observed BIN reached >=95% of the pre-drop
    reference within nine hours. Asking BIN is not a realized sale price.
    """
    now = now or datetime.now(timezone.utc)
    by_hour: dict[int, tuple[datetime, float]] = {}
    for value, at in observations:
        if not isinstance(at, datetime) or value is None or value <= 0:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at > now:
            continue
        hour = int(at.timestamp() // 3600)
        if hour not in by_hour or at > by_hour[hour][0]:
            by_hour[hour] = (at, float(value))
    seq = [by_hour[k] for k in sorted(by_hour)]
    recovered = total = 0
    recovery_times: list[float] = []
    i = lookback_hours
    while i < len(seq):
        current_at, current = seq[i]
        if current_at + timedelta(hours=recovery_hours) > now:
            break  # right-censored: cannot claim a failure
        reference_at, reference = seq[i - lookback_hours]
        if abs((current_at - reference_at).total_seconds() / 3600 - lookback_hours) > 2:
            i += 1
            continue
        if current > reference * (1 - drop_pct / 100):
            i += 1
            continue
        future = [(at, price) for at, price in seq[i + 1:]
                  if at <= current_at + timedelta(hours=recovery_hours)]
        # Need an observation near the end of the recovery window or
        # the outcome is unobserved (not a loss).
        if not future or future[-1][0] < current_at + timedelta(hours=recovery_hours - 2):
            i += 1
            continue
        total += 1
        hit = next((at for at, price in future if price >= reference * .95), None)
        if hit is not None:
            recovered += 1
            recovery_times.append((hit - current_at).total_seconds() / 3600)
        i += recovery_hours  # episodes must not overlap
    return {
        "sample_size": total,
        "recovered": recovered,
        "recovery_rate": round(recovered / total * 100, 1) if total >= 3 else None,
        "median_recovery_hours": round(sorted(recovery_times)[len(recovery_times)//2], 1) if recovery_times else None,
        "definition": "BIN returned to >=95% of the pre-drop price within 9h; historical asking prices, not confirmed resale",
    }
