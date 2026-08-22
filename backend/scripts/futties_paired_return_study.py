# backend/scripts/futties_paired_return_study.py
"""
Paired, event-level FUTTIES study for the premium_meta cohort.

Reuses market_event_study.py's evaluate_cell() so a cell here is
classified exactly the same way (VALID_ZERO/VALID_MOVE/INSUFFICIENT_
COVERAGE/NO_BASELINE/NO_TARGET) as the main study - never a second
definition of "enough data".

Two levels are reported, and they are NOT interchangeable:
  - per-event: n paired cards, median/mean/p10/p25/win-rate for THAT one
    FUTTIES event.
  - aggregate: FUTTIES 1-4 combined so ONE EVENT counts as ONE
    OBSERVATION (the per-event medians above are themselves averaged),
    rather than pooling every card from every event into one big bucket -
    which would let the event with the most cards or best price coverage
    dominate and manufacture false statistical confidence from what is
    really n=4.

    python -m scripts.futties_paired_return_study --game-year 26
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
from datetime import timedelta
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("futties_paired_return_study")

# T-1 -> T+k transitions plus the pre-release drift legs, matching what
# the user asked to see: T-7/-5/-3/-2 into T-1, then T-1 into T0..T+7.
LEGS = (
    ("T-7 -> T-1", -7, -1), ("T-5 -> T-1", -5, -1), ("T-3 -> T-1", -3, -1), ("T-2 -> T-1", -2, -1),
    ("T-1 -> T0", -1, 0), ("T-1 -> T+1", -1, 1), ("T-1 -> T+2", -1, 2), ("T-1 -> T+3", -1, 3),
    ("T-1 -> T+5", -1, 5), ("T-1 -> T+7", -1, 7),
)

EA_TAX = 0.05


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=26)
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--cohort", default="premium_meta")
    parser.add_argument("--event-title-like", default="FUTTIES",
                         help="Substring match against content_release_events.title")
    return parser.parse_args()


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


async def main() -> int:
    args = _parse_args()
    dsn = args.dsn or os.getenv("PLAYER_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        log.error("need --dsn or PLAYER_DATABASE_URL/DATABASE_URL")
        return 1

    import asyncpg
    from scripts.market_event_study import evaluate_cell, CellStatus

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            events = await conn.fetch(
                """
                SELECT id, title, released_at FROM content_release_events
                WHERE game_year = $1 AND title ILIKE $2
                ORDER BY released_at
                """,
                args.game_year, f"%{args.event_title_like}%",
            )
            if not events:
                log.warning("no events matched title ILIKE '%%%s%%'", args.event_title_like)
                return 0

            cohort_rows = await conn.fetch(
                "SELECT source_card_id FROM investment_cohort_memberships WHERE game_year = $1 AND cohort_key = $2",
                args.game_year, args.cohort,
            )
            cohort_card_ids = {r["source_card_id"] for r in cohort_rows}

            event_cards = await conn.fetch(
                """
                SELECT event_id, source_card_id FROM content_release_event_cards
                WHERE event_id = ANY($1::bigint[])
                """,
                [e["id"] for e in events],
            )
            cards_by_event: Dict[int, List[int]] = {}
            for r in event_cards:
                cards_by_event.setdefault(r["event_id"], []).append(r["source_card_id"])

            price_rows = await conn.fetch(
                """
                SELECT card_id, observed_date, price FROM fc26_historical_daily_prices
                WHERE card_id = ANY($1::bigint[])
                """,
                list(cohort_card_ids) or [-1],
            )
    finally:
        await pool.close()

    series_by_card: Dict[int, Dict] = {}
    for r in price_rows:
        series_by_card.setdefault(r["card_id"], {})[r["observed_date"]] = float(r["price"])

    print()
    print("=" * 100)
    print(f"FUTTIES paired-return study - cohort={args.cohort}, {len(events)} event(s) matched")
    print("=" * 100)

    event_level_medians: Dict[str, List[float]] = {leg[0]: [] for leg in LEGS}

    for ev in events:
        card_ids = [cid for cid in cards_by_event.get(ev["id"], []) if cid in cohort_card_ids]
        release_day = ev["released_at"].date()
        print()
        print(f"{ev['title']}  released {ev['released_at']:%Y-%m-%d %H:%M %Z}  "
              f"{args.cohort} cards eligible={len(card_ids)}")

        for leg_name, from_off, to_off in LEGS:
            from_day = release_day + timedelta(days=from_off)
            to_day = release_day + timedelta(days=to_off)
            result = evaluate_cell(series_by_card, card_ids, from_day, to_day)

            changes = []
            for cid in card_ids:
                s = series_by_card.get(cid)
                if not s:
                    continue
                base = s.get(from_day)
                target = s.get(to_day)
                if base and target and base > 0:
                    changes.append((target - base) / base * 100.0)

            if result.status in (CellStatus.VALID_ZERO, CellStatus.VALID_MOVE):
                event_level_medians[leg_name].append(result.median_pct)
                wins = sum(1 for c in changes if c > 0)
                win_rate = wins / len(changes) * 100.0
                net_of_tax = statistics.median(changes) - EA_TAX * 100.0 if to_off > from_off else None
                tax_note = f"  tax-adj={net_of_tax:+.1f}%" if net_of_tax is not None else ""
                print(f"    {leg_name:<12} n={len(changes):<3} coverage={result.n_paired}/{result.n_eligible} "
                      f"median={statistics.median(changes):+6.1f}% mean={statistics.mean(changes):+6.1f}% "
                      f"p25={_percentile(changes,0.25):+6.1f}% p10={_percentile(changes,0.10):+6.1f}% "
                      f"win_rate={win_rate:5.1f}%{tax_note}")
            else:
                print(f"    {leg_name:<12} {result.status.value}  (n_paired={result.n_paired}, "
                      f"n_eligible={result.n_eligible}) - NOT a real 0%, not reported")

    print()
    print("=" * 100)
    print(f"AGGREGATE across {len(events)} FUTTIES event(s) - ONE EVENT = ONE OBSERVATION")
    print("(medians-of-medians below, not a pool of every card from every event)")
    print("=" * 100)
    for leg_name, _, _ in LEGS:
        vals = event_level_medians[leg_name]
        if len(vals) < 2:
            print(f"  {leg_name:<12} only {len(vals)} event(s) had a reportable median - "
                  f"too few events to aggregate")
            continue
        print(f"  {leg_name:<12} n_events={len(vals)} median_of_event_medians={statistics.median(vals):+.1f}% "
              f"mean={statistics.mean(vals):+.1f}%  per-event: "
              + ", ".join(f"{v:+.1f}%" for v in vals))

    print()
    print("Read as association across n={} FUTTIES events, never causation.".format(len(events)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
