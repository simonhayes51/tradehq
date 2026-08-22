# backend/app/services/incumbent_market_study.py
"""
Pure core for the incumbent-gold-market event study (research only, never
touched by the Opportunity or Investment Engine).

FRAMING - read before touching anything below
-----------------------------------------------
content_release_events is the EVENT CALENDAR. fc26_historical_daily_prices'
~2600 base Gold Common/Gold Rare incumbents are the MARKET BEING MEASURED.
A promo card is never required to have a price of its own here - the
question is how the EXISTING gold market reacted around a content drop,
not how the drop's own cards behaved (that question hit a hard data wall,
see the "0/98 events usable" incumbent-restricted result this replaces).

NO LOOKAHEAD - the rule every function here is built to satisfy
------------------------------------------------------------------
rating / rarity / primary_position / pace are static card attributes:
using them is never lookahead, they don't change after a card is minted.
launch_cohort and price_band are event-RELATIVE: both are recomputed PER
EVENT from that event's own T-1 price, via assign_asof_launch_cohorts()/
assign_price_band(). Neither ever reads a global "current" classification
- a December event must never be cohorted using an August price, and
isn't, because the T-1 price used is December's own.

ONE EVENT = ONE OBSERVATION when aggregating across events
------------------------------------------------------------
Every stat starts as a per-card return, medianed within ONE event first
(leg_stats()). aggregate_event_level() then medians THOSE per-event
medians across events - it never re-pools raw card returns from
different events into one bucket, which would let the best-covered or
biggest event dominate and manufacture false statistical confidence from
what might really be n=4 independent promos.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from app.services.investment_engine.cohorts import (
    ALL_COHORTS, CohortInput, classify_cohorts,
)
from app.services.investment_engine.config import InvestmentEngineConfig

EA_TAX_RATE = 0.05
MIN_PAIRED_CARDS = 3

# Core T-1-anchored legs the study reports everywhere.
BASELINE_LEGS: Tuple[Tuple[str, int, int], ...] = (
    ("T-7->T-1", -7, -1), ("T-5->T-1", -5, -1), ("T-3->T-1", -3, -1), ("T-2->T-1", -2, -1),
    ("T-1->T0", -1, 0), ("T-1->T+1", -1, 1), ("T-1->T+2", -1, 2), ("T-1->T+3", -1, 3),
    ("T-1->T+5", -1, 5), ("T-1->T+7", -1, 7),
)

# Entry/exit matrix for "buy Thursday vs buy after content" (hypothesis C):
# every (entry, exit) pair with exit strictly after entry, entries at
# T-1/T0/T+1/T+2, exits at 1/2/3/5/7 days after that entry.
ENTRY_OFFSETS: Tuple[int, ...] = (-1, 0, 1, 2)
EXIT_HORIZONS: Tuple[int, ...] = (1, 2, 3, 5, 7)


def _fmt_t(offset: int) -> str:
    return "T0" if offset == 0 else f"T{offset:+d}"


def entry_exit_legs() -> Tuple[Tuple[str, int, int], ...]:
    legs = []
    for entry in ENTRY_OFFSETS:
        for horizon in EXIT_HORIZONS:
            exit_offset = entry + horizon
            legs.append((f"{_fmt_t(entry)}->{_fmt_t(exit_offset)}", entry, exit_offset))
    return tuple(legs)


ENTRY_EXIT_LEGS = entry_exit_legs()

SEASON_REGIMES: Tuple[Tuple[str, date, date], ...] = (
    ("Launch (Sep-Oct 2025)", date(2025, 9, 1), date(2025, 10, 31)),
    ("Early season (Nov-Dec 2025)", date(2025, 11, 1), date(2025, 12, 31)),
    ("Midseason (Jan-Mar 2026)", date(2026, 1, 1), date(2026, 3, 31)),
    ("Spring content (Apr-May 2026)", date(2026, 4, 1), date(2026, 5, 31)),
    ("Summer/FUTTIES (Jun-Aug 2026)", date(2026, 6, 1), date(2026, 8, 31)),
)

# Two events closer together than this are treated as one cluster, not
# independent experiments - their -7..+7 study windows overlap (e.g.
# FUTTIES releases are exactly 7 days apart, so T+7 of one IS the
# release day of the next).
INDEPENDENCE_GAP_DAYS = 8


def season_regime(d: date) -> str:
    for label, start, end in SEASON_REGIMES:
        if start <= d <= end:
            return label
    return "Other"


def weekday_bucket(d: date) -> str:
    name = d.strftime("%A")
    return name if name in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday") else "Weekend"


def flag_independent_events(events: Sequence[dict]) -> Dict[int, bool]:
    """events: [{'id':.., 'released_at': datetime}, ...]. False when this
    event's release is within INDEPENDENCE_GAP_DAYS of the PREVIOUS
    event's release (any type) - i.e. their study windows overlap."""
    ordered = sorted(events, key=lambda e: e["released_at"])
    result: Dict[int, bool] = {}
    prev_release = None
    for ev in ordered:
        release = ev["released_at"]
        if prev_release is not None and (release - prev_release).days < INDEPENDENCE_GAP_DAYS:
            result[ev["id"]] = False
        else:
            result[ev["id"]] = True
        prev_release = release
    return result


