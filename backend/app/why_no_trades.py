#!/usr/bin/env python
# app/why_no_trades.py
#
# Read-only. Answers one question, completely, in one run:
#
#     Why is the dashboard showing zero trades?
#
# It exists because that question has now taken several rounds of
# inferring deployment state from screenshots. Every candidate cause -
# migrations not applied, a worker not deployed, the snapshot not
# refreshing, a scrape cadence that outruns the engine's own gate, or
# simply a market with no edges in it today - produces the identical
# symptom: "0 trades available". This script distinguishes them.
#
# It writes nothing and needs no flag. It runs the REAL engine over the
# REAL dashboard candidate pool through the REAL provider query, so its
# verdict is what production would produce, not an approximation of it.
# That last point matters: a previous fix was verified with SELECT * and
# hand-built dicts, and was inert in production for days because the
# provider's explicit column list had not been updated. This report goes
# through that column list deliberately.
#
# The same analysis is also served, admin-only, at
# GET /api/admin/why-no-trades - because the person who needs it is often
# looking at a phone rather than a shell, which is how this question has
# repeatedly ended up being answered from screenshots.
#
#   cd backend && python -m app.why_no_trades
#   cd backend && python -m app.why_no_trades --limit 500 --examples 5
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.futgg_config import ENGINE_CONFIG  # noqa: E402
from app.services.futgg_intelligence import evaluate_card  # noqa: E402
from app.services.market_data_provider import (  # noqa: E402
    FutggMarketDataProvider,
    resolve_snapshot_columns,
)

# Mirrors the dashboard's own pool - see routers/v2/futgg_market.py's
# CANDIDATE_SCAN_LIMIT / CANDIDATE_SCAN_ORDER_BY. Same 500 cards, same
# ordering, so "16 of 500" here means the same thing it means on screen.
DASHBOARD_LIMIT = 500
DASHBOARD_ORDER_BY = "sales_count DESC NULLS LAST"

BAR = "=" * 72


def _h(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f}%" if whole else "    -"


async def check_deployment(conn) -> Dict[str, Any]:
    """Which of the fixes are actually live on THIS database.

    Each of these has silently been the answer at least once. A column
    that does not exist and a column that exists but is never written
    produce the same engine behaviour - None - so they are reported
    separately.
    """
    _h("1. DEPLOYMENT STATE")

    view_cols = {
        r["attname"]
        for r in await conn.fetch(
            """
            SELECT a.attname FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'futgg_market_snapshot'
              AND a.attnum > 0 AND NOT a.attisdropped
            """
        )
    }
    has_checked = "bin_checked_at" in view_cols
    has_interdecile = "sales_interdecile_ratio" in view_cols
    print(f"  migration 052 applied (bin_checked_at on view) : {has_checked}")
    print(f"  migration 050 applied (sales_interdecile_ratio): {has_interdecile}")

    if not has_checked:
        print("\n  >> Migration 052 has NOT been applied. The price-freshness fix")
        print("     cannot work: every card is judged on when its price last")
        print("     CHANGED, so any card with a stable price fails STALE_MARKET.")
        return {"has_checked": False}

    written = await conn.fetchrow(
        """
        SELECT count(*) AS total,
               count(price_checked_at) AS populated,
               EXTRACT(EPOCH FROM (now() - max(price_checked_at))) / 60.0 AS newest_min
        FROM futgg_players WHERE is_active
        """
    )
    print(
        f"  price_checked_at populated                     : "
        f"{written['populated']}/{written['total']}"
    )
    if written["newest_min"] is None:
        print("\n  >> No card has EVER had price_checked_at written. The bulk price")
        print("     sync is running an old build - the column exists but nothing")
        print("     fills it, so the engine still falls back to the old clock.")
    else:
        print(f"  newest price_checked_at                        : {written['newest_min']:.1f} min ago")
        if written["newest_min"] > 30:
            print("\n  >> Nothing has been price-checked in over 30 minutes. The bulk")
            print("     price sync is not running, or is not writing this column.")

    snap_age = await conn.fetchval(
        "SELECT EXTRACT(EPOCH FROM (now() - max(snapshot_computed_at))) / 60.0"
        " FROM futgg_market_snapshot"
    )
    print(f"  futgg_market_snapshot last refreshed           : {snap_age:.1f} min ago")
    if snap_age and snap_age > 15:
        print("\n  >> The snapshot is stale. Everything below reads from it, so the")
        print("     engine is judging a market that is that many minutes out of")
        print("     date. Check the backend's refresher (it warns when blocked).")

    return {"has_checked": has_checked, "snapshot_age_min": snap_age}


