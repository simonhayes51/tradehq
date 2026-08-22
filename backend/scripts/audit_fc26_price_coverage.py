# backend/scripts/audit_fc26_price_coverage.py
"""
Read-only diagnostic: how much usable FC26 price history do we actually
have, where are the gaps, and can the raw source tables fill them in.

Never writes anything. Answers three questions market_event_study.py's
output cannot answer on its own:

  1. MONTHLY COVERAGE of fc26_historical_daily_prices (the chart-
     reconstructed series market_event_study.py reads) - row/date/card
     counts per calendar month, overall and broken down by the five FC26
     Investment Engine cohorts.

  2. EVENT-LEVEL COVERAGE - for every content_release_events row, how
     many cards in each cohort actually have a paired T-1/T+k price at
     each horizon, with a HIGH/MEDIUM/LOW/UNUSABLE quality label whose
     thresholds are printed and justified from the observed distribution,
     not chosen to flatter any particular event.

  3. RECOVERABILITY - coverage of the two RAW source tables
     (futgg_bin_history, futgg_sales_history - see auto_sync/
     futgg_price_sync.py / futgg_sales_sync.py) compared to
     fc26_historical_daily_prices, so we know whether the sparse months
     could be densified from real observations instead of chart
     reconstruction, without actually doing that rebuild here.

    python -m scripts.audit_fc26_price_coverage --game-year 26
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
from collections import defaultdict
from datetime import date
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("audit_fc26_price_coverage")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=26)
    parser.add_argument("--dsn", default=None)
    return parser.parse_args()


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _percentile(values: List[int], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return s[f] + (s[c] - s[f]) * (k - f)


async def _section_1_monthly_coverage(conn, game_year: int) -> None:
    print()
    print("=" * 88)
    print("1. MONTHLY COVERAGE - fc26_historical_daily_prices")
    print("   (source: auto_sync/research_fc26_historical_loader.py, confidence in "
          "{'exact','reconstructed'} -")
    print("   this is a chart-widget RECONSTRUCTION, not a raw observation feed - see section 3)")
    print("=" * 88)

    rows = await conn.fetch(
        """
        SELECT h.observed_date, h.card_id
        FROM fc26_historical_daily_prices h
        JOIN futgg_players p ON p.source_card_id = h.card_id
        WHERE p.game_year = $1
        """,
        game_year,
    )
    if not rows:
        print("  NO ROWS at all for this game_year - fc26_historical_daily_prices is empty "
              "or the game_year join found nothing.")
        return

    by_date: Dict[date, set] = defaultdict(set)
    for r in rows:
        by_date[r["observed_date"]].add(r["card_id"])

    all_dates = sorted(by_date)
    print(f"  overall: {len(rows)} rows, {len(all_dates)} distinct dates, "
          f"{len({cid for s in by_date.values() for cid in s})} distinct cards")
    print(f"  earliest observation: {all_dates[0]}   latest observation: {all_dates[-1]}")

    by_month: Dict[str, Dict[date, set]] = defaultdict(dict)
    for d, cards in by_date.items():
        by_month[_month_key(d)][d] = cards

    print()
    print(f"  {'month':<9} {'rows':>8} {'active_dates':>13} {'distinct_cards':>15} "
          f"{'cards/active_date':>18} {'p10':>6} {'p25':>6} {'median':>7} {'p75':>6} {'p90':>6}")
    for month in sorted(by_month):
        dates_in_month = by_month[month]
        active_dates = len(dates_in_month)
        rows_in_month = sum(len(c) for c in dates_in_month.values())
        cards_in_month = len({cid for c in dates_in_month.values() for cid in c})
        per_day_counts = [len(c) for c in dates_in_month.values()]
        p10 = _percentile(per_day_counts, 0.10)
        p25 = _percentile(per_day_counts, 0.25)
        p75 = _percentile(per_day_counts, 0.75)
        p90 = _percentile(per_day_counts, 0.90)
        med = statistics.median(per_day_counts)
        print(f"  {month:<9} {rows_in_month:>8} {active_dates:>13} {cards_in_month:>15} "
              f"{'':>18} {p10:>6.0f} {p25:>6.0f} {med:>7.0f} {p75:>6.0f} {p90:>6.0f}")

    print()
    print("  same breakdown BY COHORT (investment_cohort_memberships):")
    cohort_rows = await conn.fetch(
        "SELECT source_card_id, cohort_key FROM investment_cohort_memberships WHERE game_year = $1",
        game_year,
    )
    cohort_of: Dict[int, str] = {r["source_card_id"]: r["cohort_key"] for r in cohort_rows}
    if not cohort_of:
        print("    (no cohort memberships populated - skipping)")
        return

    by_month_cohort: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for r in rows:
        cohort = cohort_of.get(r["card_id"])
        if cohort is None:
            continue
        by_month_cohort[_month_key(r["observed_date"])][cohort].add(r["card_id"])

    for month in sorted(by_month_cohort):
        cells = ", ".join(
            f"{k}={len(v)}" for k, v in sorted(by_month_cohort[month].items())
        )
        print(f"    {month:<9} {cells}")


async def _section_3_event_level_coverage(conn, game_year: int) -> None:
    print()
    print("=" * 88)
    print("2. EVENT-LEVEL USABLE COVERAGE")
    print("   For each event/cohort: eligible cards, paired-n at each horizon, and a quality")
    print("   label. Thresholds below are derived from this dataset's own paired-n distribution")
    print("   (see the histogram printed first), not picked to make any event look better.")
    print("=" * 88)

    events = await conn.fetch(
        """
        SELECT id, title, event_type, released_at, card_count
        FROM content_release_events
        WHERE game_year = $1
        ORDER BY released_at
        """,
        game_year,
    )
    if not events:
        print("  No content_release_events rows for this game_year.")
        return

    event_card_rows = await conn.fetch(
        """
        SELECT event_id, source_card_id
        FROM content_release_event_cards
        WHERE event_id = ANY($1::bigint[])
        """,
        [e["id"] for e in events],
    )
    cards_by_event: Dict[int, List[int]] = defaultdict(list)
    for r in event_card_rows:
        cards_by_event[r["event_id"]].append(r["source_card_id"])

    cohort_rows = await conn.fetch(
        "SELECT source_card_id, cohort_key FROM investment_cohort_memberships WHERE game_year = $1",
        game_year,
    )
    cohort_of: Dict[int, str] = {r["source_card_id"]: r["cohort_key"] for r in cohort_rows}

    price_rows = await conn.fetch(
        """
        SELECT h.card_id, h.observed_date
        FROM fc26_historical_daily_prices h
        JOIN futgg_players p ON p.source_card_id = h.card_id
        WHERE p.game_year = $1
        """,
        game_year,
    )
    priced_dates_by_card: Dict[int, set] = defaultdict(set)
    for r in price_rows:
        priced_dates_by_card[r["card_id"]].add(r["observed_date"])

    from datetime import timedelta
    horizons = (-1, 0, 1, 2, 3, 5, 7)

    # First pass: collect every paired-n we would compute, purely to
    # derive thresholds from the ACTUAL distribution rather than guessing.
    all_paired_n: List[int] = []
    per_event_cohort_paired: Dict[tuple, Dict[int, int]] = {}
    for ev in events:
        card_ids = cards_by_event.get(ev["id"], [])
        by_cohort: Dict[str, List[int]] = defaultdict(list)
        for cid in card_ids:
            c = cohort_of.get(cid)
            if c:
                by_cohort[c].append(cid)
        release_day = ev["released_at"].date()
        baseline = release_day + timedelta(days=-1)
        for cohort, cids in by_cohort.items():
            n_t1 = sum(1 for cid in cids if baseline in priced_dates_by_card.get(cid, ()))
            paired = {}
            for h in horizons:
                target = release_day + timedelta(days=h)
                n = sum(
                    1 for cid in cids
                    if baseline in priced_dates_by_card.get(cid, ())
                    and target in priced_dates_by_card.get(cid, ())
                )
                paired[h] = n
                all_paired_n.append(n)
            per_event_cohort_paired[(ev["id"], cohort)] = paired

    if all_paired_n:
        print(f"  paired-n distribution across all (event, cohort, horizon) cells "
              f"(n={len(all_paired_n)}):")
        print(f"    p10={_percentile(all_paired_n,0.10):.0f}  p25={_percentile(all_paired_n,0.25):.0f}  "
              f"median={statistics.median(all_paired_n):.0f}  p75={_percentile(all_paired_n,0.75):.0f}  "
              f"p90={_percentile(all_paired_n,0.90):.0f}  max={max(all_paired_n)}")

    # Thresholds: UNUSABLE below MIN_PAIRED_CARDS (matches
    # market_event_study.py's own reportability floor - a quality label
    # above that floor would contradict the study script's own cutoff).
    # LOW/MEDIUM/HIGH split the remaining range at this dataset's own
    # median and p75 of paired-n, i.e. "better/worse than most cells we
    # actually have" rather than an arbitrary absolute count.
    from scripts.market_event_study import MIN_PAIRED_CARDS
    median_n = statistics.median(all_paired_n) if all_paired_n else 0
    p75_n = _percentile(all_paired_n, 0.75) if all_paired_n else 0
    print(f"  thresholds used below: UNUSABLE < {MIN_PAIRED_CARDS} (market_event_study.py's own "
          f"floor), LOW < {median_n:.0f} (this dataset's median), "
          f"MEDIUM < {p75_n:.0f} (this dataset's p75), HIGH >= {p75_n:.0f}")

    def _quality(n: int) -> str:
        if n < MIN_PAIRED_CARDS:
            return "UNUSABLE"
        if n < median_n:
            return "LOW"
        if n < p75_n:
            return "MEDIUM"
        return "HIGH"

    print()
    usable_events = 0
    for ev in events:
        by_cohort_lines = []
        best_quality_rank = -1
        rank = {"UNUSABLE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        for cohort in ("premium_meta", "mid_tier_meta", "desirable_starter", "ordinary", "likely_fodder"):
            paired = per_event_cohort_paired.get((ev["id"], cohort))
            if not paired:
                continue
            qualities = {h: _quality(n) for h, n in paired.items()}
            worst = min(qualities.values(), key=lambda q: rank[q])
            best_quality_rank = max(best_quality_rank, rank[worst])
            by_cohort_lines.append(
                f"      {cohort:<20} n(T-1..T+7)={[paired[h] for h in horizons]} -> {worst}"
            )
        if best_quality_rank >= 1:
            usable_events += 1
        print(f"  {ev['released_at']:%Y-%m-%d} {ev['title']}  [{ev['event_type']}]  "
              f"card_count={ev['card_count']}")
        for line in by_cohort_lines:
            print(line)

    print()
    print(f"  {usable_events}/{len(events)} events have at least one cohort above UNUSABLE "
          f"at at least one horizon.")


async def _section_5_recoverability(conn, game_year: int) -> None:
    print()
    print("=" * 88)
    print("3. RECOVERABILITY - raw source tables vs the reconstructed series")
    print("   futgg_bin_history (auto_sync/futgg_price_sync.py) - periodic BIN snapshots")
    print("   futgg_sales_history (auto_sync/futgg_price_sync.py + futgg_sales_sync.py) - "
          "individual sale events")
    print("=" * 88)

    for table, date_col in (("futgg_bin_history", "captured_at"), ("futgg_sales_history", "approximate_sold_at")):
        exists = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"public.{table}",
        )
        if not exists:
            print(f"  {table}: table does not exist on this database")
            continue
        row = await conn.fetchrow(
            f"""
            SELECT count(*) AS n, count(DISTINCT source_card_id) AS cards,
                   min({date_col}) AS earliest, max({date_col}) AS latest
            FROM {table} t
            JOIN futgg_players p ON p.source_card_id = t.source_card_id
            WHERE p.game_year = $1
            """,
            game_year,
        )
        print(f"  {table}: {row['n']} rows, {row['cards']} distinct FC{game_year} cards, "
              f"{row['earliest']} .. {row['latest']}")

    recon_row = await conn.fetchrow(
        """
        SELECT count(*) AS n, count(DISTINCT card_id) AS cards,
               min(observed_date) AS earliest, max(observed_date) AS latest
        FROM fc26_historical_daily_prices h
        JOIN futgg_players p ON p.source_card_id = h.card_id
        WHERE p.game_year = $1
        """,
        game_year,
    )
    print(f"  fc26_historical_daily_prices (reconstructed): {recon_row['n']} rows, "
          f"{recon_row['cards']} distinct cards, {recon_row['earliest']} .. {recon_row['latest']}")
    print()
    print("  Reading this: futgg_bin_history/futgg_sales_history only exist from whenever those")
    print("  workers were deployed onward - they cannot backfill dates before that regardless of")
    print("  density. If their earliest date is well AFTER fc26_historical_daily_prices's, the")
    print("  chart reconstruction is the ONLY source for the early season and raw data can only")
    print("  densify (not extend) periods the two overlap. This script deliberately stops at")
    print("  reporting that overlap - no rebuild is performed here.")


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
            await _section_1_monthly_coverage(conn, args.game_year)
            await _section_3_event_level_coverage(conn, args.game_year)
            await _section_5_recoverability(conn, args.game_year)
    finally:
        await pool.close()

    print()
    print("Audit complete - read-only, nothing was written or changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
