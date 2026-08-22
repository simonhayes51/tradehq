# backend/scripts/diagnose_price_quantization.py
"""
Is the incumbent-market study's sea of +0.0% real market flatness, or an
artifact of how fc26_historical_daily_prices was built?

research_fc26_chart_report.py's extract_series() derives price from a
LINEAR PIXEL-TO-PRICE FIT read off an SVG chart:
    price = round(a_y + b_y * cy)
For a chart whose Y-axis has to span a wide price range, the pixel-to-
coin resolution can be coarser than a cheap card's real day-to-day price
movement, so two genuinely different days round to the SAME integer
price - a flat "0% return" that reflects chart resolution, not the
market. This script checks whether that's actually happening, by price
tier, rather than assuming it.

Read-only.

    python -m scripts.diagnose_price_quantization --game-year 26
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
from collections import defaultdict
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("diagnose_price_quantization")

PRICE_BUCKETS = (
    ("<1,000", 0, 1000), ("1,000-5,000", 1000, 5000), ("5,000-20,000", 5000, 20000),
    ("20,000-100,000", 20000, 100000), ("100,000-500,000", 100000, 500000),
    ("500,000+", 500000, float("inf")),
)


def _bucket(median_price: float) -> str:
    for label, lo, hi in PRICE_BUCKETS:
        if lo <= median_price < hi:
            return label
    return "500,000+"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=26)
    parser.add_argument("--dsn", default=None)
    return parser.parse_args()


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
            rows = await conn.fetch(
                """
                SELECT h.card_id, h.observed_date, h.price
                FROM fc26_historical_daily_prices h
                JOIN futgg_players p ON p.source_card_id = h.card_id
                WHERE p.game_year = $1 AND p.rating >= 75 AND p.rarity IN ('Common', 'Rare')
                ORDER BY h.card_id, h.observed_date
                """,
                args.game_year,
            )
    finally:
        await pool.close()

    if not rows:
        log.error("no rows found")
        return 1

    by_card: Dict[int, List[tuple]] = defaultdict(list)
    for r in rows:
        by_card[r["card_id"]].append((r["observed_date"], r["price"]))

    print()
    print("=" * 100)
    print(f"PRICE QUANTIZATION DIAGNOSTIC - {len(by_card)} incumbent base-gold FC{args.game_year} cards")
    print("=" * 100)

    bucket_stats: Dict[str, dict] = {
        label: {"cards": 0, "consec_pairs": 0, "zero_pairs": 0, "nonzero_abs_deltas": [],
                "distinct_price_ratios": []}
        for label, _, _ in PRICE_BUCKETS
    }

    for card_id, points in by_card.items():
        prices = [p for _, p in points]
        median_price = statistics.median(prices)
        label = _bucket(median_price)
        b = bucket_stats[label]
        b["cards"] += 1
        distinct_ratio = len(set(prices)) / len(prices)
        b["distinct_price_ratios"].append(distinct_ratio)

        for i in range(1, len(points)):
            (d0, p0), (d1, p1) = points[i - 1], points[i]
            if (d1 - d0).days != 1:
                continue  # only true day-adjacent pairs - a gap isn't a "0% day"
            b["consec_pairs"] += 1
            delta = p1 - p0
            if delta == 0:
                b["zero_pairs"] += 1
            else:
                b["nonzero_abs_deltas"].append(abs(delta))

    print(f"\n{'price bucket':<18} {'cards':>7} {'consec_day_pairs':>17} {'zero_change_%':>14} "
          f"{'median|nonzero_delta|':>22} {'distinct/total_ratio':>21}")
    for label, _, _ in PRICE_BUCKETS:
        b = bucket_stats[label]
        if b["consec_pairs"] == 0:
            print(f"{label:<18} {b['cards']:>7} {'(no adjacent-day pairs)':>17}")
            continue
        zero_pct = b["zero_pairs"] / b["consec_pairs"] * 100.0
        med_delta = statistics.median(b["nonzero_abs_deltas"]) if b["nonzero_abs_deltas"] else float("nan")
        med_ratio = statistics.median(b["distinct_price_ratios"]) if b["distinct_price_ratios"] else float("nan")
        print(f"{label:<18} {b['cards']:>7} {b['consec_pairs']:>17} {zero_pct:>13.1f}% "
              f"{med_delta:>22.0f} {med_ratio:>21.2f}")

    print()
    print("Reading this:")
    print("  zero_change_% = share of literally-adjacent days where the stored price is IDENTICAL.")
    print("  A high rate for cheap cards alongside a low rate for expensive cards is the signature of")
    print("  chart pixel-resolution quantization (round(a_y + b_y*cy) can't represent small moves for")
    print("  a card whose whole price range spans only a few pixels), NOT a genuinely flatter market.")
    print("  distinct/total_ratio close to 1.0 means almost every stored day is a distinct price;")
    print("  close to 0 means the series is mostly repeats of a handful of values.")
    print("  median|nonzero_delta| that looks suspiciously round (multiples of 50/100/500) for a")
    print("  bucket is further evidence of pixel-grid rounding rather than real coin-level prices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