def card_return(series: Dict[date, float], baseline_day: date, target_day: date) -> Optional[float]:
    base = series.get(baseline_day)
    target = series.get(target_day)
    if base and target and base > 0:
        return (target - base) / base * 100.0
    return None


def _percentile(values: List[float], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


@dataclass(frozen=True)
class LegStats:
    leg: str
    n_eligible: int
    n_paired: int
    median: Optional[float] = None
    mean: Optional[float] = None
    p10: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None
    positive_rate: Optional[float] = None
    tax_adjusted_median: Optional[float] = None

    @property
    def is_reportable(self) -> bool:
        return self.n_paired >= MIN_PAIRED_CARDS

    def as_dict(self) -> dict:
        return {
            "leg": self.leg, "n_eligible": self.n_eligible, "n_paired": self.n_paired,
            "median": self.median, "mean": self.mean, "p10": self.p10, "p25": self.p25,
            "p75": self.p75, "p90": self.p90, "positive_rate": self.positive_rate,
            "tax_adjusted_median": self.tax_adjusted_median,
        }


def leg_stats(
    leg_name: str, series_by_card: Dict[int, Dict[date, float]], card_ids: Sequence[int],
    baseline_day: date, target_day: date,
) -> LegStats:
    n_eligible = len(card_ids)
    changes: List[float] = []
    for cid in card_ids:
        series = series_by_card.get(cid)
        if not series:
            continue
        r = card_return(series, baseline_day, target_day)
        if r is not None:
            changes.append(r)
    n_paired = len(changes)
    if n_paired < MIN_PAIRED_CARDS:
        return LegStats(leg_name, n_eligible, n_paired)
    return LegStats(
        leg_name, n_eligible, n_paired,
        median=statistics.median(changes), mean=statistics.mean(changes),
        p10=_percentile(changes, 0.10), p25=_percentile(changes, 0.25),
        p75=_percentile(changes, 0.75), p90=_percentile(changes, 0.90),
        positive_rate=sum(1 for c in changes if c > 0) / n_paired * 100.0,
        tax_adjusted_median=statistics.median(changes) - EA_TAX_RATE * 100.0,
    )


def legs_for_event(
    legs: Sequence[Tuple[str, int, int]], series_by_card: Dict[int, Dict[date, float]],
    card_ids: Sequence[int], release_day: date,
) -> Dict[str, LegStats]:
    out = {}
    for leg_name, from_off, to_off in legs:
        baseline = release_day + timedelta(days=from_off)
        target = release_day + timedelta(days=to_off)
        out[leg_name] = leg_stats(leg_name, series_by_card, card_ids, baseline, target)
    return out


def per_label_legs(
    legs: Sequence[Tuple[str, int, int]], series_by_card: Dict[int, Dict[date, float]],
    cid_to_label: Dict[int, str], release_day: date,
) -> Dict[str, Dict[str, LegStats]]:
    by_label: Dict[str, List[int]] = defaultdict(list)
    for cid, label in cid_to_label.items():
        by_label[label].append(cid)
    return {label: legs_for_event(legs, series_by_card, cids, release_day) for label, cids in by_label.items()}


def assign_asof_launch_cohorts(
    card_ids: Sequence[int], series_by_card: Dict[int, Dict[date, float]],
    positions_by_card: Dict[int, Optional[str]], baseline_day: date,
    config: InvestmentEngineConfig,
) -> Dict[int, str]:
    """AS-OF cohort label for one event, keyed by card_id - built ONLY
    from that event's own T-1 price via the Investment Engine's own
    unmodified classify_cohorts(), never a global/current classification.
    A card with no T-1 price is omitted (unmeasurable for this event
    regardless of cohort)."""
    inputs = []
    for cid in card_ids:
        series = series_by_card.get(cid)
        price = series.get(baseline_day) if series else None
        if not price or price <= 0:
            continue
        inputs.append(CohortInput(
            card_id=cid, reference_price=float(price),
            primary_position=positions_by_card.get(cid) or "CM",
        ))
    assignments = classify_cohorts(inputs, config)
    return {a.card_id: a.cohort_key for a in assignments}


def assign_price_band(
    card_ids: Sequence[int], series_by_card: Dict[int, Dict[date, float]], baseline_day: date,
) -> Dict[int, str]:
    """AS-OF price quintile label, keyed by card_id, computed from prices
    observed on THIS event's own T-1 day only."""
    priced = [
        (cid, series_by_card[cid][baseline_day]) for cid in card_ids
        if series_by_card.get(cid) and series_by_card[cid].get(baseline_day)
    ]
    if len(priced) < 5:
        return {}
    prices = sorted(p for _, p in priced)
    bounds = [_percentile(prices, q) for q in (0.2, 0.4, 0.6, 0.8)]
    labels = ["Q1_cheapest", "Q2", "Q3", "Q4", "Q5_priciest"]
    out: Dict[int, str] = {}
    for cid, price in priced:
        idx = 0
        for i, b in enumerate(bounds):
            if price > b:
                idx = i + 1
        out[cid] = labels[idx]
    return out


def rating_band(rating: Optional[int]) -> str:
    if rating is None:
        return "unknown"
    if rating < 78:
        return "75-77"
    if rating < 81:
        return "78-80"
    if rating < 84:
        return "81-83"
    if rating < 87:
        return "84-86"
    return "87+"


def pace_band(pace: Optional[int]) -> str:
    if pace is None:
        return "unknown"
    if pace < 60:
        return "<60"
    if pace < 70:
        return "60-69"
    if pace < 80:
        return "70-79"
    if pace < 90:
        return "80-89"
    return "90+"


def aggregate_event_level(per_event_legs: Sequence[Dict[str, LegStats]], legs: Sequence[Tuple[str, int, int]]) -> Dict[str, dict]:
    """ONE EVENT = ONE OBSERVATION - see module docstring."""
    out = {}
    for leg_name, _, _ in legs:
        event_medians = [
            stats[leg_name].median for stats in per_event_legs
            if leg_name in stats and stats[leg_name].is_reportable
        ]
        if len(event_medians) < 2:
            out[leg_name] = {
                "n_events": len(event_medians), "median_of_medians": None,
                "mean_of_medians": None, "win_rate_events_pct": None,
            }
            continue
        out[leg_name] = {
            "n_events": len(event_medians),
            "median_of_medians": statistics.median(event_medians),
            "mean_of_medians": statistics.mean(event_medians),
            "win_rate_events_pct": sum(1 for m in event_medians if m > 0) / len(event_medians) * 100.0,
        }
    return out