async def check_sales_coverage(conn) -> None:
    """Can the scrape schedule physically satisfy the engine's gate?

    The engine refuses to value a card whose newest sale is older than
    reject_sales_older_than_minutes. A rating band re-read less often than
    that can never pass, no matter how healthy the scraper is - the
    schedule itself guarantees the rejection.
    """
    _h("2. SALES FRESHNESS vs THE ENGINE'S GATE")
    limit_min = ENGINE_CONFIG.reject_sales_older_than_minutes
    print(f"  Engine rejects sales older than {limit_min} min ({limit_min / 60:.0f}h)\n")

    rows = await conn.fetch(
        """
        SELECT CASE WHEN rating >= 85 THEN '85_plus'
                    WHEN rating >= 80 THEN '80_84'
                    WHEN rating >= 75 THEN '75_79'
                    ELSE 'under_75' END AS band,
               count(*) AS cards,
               count(*) FILTER (
                   WHERE latest_sale_at >= now() - make_interval(mins => $1)
               ) AS fresh,
               count(*) FILTER (WHERE latest_sale_at IS NULL) AS never
        FROM futgg_market_snapshot
        WHERE is_active AND is_tradeable IS NOT FALSE
        GROUP BY 1 ORDER BY 1
        """,
        limit_min,
    )
    print(f"  {'band':<10} {'cards':>7} {'sales fresh enough':>20} {'no sales at all':>17}")
    for r in rows:
        print(
            f"  {r['band']:<10} {r['cards']:>7} "
            f"{r['fresh']:>10} {_pct(r['fresh'], r['cards']):>9} "
            f"{r['never']:>10} {_pct(r['never'], r['cards']):>6}"
        )

    intervals = {
        "85_plus": int(os.getenv("FUTGG_SALES_85_PLUS_INTERVAL_MIN", 60)),
        "80_84": int(os.getenv("FUTGG_SALES_80_84_INTERVAL_MIN", 240)),
        "75_79": int(os.getenv("FUTGG_SALES_75_79_INTERVAL_MIN", 720)),
        "under_75": int(os.getenv("FUTGG_SALES_UNDER_75_INTERVAL_MIN", 2880)),
    }
    conflicts = [(b, m) for b, m in intervals.items() if m >= limit_min]
    if conflicts:
        print("\n  >> CADENCE CONFLICT. These bands are scheduled to be re-read less")
        print("     often than the engine accepts sales for, so they can NEVER")
        print("     produce a recommendation:")
        for band, mins in conflicts:
            print(f"       {band}: every {mins} min ({mins / 60:.0f}h) vs a {limit_min} min limit")
        print("     (read from THIS process's env - set the same vars here as on")
        print("      the SalesSync service, or these will read as defaults)")


async def replay_dashboard_pool(conn, provider, limit: int, examples: int) -> None:
    """Run the real engine over the real pool and tally what blocks it."""
    _h("3. WHY EACH CARD IS REJECTED")

    # Through the provider's own resolver, so this report degrades on a
    # partially-migrated database exactly as production does.
    columns = await resolve_snapshot_columns(conn)
    rows = [
        dict(r)
        for r in await conn.fetch(
            f"""
            SELECT {columns} FROM futgg_market_snapshot
            WHERE is_tradeable IS DISTINCT FROM FALSE
            ORDER BY {DASHBOARD_ORDER_BY} LIMIT $1
            """,
            limit,
        )
    ]
    if not rows:
        print("  The snapshot returned no rows at all.")
        return

    sales_by_card = await provider.get_sales_by_ids([int(r["source_card_id"]) for r in rows])
    as_of = datetime.now(timezone.utc)

    blocked: Counter = Counter()
    signals: Counter = Counter()
    samples: Dict[str, List[str]] = {}
    for row in rows:
        card_id = int(row["source_card_id"])
        ci = evaluate_card(row, as_of=as_of, sales=sales_by_card.get(card_id, []))
        signals[ci.signal] += 1
        for code in (ci.blocking_codes or []):
            blocked[code] += 1
            samples.setdefault(code, [])
            if len(samples[code]) < examples:
                samples[code].append(f"{row.get('name')} ({card_id}, {row.get('price_tier')})")

    print(f"  Pool: the {len(rows)} most-traded cards - the dashboard's own pool.\n")
    print("  Signals produced:")
    for signal, n in signals.most_common():
        print(f"    {signal:<20} {n:>5}  {_pct(n, len(rows))}")

    if not blocked:
        print("\n  Nothing is blocking. If the dashboard still shows no trades, the")
        print("  problem is downstream of the engine, not in the data.")
        return

    print("\n  Blocking reasons (a card can hit several):")
    for code, n in blocked.most_common():
        print(f"    {code:<28} {n:>5}  {_pct(n, len(rows))}")
        for sample in samples.get(code, []):
            print(f"        e.g. {sample}")

    top, top_n = blocked.most_common(1)[0]
    print(f"\n  >> DOMINANT CAUSE: {top} ({top_n} of {len(rows)} cards)")
    guidance = {
        "stale_market": (
            "The BIN price is judged too old. If price_checked_at is being\n"
            "     written (section 1) this should be rare - check the bulk price\n"
            "     sync is running and that migration 052 is applied."
        ),
        "stale_sales": (
            "Sales data is too old. This is SalesSync coverage - either it is\n"
            "     not running, has not reached these cards yet, or the cadence\n"
            "     conflict in section 2 makes it structurally impossible."
        ),
        "insufficient_sales": (
            "Fewer than %d sales in the window. SalesSync has not gathered\n"
            "     enough history for these cards yet." % ENGINE_CONFIG.min_sales_for_signal
        ),
        "excessive_dispersion": (
            "Sales are too scattered to derive a price. This is the engine\n"
            "     working as intended - it is refusing to guess."
        ),
        "price_above_max_buy": (
            "The engine CAN value these cards and is simply saying today's\n"
            "     asking prices are not good enough. That is a real market\n"
            "     answer, not a fault."
        ),
    }
    print(f"     {guidance.get(top, 'See futgg_reasons.py for this code.')}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=DASHBOARD_LIMIT)
    ap.add_argument("--examples", type=int, default=3)
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = ap.parse_args()

    if not args.database_url:
        print("Set DATABASE_URL (or pass --database-url).", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(args.database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            state = await check_deployment(conn)
            await check_sales_coverage(conn)
            await replay_dashboard_pool(
                conn, FutggMarketDataProvider(pool), args.limit, args.examples
            )
        print(f"\n{BAR}\nRead-only: nothing was modified.\n{BAR}")
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
