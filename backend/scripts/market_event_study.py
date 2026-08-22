# backend/scripts/market_event_study.py
"""
Event-relative price study: joins market_events (migration 064) to
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

For FC27 the exact timestamp is retained on market_events, so once
high-resolution live data exists the same events can support genuine
hourly/intraday studies. That is deliberately not attempted here.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("market_event_study")

# Day offsets relative to the release day. Deliberately asymmetric-free:
# the pre-window matters as much as the post-window, because a promo that
# was already priced in shows up as pre-release drift.
OFFSETS = (-7, -5, -3, -2, -1, 0, 1, 2, 3, 5, 7)


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
        FROM market_events
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


def _median_pct_change(
    series_by_card: Dict[int, Dict[date, float]], card_ids: List[int],
    baseline_day: date, target_day: date,
) -> Optional[float]:
    """Median per-card % change between two days. Per-card first, then
    median across cards - never a ratio of two cohort medians, which
    would let composition changes masquerade as price movement."""
    changes = []
    for cid in card_ids:
        series = series_by_card.get(cid)
        if not series:
            continue
        base = series.get(baseline_day)
        target = series.get(target_day)
        if base and target and base > 0:
            changes.append((target - base) / base * 100.0)
    if len(changes) < 3:
        return None
    return statistics.median(changes)


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
            "No events found for game_year=%d with >= %d cards. Has migration 064 been "
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
    print("=" * 78)
    print(f"FC{args.game_year} EVENT-RELATIVE PRICE STUDY  (association, NOT causation)")
    print("Daily price resolution - day 0 may fall either side of an evening release.")
    print("=" * 78)

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
                pct = _median_pct_change(
                    series_by_card, card_ids, baseline, release_day + timedelta(days=offset),
                )
                cells.append("    n/a" if pct is None else f"{pct:+6.1f}%")
            print(f"    {cohort_key:<20} " + " ".join(cells))

        if by_cohort:
            header = " ".join(f"{('T' + f'{o:+d}') if o else '  T0':>7}" for o in OFFSETS)
            print(f"    {'(vs T-1)':<20} " + header)

    print()
    print("Read as: 'around <event>, <cohort> moved X%' - never 'caused'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
