"""SalesSync V2 shadow scheduler: market-value coverage and capacity accounting.

Read-only. Does not fetch FUT.GG, modify scheduling columns, or run alongside
production as an additional collector. Run with DATABASE_URL to audit the
candidate pool and proposed dispatch order before a controlled cutover.

The five-minute bulk BIN feed is separate from completed-sales collection.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

from db_connect import create_pool_with_retry

log = logging.getLogger("sales_v2_shadow")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MIN_BIN = int(os.getenv("SALES_V2_MIN_BIN", "5000"))
PREMIUM_BIN = int(os.getenv("SALES_V2_PREMIUM_BIN", "20000"))
TARGET_INTERVAL_MINUTES = int(os.getenv("SALES_V2_TARGET_MINUTES", "60"))
OBSERVED_ATTEMPTS_PER_DAY = int(
    os.getenv("SALES_V2_OBSERVED_DAILY_ATTEMPTS", "480")
)
GAME_YEAR = int(os.getenv("FUTGG_GAME_YEAR", "27"))


def select_candidates(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Assign a fair age-first order inside premium and standard price pools.

    This is planning only. An unknown tradeability flag is not treated as
    proof of tradeability; cards explicitly marked false are excluded.
    A failed recent attempt is not incorrectly counted as a success.
    Previously never-read cards are prioritised for an initial observation.
    """
    eligible = []
    for row in rows:
        price = row.get("current_bin")
        if price is None or float(price) < MIN_BIN or row.get("is_tradeable") is False:
            continue
        successful = row.get("last_sales_status") == "success"
        checked = row.get("last_sales_status_at") if successful else None
        first = row.get("sales_first_read_at")
        due = row.get("next_sales_due_at")
        failures = int(row.get("sales_consecutive_failures") or 0)
        if due is not None and due > now:
            continue
        # Scheduling respects documented retry/backoff dates.
        premium = float(price) >= PREMIUM_BIN
        eligible.append({
            **row, "price": float(price), "premium": premium,
            "never_read": first is None,
            "last_verifiable_success": checked,
            "failed_attempts": failures,
        })
    # Fairness within each market tier; successes never permanently outrank
    # first reads. Unknown previous successes rank as oldest.
    eligible.sort(key=lambda r: (
        not r["never_read"],
        not r["premium"],
        r["last_verifiable_success"] or datetime.min.replace(tzinfo=timezone.utc),
        -r["price"],
        int(r["source_card_id"]),
    ))
    return eligible


def capacity_summary(eligible_count: int, rate_per_day: int, target_minutes: int) -> dict:
    target_per_day = eligible_count * 1440 / target_minutes if target_minutes > 0 else 0
    return {
        "eligible": eligible_count,
        "target_minutes": target_minutes,
        "required_checks_per_day": math.ceil(target_per_day),
        "required_per_minute": round(target_per_day / 1440, 2),
        "observed_attempts_per_day": rate_per_day,
        "optimistic_refresh_hours": round(
            eligible_count * 24 / rate_per_day, 1
        ) if rate_per_day > 0 else None,
        "hourly_feasible_even_if_all_attempts_succeed": rate_per_day >= target_per_day,
    }


async def run() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required (read-only shadow runner)")
    pool = await create_pool_with_retry(dsn, min_size=1, max_size=1, command_timeout=45)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.source_card_id, p.is_tradeable, p.last_sales_status,
                       p.last_sales_status_at, p.sales_first_read_at,
                       p.sales_consecutive_failures, p.next_sales_due_at,
                       m.current_bin
                FROM futgg_players p
                JOIN futgg_market_snapshot m
                  ON m.source_card_id = p.source_card_id
                 AND m.game_year = p.game_year
                WHERE p.game_year = $1 AND p.is_active
                  AND p.is_tradeable IS DISTINCT FROM FALSE
                  AND m.current_bin >= $2
                """,
                GAME_YEAR, MIN_BIN,
            )
        now = datetime.now(timezone.utc)
        all_rows = [dict(row) for row in rows]
        due = select_candidates(all_rows, now)
        log.info("V2 shadow: %s", capacity_summary(
            len(all_rows), OBSERVED_ATTEMPTS_PER_DAY,
            TARGET_INTERVAL_MINUTES,
        ))
        log.info(
            "V2 shadow: eligible=%d due_now=%d premium_due=%d never_read_due=%d "
            "failed_due=%d; no requests sent",
            len(all_rows), len(due), sum(c["premium"] for c in due),
            sum(c["never_read"] for c in due),
            sum(c["failed_attempts"] > 0 for c in due),
        )
        log.info(
            "V2 shadow next 20 cards: %s",
            [(r["source_card_id"], int(r["price"]), r["premium"])
             for r in due[:20]],
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
