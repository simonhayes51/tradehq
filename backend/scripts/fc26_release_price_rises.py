# backend/scripts/fc26_release_price_rises.py
"""
Read-only CLI: rank individual FC26 cards by price change from early
release to full release, and print the same position/pace-tier
breakdown the /api/v2/release-price-rises endpoint serves to the
frontend (app/routers/v2/release_price_rises.py) - same query logic,
run directly against the database for a quick terminal check without
starting the API.

Usage:
    python -m scripts.fc26_release_price_rises --dsn "$DATABASE_URL"
    python -m scripts.fc26_release_price_rises --game-year 26 --top 25
    python -m scripts.fc26_release_price_rises --early-date 2025-09-26 --full-date 2025-10-03
    python -m scripts.fc26_release_price_rises --position ST --top 15

For --game-year 26, dates default to FC26's confirmed launch calendar
(19 Sep 2025 early release / 26 Sep 2025 full release) unless
EAFC_WEB_APP_LAUNCH_DATE/EAFC_FULL_RELEASE_DATE are set, in which case
those win. For any other game year, dates come from those env vars only.
--early-date/--full-date always override both.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("fc26_release_price_rises")

MATCH_WINDOW_DAYS = 5
PACE_HIGH_THRESHOLD = 85
HIGH_CALIBRATION_ERR_PCT = 20.0

# FC26's confirmed launch calendar - see release_price_rises.py's module
# docstring for why this is hardcoded rather than env-var-only: FC26
# already happened, so these are a historical fact, not a per-deployment
# config value still waiting on an EA announcement.
FC26_WEB_APP_LAUNCH_DATE = date(2025, 9, 17)
FC26_FULL_RELEASE_DATE = date(2025, 9, 26)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=None, help="Defaults to $DATABASE_URL / $PLAYER_DATABASE_URL")
    parser.add_argument("--game-year", type=int, default=26)
    parser.add_argument("--early-date", default=None, help="YYYY-MM-DD, defaults to EAFC_WEB_APP_LAUNCH_DATE")
    parser.add_argument("--full-date", default=None, help="YYYY-MM-DD, defaults to EAFC_FULL_RELEASE_DATE")
    parser.add_argument("--match-window-days", type=int, default=MATCH_WINDOW_DAYS)
    parser.add_argument("--position", default=None, help="e.g. ST, CB")
    parser.add_argument("--min-early-price", type=int, default=None)
    parser.add_argument("--max-early-price", type=int, default=None)
    parser.add_argument("--include-uncertain", action="store_true",
                         help="Include cards whose chart extraction was flagged low-confidence "
                              "(status != success, calibration error, or tick warnings) - "
                              "excluded by default because these produce fake spectacular % moves")
    parser.add_argument("--top", type=int, default=30, help="Rows to print (0 = all)")
    return parser.parse_args()


def _env_date(name: str) -> Optional[date]:
    from datetime import datetime
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_date(explicit: Optional[str], env_name: str, label: str, hardcoded_fallback: Optional[date] = None) -> date:
    if explicit:
        return date.fromisoformat(explicit)
    fallback = _env_date(env_name) or hardcoded_fallback
    if fallback is None:
        raise SystemExit(f"No {label} date: pass --{label}-date or set {env_name}")
    return fallback


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


async def _nearest_prices(conn, card_ids: List[int], target: date, window_days: int) -> Dict[int, Dict[str, Any]]:
    if not card_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (card_id) card_id, observed_date, price
        FROM fc26_historical_daily_prices
        WHERE card_id = ANY($1::bigint[])
          AND observed_date >= $2
          AND observed_date <= $3
        ORDER BY card_id, observed_date ASC
        """,
        card_ids, target, target + timedelta(days=window_days),
    )
    return {r["card_id"]: {"date": r["observed_date"], "price": r["price"]} for r in rows}


