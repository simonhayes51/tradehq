# backend/scripts/market_event_study.py
"""
Event-relative price study: joins content_release_events (migration 064) to
fc26_historical_daily_prices and reports how cohorts moved around each
content release.

    python -m scripts.market_event_study --game-year 26
    python -m scripts.market_event_study --game-year 26 --min-cards 6

WORDING DISCIPLINE - read before using the output anywhere
----------------------------------------------------------
This measures ASSOCIATION, never causation. The correct phrasing is
"around the Cornerstones 1 release, premium golds moved -4.2%", never
"Cornerstones caused premium golds to fall 4.2%". Promos overlap with
each other, with SBC cycles, with weekend league, and with ordinary
market drift; nothing in this join isolates a cause.

RESOLUTION LIMIT - FC26 prices are DAILY
----------------------------------------
Release timestamps are exact to the minute (e.g. 18:00), but
fc26_historical_daily_prices has one observation per card per day. So a
release at 18:00 and the same day's price cannot be ordered within that
day. This script therefore reports DAY-relative offsets only and never
fabricates an intraday reaction. Day 0 is the calendar day containing the
release timestamp, and its price may reflect either side of an evening
drop - which is exactly why day 0 should be read with caution and the
T-1 -> T+1 transition is the more meaningful comparison.

For FC27 the exact timestamp is retained on content_release_events, so once
high-resolution live data exists the same events can support genuine
hourly/intraday studies. That is deliberately not attempted here.

ZERO VS MISSING - read before trusting a "0.0%" anywhere in the output
------------------------------------------------------------------
fc26_historical_daily_prices ("Phase 5/8" chart-reconstructed data - see
auto_sync/research_fc26_historical_loader.py) is NOT uniformly dense
across the season: each card's row count depends on how much trailing
history FUT.GG's own price chart widget happened to render when it was
captured. A cell with too few paired cards to trust a median is NOT the
same thing as a cell where cards genuinely didn't move, so every cell is
classified explicitly (see CellStatus below) instead of collapsing
"missing data" and "flat price" into the same "+0.0%" text. A bare
"+0.0%" in this script's output ALWAYS means a real, sufficiently-paired
median of zero - never a stand-in for "no data".
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("market_event_study")

# Day offsets relative to the release day. Deliberately asymmetric-free:
# the pre-window matters as much as the post-window, because a promo that
# was already priced in shows up as pre-release drift.
OFFSETS = (-7, -5, -3, -2, -1, 0, 1, 2, 3, 5, 7)

# Below this many paired (both baseline AND target priced) cards, a
# median is not reportable - matches the threshold this script has always
# used, now named and classified instead of silently returning None.
MIN_PAIRED_CARDS = 3

# A median inside this band is treated as a real, reported flat price
# (VALID_ZERO) rather than float noise from a near-zero divide.
ZERO_EPSILON_PCT = 0.05


class CellStatus(str, Enum):
    VALID_ZERO = "VALID_ZERO"                  # enough paired cards, median is genuinely ~0%
    VALID_MOVE = "VALID_MOVE"                   # enough paired cards, median is non-zero
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"  # some paired cards exist, just not enough
    NO_BASELINE = "NO_BASELINE"                 # zero cards have a T-1 price at all
    NO_TARGET = "NO_TARGET"                     # cards have a T-1 price, none have this day's


@dataclass(frozen=True)
class CellResult:
    status: CellStatus
    median_pct: Optional[float]
    n_eligible: int          # cards in this cohort at all
    n_with_baseline: int     # of those, how many have a T-1 price
    n_with_target: int       # of those, how many have a price on this offset's day
    n_paired: int            # how many have BOTH (this is what feeds the median)

    def render(self) -> str:
        if self.status in (CellStatus.VALID_ZERO, CellStatus.VALID_MOVE):
            return f"{self.median_pct:+6.1f}%"
        if self.status is CellStatus.NO_BASELINE:
            return " no_T-1"
        if self.status is CellStatus.NO_TARGET:
            return " no_day"
        return f"thin,n={self.n_paired}"


def evaluate_cell(
    series_by_card: Dict[int, Dict[date, float]], card_ids: List[int],
    baseline_day: date, target_day: date, *, min_paired: int = MIN_PAIRED_CARDS,
) -> CellResult:
    """Classify one (event, cohort, day-offset) cell instead of collapsing
    "not enough data" and "genuinely flat" into the same number. Per-card
    % change first, then median across cards - never a ratio of two
    cohort medians, which would let composition changes masquerade as
    price movement."""
    n_eligible = len(card_ids)
    changes: List[float] = []
    n_with_baseline = 0
    n_with_target = 0
    for cid in card_ids:
        series = series_by_card.get(cid)
        if not series:
            continue
        base = series.get(baseline_day)
        target = series.get(target_day)
        if base is not None:
            n_with_baseline += 1
        if target is not None:
            n_with_target += 1
        if base and target and base > 0:
            changes.append((target - base) / base * 100.0)

    n_paired = len(changes)
    if n_paired >= min_paired:
        median = statistics.median(changes)
        status = CellStatus.VALID_ZERO if abs(median) < ZERO_EPSILON_PCT else CellStatus.VALID_MOVE
        return CellResult(status, median, n_eligible, n_with_baseline, n_with_target, n_paired)

    if n_with_baseline == 0:
        status = CellStatus.NO_BASELINE
    elif n_with_target == 0:
        status = CellStatus.NO_TARGET
    else:
        status = CellStatus.INSUFFICIENT_COVERAGE
    return CellResult(status, None, n_eligible, n_with_baseline, n_with_target, n_paired)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=26)
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--min-cards", type=int, default=6,
                         help="Only study events with at least this many cards (default 6 = 'broad')")
    parser.add_argument("--limit", type=int, default=25, help="Max events to report")
    return parser.parse_args()


async def _load(conn, game_year: int, min_cards: int, limit: int):
    events = await conn.fetch(
        """
        SELECT id, event_type, event_key, title, rarity, squad, released_at,
               card_count, event_scale, confidence
        FROM content_release_events
        WHERE game_year = $1 AND card_count >= $2
        ORDER BY released_at
        LIMIT $3
        """,
        game_year, min_cards, limit,
    )

    # Cohort membership reuses the Investment Engine's OWN classifier
    # rather than a second definition of "premium gold", so an event study
    # and an engine decision always mean the same thing by "premium_meta".
    cohorts = await conn.fetch(
        """
        SELECT source_card_id, cohort_key
        FROM investment_cohort_memberships
        WHERE game_year = $1
        """,
        game_year,
    )
    prices = await conn.fetch(
        "SELECT card_id, observed_date, price FROM fc26_historical_daily_prices"
    )
    return events, cohorts, prices


async def main() -> int:
    args = _parse_args()
    dsn = args.dsn or os.getenv("PLAYER_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        log.error("need --dsn or PLAYER_DATABASE_URL/DATABASE_URL")
        return 1

    import asyncpg
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            events, cohort_rows, price_rows = await _load(
                conn, args.game_year, args.min_cards, args.limit,
            )
    finally:
        await pool.close()

    if not events:
        log.warning(
            "No events found for game_year=%d with >= %d cards. Have migrations 064/066 been "
            "applied and the reconstruction run?", args.game_year, args.min_cards,
        )
        return 0

    series_by_card: Dict[int, Dict[date, float]] = {}
    for r in price_rows:
        series_by_card.setdefault(r["card_id"], {})[r["observed_date"]] = float(r["price"])

    by_cohort: Dict[str, List[int]] = {}
    for r in cohort_rows:
        by_cohort.setdefault(r["cohort_key"], []).append(r["source_card_id"])

    if not by_cohort:
        log.warning(
            "No investment_cohort_memberships for game_year=%d - cohort breakdown will be "
            "skipped. Run the Investment Engine's cohort classification first.", args.game_year,
        )

    print()
    print("=" * 88)
    print(f"FC{args.game_year} EVENT-RELATIVE PRICE STUDY  (association, NOT causation)")
    print("Daily price resolution - day 0 may fall either side of an evening release.")
    print("Cells: +N.N% = VALID_ZERO/VALID_MOVE (real median). no_T-1/no_day/thin,n=k = NOT a 0%.")
    print("=" * 88)

    status_totals: Dict[CellStatus, int] = {s: 0 for s in CellStatus}

    for ev in events:
        release_day = ev["released_at"].date()
        print()
        print(f"{ev['title']}  [{ev['event_type']}]")
        print(f"  released {ev['released_at']:%Y-%m-%d %H:%M %Z}  "
              f"cards={ev['card_count']}  scale={ev['event_scale']}  confidence={ev['confidence']}")

        for cohort_key, card_ids in sorted(by_cohort.items()):
            baseline = release_day + timedelta(days=-1)
            cells = []
            for offset in OFFSETS:
                result = evaluate_cell(
                    series_by_card, card_ids, baseline, release_day + timedelta(days=offset),
                )
                status_totals[result.status] += 1
                cells.append(f"{result.render():>9}")
            print(f"    {cohort_key:<20} " + " ".join(cells))

        if by_cohort:
            header = " ".join(f"{('T' + f'{o:+d}') if o else '  T0':>9}" for o in OFFSETS)
            print(f"    {'(vs T-1)':<20} " + header)

    total_cells = sum(status_totals.values())
    print()
    print("-" * 88)
    print(f"cell coverage over {total_cells} (event, cohort, offset) cells:")
    for status in CellStatus:
        n = status_totals[status]
        pct = (n / total_cells * 100.0) if total_cells else 0.0
        print(f"    {status.value:<22} {n:>6}  ({pct:5.1f}%)")
    print("-" * 88)
    print("Read as: 'around <event>, <cohort> moved X%' - never 'caused'.")
    print("A large NO_BASELINE/NO_TARGET/INSUFFICIENT_COVERAGE share means most of this table")
    print("is a coverage gap, not evidence of no movement - see scripts/audit_fc26_price_coverage.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
