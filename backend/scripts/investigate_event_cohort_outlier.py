# backend/scripts/investigate_event_cohort_outlier.py
"""
Forensic drill-down: show every card behind one (event, cohort, horizon)
cell in market_event_study.py's output, so a headline number like "+415%"
can be checked instead of trusted.

Read-only.

    python -m scripts.investigate_event_cohort_outlier \
        --game-year 26 --event-title "Summer Stars" --cohort premium_meta --offset 7

    python -m scripts.investigate_event_cohort_outlier \
        --game-year 26 --event-title "FUTTIES 1" --cohort premium_meta --offset -7 --offset 1 --offset 7
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
from datetime import timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("investigate_event_cohort_outlier")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=26)
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--event-title", required=True, help="Substring match against content_release_events.title")
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--offset", type=int, action="append", required=True,
                         help="Day offset(s) from release day, relative to T-1 baseline. Repeatable.")
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
            events = await conn.fetch(
                """
                SELECT id, title, event_type, released_at, card_count
                FROM content_release_events
                WHERE game_year = $1 AND title ILIKE $2
                ORDER BY released_at
                """,
                args.game_year, f"%{args.event_title}%",
            )
            if not events:
                log.error("no content_release_events matched title ILIKE '%%%s%%'", args.event_title)
                return 1
            if len(events) > 1:
                log.warning("multiple events matched - reporting all of them:")
                for e in events:
                    log.warning("  id=%s title=%r released_at=%s", e["id"], e["title"], e["released_at"])

            for ev in events:
                await _report_event(conn, ev, args.cohort, args.offset, args.game_year)
    finally:
        await pool.close()
    return 0


async def _report_event(conn, ev, cohort: str, offsets, game_year: int) -> None:
    print()
    print("=" * 100)
    print(f"{ev['title']}  [{ev['event_type']}]  released {ev['released_at']:%Y-%m-%d %H:%M %Z}  "
          f"card_count={ev['card_count']}")
    print("=" * 100)

    cards = await conn.fetch(
        """
        SELECT c.source_card_id, p.display_name, p.name, p.rarity, p.squad, p.released_at
        FROM content_release_event_cards c
        JOIN futgg_players p ON p.source_card_id = c.source_card_id
        WHERE c.event_id = $1
        """,
        ev["id"],
    )
    card_ids = [c["source_card_id"] for c in cards]
    if not card_ids:
        print("  no cards linked to this event in content_release_event_cards")
        return

    cohort_rows = await conn.fetch(
        """
        SELECT source_card_id FROM investment_cohort_memberships
        WHERE game_year = $1 AND cohort_key = $2 AND source_card_id = ANY($3::bigint[])
        """,
        game_year, cohort, card_ids,
    )
    in_cohort = {r["source_card_id"] for r in cohort_rows}
    print(f"  {len(in_cohort)}/{len(card_ids)} cards in this event belong to cohort '{cohort}'")
    if not in_cohort:
        return

    price_rows = await conn.fetch(
        """
        SELECT card_id, observed_date, price FROM fc26_historical_daily_prices
        WHERE card_id = ANY($1::bigint[])
        """,
        list(in_cohort),
    )
    series = {}
    for r in price_rows:
        series.setdefault(r["card_id"], {})[r["observed_date"]] = float(r["price"])

    release_day = ev["released_at"].date()
    baseline_day = release_day + timedelta(days=-1)
    cards_by_id = {c["source_card_id"]: c for c in cards}

    for offset in offsets:
        target_day = release_day + timedelta(days=offset)
        label = "T0" if offset == 0 else f"T{offset:+d}"
        print()
        print(f"  --- {label} ({target_day}) vs T-1 ({baseline_day}) ---")
        print(f"  {'card_id':>10} {'player':<28} {'rarity':<22} {'squad':<16} "
              f"{'released':<11} {'T-1':>8} {label:>8} {'return%':>9}")
        returns = []
        for cid in sorted(in_cohort):
            c = cards_by_id[cid]
            s = series.get(cid, {})
            base = s.get(baseline_day)
            target = s.get(target_day)
            name = c["display_name"] or c["name"] or "?"
            rel = c["released_at"].date().isoformat() if c["released_at"] else "?"
            pct = None
            if base and target and base > 0:
                pct = (target - base) / base * 100.0
                returns.append(pct)
            print(f"  {cid:>10} {name:<28.28} {(c['rarity'] or '?'):<22.22} {(c['squad'] or '?'):<16.16} "
                  f"{rel:<11} {('%8d' % base) if base else '     n/a'} "
                  f"{('%8d' % target) if target else '     n/a'} "
                  f"{('%+8.1f' % pct) if pct is not None else '      n/a'}")

        if len(returns) >= 3:
            print(f"  n={len(returns)}  median={statistics.median(returns):+.1f}%  "
                  f"mean={statistics.mean(returns):+.1f}%  min={min(returns):+.1f}%  max={max(returns):+.1f}%")
            if len(returns) <= 5:
                print("  WARNING: fewer than 6 paired cards - a single card can swing this median heavily.")
        else:
            print(f"  n={len(returns)} paired cards - BELOW market_event_study.py's MIN_PAIRED_CARDS floor, "
                  "this cell would render as INSUFFICIENT_COVERAGE/NO_BASELINE/NO_TARGET, never a number.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