async def main() -> int:
    args = _parse_args()
    dsn = args.dsn or os.getenv("PLAYER_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        log.error("need --dsn or PLAYER_DATABASE_URL/DATABASE_URL")
        return 1

    early_hardcoded = FC26_WEB_APP_LAUNCH_DATE if args.game_year == 26 else None
    full_hardcoded = FC26_FULL_RELEASE_DATE if args.game_year == 26 else None
    early = _resolve_date(args.early_date, "EAFC_WEB_APP_LAUNCH_DATE", "early", early_hardcoded)
    full = _resolve_date(args.full_date, "EAFC_FULL_RELEASE_DATE", "full", full_hardcoded)
    if full <= early:
        log.error("full date (%s) must be after early date (%s)", full, early)
        return 1

    import asyncpg
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            card_where = ["p.game_year = $1"]
            query_args: List[Any] = [args.game_year]
            if args.position:
                query_args.append(args.position.upper())
                card_where.append(f"p.primary_position = ${len(query_args)}")
            if not args.include_uncertain:
                card_where.append(
                    "(r.card_id IS NULL OR (r.status = 'success' "
                    f"AND (r.calibration_max_pct_err IS NULL OR r.calibration_max_pct_err <= {HIGH_CALIBRATION_ERR_PCT}) "
                    "AND cardinality(r.tick_warnings) = 0))"
                )

            players = await conn.fetch(
                f"""
                SELECT p.source_card_id, p.name, p.display_name, p.rating,
                       p.primary_position, p.rarity, p.pace
                FROM futgg_players p
                LEFT JOIN fc26_historical_extraction_runs r ON r.card_id = p.source_card_id
                WHERE {' AND '.join(card_where)}
                  AND EXISTS (SELECT 1 FROM fc26_historical_daily_prices h WHERE h.card_id = p.source_card_id)
                """,
                *query_args,
            )
            card_ids = [r["source_card_id"] for r in players]
            early_prices = await _nearest_prices(conn, card_ids, early, args.match_window_days)
            full_prices = await _nearest_prices(conn, card_ids, full, args.match_window_days)
    finally:
        await pool.close()

    rows: List[Dict[str, Any]] = []
    for p in players:
        cid = p["source_card_id"]
        e, f = early_prices.get(cid), full_prices.get(cid)
        if not e or not f:
            continue
        if args.min_early_price is not None and e["price"] < args.min_early_price:
            continue
        if args.max_early_price is not None and e["price"] > args.max_early_price:
            continue
        abs_change = f["price"] - e["price"]
        pct_change = (abs_change / e["price"] * 100.0) if e["price"] else None
        rows.append({
            "name": p["display_name"] or p["name"],
            "rating": p["rating"],
            "position": p["primary_position"],
            "pace": p["pace"],
            "early_price": e["price"],
            "early_date": e["date"],
            "full_price": f["price"],
            "full_date": f["date"],
            "abs_change": abs_change,
            "pct_change": pct_change,
        })

    if not rows:
        print(f"No cards had a priced observation within {args.match_window_days} days of both "
              f"{early} and {full}. Try --match-window-days higher, or check "
              f"fc26_historical_daily_prices coverage with scripts/audit_fc26_price_coverage.py.")
        return 0

    rows.sort(key=lambda r: r["pct_change"] or 0, reverse=True)
    top = rows if args.top == 0 else rows[:args.top]

    print()
    print("=" * 100)
    print(f"FC{args.game_year} PRICE RISE: {early} (early release) -> {full} (full release)")
    print(f"  {len(rows)} cards with prices near both dates (window +/-{args.match_window_days}d, forward-search)"
          + (f", position={args.position}" if args.position else ""))
    print("=" * 100)
    print(f"  {'#':<4}{'card':<28}{'rtg':>4}{'pos':>5}{'pace':>6}{'early':>10}{'full':>10}{'change':>12}{'pct':>9}")
    for i, r in enumerate(top, 1):
        pct = f"{r['pct_change']:+.1f}%" if r["pct_change"] is not None else "  n/a"
        print(f"  {i:<4}{(r['name'] or '?')[:27]:<28}{r['rating'] or '-':>4}{r['position'] or '-':>5}"
              f"{r['pace'] if r['pace'] is not None else '-':>6}{r['early_price']:>10,}{r['full_price']:>10,}"
              f"{r['abs_change']:>+12,}{pct:>9}")

    by_position: Dict[str, List[float]] = {}
    for r in rows:
        if r["pct_change"] is None or not r["position"]:
            continue
        by_position.setdefault(r["position"], []).append(r["pct_change"])
    print()
    print("  MEDIAN % CHANGE BY POSITION")
    for pos, vals in sorted(by_position.items(), key=lambda kv: _median(kv[1]) or 0, reverse=True):
        print(f"    {pos:<5} median={_median(vals):+.1f}%  n={len(vals)}")

    pace_groups: Dict[str, List[float]] = {"high_pace": [], "low_pace": []}
    for r in rows:
        if r["pct_change"] is None or r["pace"] is None:
            continue
        key = "high_pace" if r["pace"] >= PACE_HIGH_THRESHOLD else "low_pace"
        pace_groups[key].append(r["pct_change"])
    print()
    print(f"  MEDIAN % CHANGE: PACE {PACE_HIGH_THRESHOLD}+ VS UNDER {PACE_HIGH_THRESHOLD}")
    for key, vals in pace_groups.items():
        med = _median(vals)
        print(f"    {key:<10} median={'n/a' if med is None else f'{med:+.1f}%'}  n={len(vals)}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
