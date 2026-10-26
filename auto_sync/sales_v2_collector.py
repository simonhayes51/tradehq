"""SalesSync V2 collector entrypoint, sharing V1's hardened fetch/write transport.

SAFETY: Runs only when SALES_V2_ENABLE_FETCH=true; additionally requires
SALES_V2_ALLOW_WRITES=true for production writes. Uses the SAME Postgres
advisory lock as V1. Do not run both as production services: V1 wins the
lock until stopped. A single source budget is never doubled.

This deliberately reuses the battle-tested signed fetch, page-capture,
429 circuit breaker, deduplicating INSERTs and failure retry mechanics.
Only the catalogue selection and post-success cadence are changed.
"""
from __future__ import annotations

import asyncio
import logging
import os

import futgg_sales_sync as sales
from sales_v2_shadow import MIN_BIN, PREMIUM_BIN, TARGET_INTERVAL_MINUTES

log = logging.getLogger("sales_v2_collector")


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"true", "1", "yes"}



# V1 automatic recovery is off for good reason: the original controller
# speeds up every few successful *batches*, causing a 429 storm once
# the windowed FUT.GG quota is exhausted. V2 instead makes at most one
# small adjustment per completed selection-sized block of successful
# requests. No new simultaneous requests are introduced.
RECOVERY_SUCCESS_REQUESTS = max(
    30, int(os.getenv("SALES_V2_RECOVERY_SUCCESS_REQUESTS", "90"))
)
RECOVERY_DELAY_STEP_SECONDS = min(
    5.0, max(0.5, float(os.getenv("SALES_V2_RECOVERY_DELAY_STEP_SECONDS", "5")))
)
_BaseThrottle = sales.AdaptiveThrottle


class GuardedRecoveryThrottle(_BaseThrottle):
    """Conservative recovery: 90 consecutive clean requests -> 5s less delay.

    Any throttled result resets the streak and existing base.record()
    immediately applies normal backoff. Batch size is capped to one to
    avoid suddenly spiking concurrent requests after clean cycles.
    Resume/persisted delay continues to be honoured.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch_size = 1
        self.max_batch = 1
        self._v2_clean_requests = 0

    def record(self, attempted, throttled, retry_after_seconds=None):
        super().record(attempted, throttled, retry_after_seconds)
        if throttled:
            self._v2_clean_requests = 0
            return
        if attempted <= 0:
            return
        self._v2_clean_requests += attempted
        if self._v2_clean_requests < RECOVERY_SUCCESS_REQUESTS:
            return
        self._v2_clean_requests = 0
        previous = self.delay
        self.delay = max(self.min_delay, self.delay - RECOVERY_DELAY_STEP_SECONDS)
        if self.delay < previous:
            log.warning(
                "V2 measured throttle recovery after %d clean requests: "
                "batch=1 delay %.1fs -> %.1fs (floor=%.1fs)",
                RECOVERY_SUCCESS_REQUESTS, previous, self.delay, self.min_delay,
            )


def configure() -> None:
    if not _enabled("SALES_V2_ENABLE_FETCH"):
        raise SystemExit(
            "V2 fetch disabled. Run sales_v2_shadow.py for the DB-only audit; "
            "set SALES_V2_ENABLE_FETCH=true only after stopping V1."
        )
    if MIN_BIN < 1000:
        raise SystemExit("SALES_V2_MIN_BIN must be at least 1000")
    if TARGET_INTERVAL_MINUTES < 60:
        raise SystemExit("SALES_V2_TARGET_MINUTES must be >=60")
    # Fail closed. Never turn on production writes implicitly because
    # FUTGG_SALES_APPLY defaults to true in the legacy worker.
    sales.APPLY = _enabled("SALES_V2_ALLOW_WRITES")
    # V2 collects any market-valued card, regardless of rarity and rating.
    # Every selected row is assigned the same cadence after success.
    sales.SALES_INTERVALS.update({
        tier: TARGET_INTERVAL_MINUTES
        for tier in sales.SALES_INTERVALS
    })
    sales.AdaptiveThrottle = GuardedRecoveryThrottle
    sales.select_due = select_market_due
    sales.report_coverage = report_market_coverage
    log.warning(
        "SalesSync V2 fetch ACTIVE min_bin=%d premium_bin=%d target=%dmin "
        "writes=%s; shared V1 advisory lock and rate limits retained",
        MIN_BIN, PREMIUM_BIN, TARGET_INTERVAL_MINUTES, sales.APPLY,
    )


async def select_market_due(conn):
    """Every 5k+ card gets a fair chance, within existing source budget.

    A previous successful check can be rescheduled after target_minutes
    even when V1 wrote a later next_due_at. Failures retain their actual
    retry/backoff deadline; no_price_section exclusions also retain it.
    No-price cards are not fetched. Never-read cards are preferred, with
    both >20k and 5k-20k receiving service via a reserved quota.
    """
    limit = sales.SELECT_LIMIT
    # 20% guaranteed slots for the less expensive end to avoid starvation.
    cheap_floor = max(1, limit // 5)
    sql = """
        SELECT p.source_card_id, p.rating, p.next_sales_due_at,
               p.source_url, p.sales_first_read_at,
               p.sales_consecutive_failures,
               'CRITICAL'::text AS effective_tier
        FROM futgg_players p
        JOIN futgg_market_snapshot m
          ON m.source_card_id = p.source_card_id AND m.game_year = p.game_year
        WHERE p.game_year = $1
          AND p.is_active
          AND p.is_tradeable IS DISTINCT FROM FALSE
          AND m.current_bin >= $2
          AND m.current_bin < $3
          AND (
              CASE
                WHEN p.last_sales_status = 'success'
                THEN p.last_sales_status_at IS NULL
                  OR p.last_sales_status_at <= NOW() - ($4::int * interval '1 minute')
                ELSE p.next_sales_due_at IS NULL OR p.next_sales_due_at <= NOW()
              END
          )
        ORDER BY (p.sales_first_read_at IS NOT NULL),
                 p.last_sales_status_at ASC NULLS FIRST,
                 p.source_card_id
        LIMIT $5
    """
    # Avoid 90x individual queries. Two bounded selects are enough.
    cheaper = await conn.fetch(
        sql, int(sales.GAME_YEAR), MIN_BIN, PREMIUM_BIN,
        TARGET_INTERVAL_MINUTES, cheap_floor,
    )
    premium = await conn.fetch(
        sql, int(sales.GAME_YEAR), PREMIUM_BIN, 2147483647,
        TARGET_INTERVAL_MINUTES, limit - len(cheaper),
    )
    # Fill any slack in either group with due cards; don't reserve idle
    # quota. A deduplicated source-id list protects against duplicate work.
    result = list(cheaper) + list(premium)
    if len(result) < limit:
        extra = await conn.fetch(
            """
            SELECT p.source_card_id, p.rating, p.next_sales_due_at,
                   p.source_url, p.sales_first_read_at,
                   p.sales_consecutive_failures,
                   'CRITICAL'::text AS effective_tier
            FROM futgg_players p
            JOIN futgg_market_snapshot m
              ON m.source_card_id = p.source_card_id AND m.game_year = p.game_year
            WHERE p.game_year = $1 AND p.is_active
              AND p.is_tradeable IS DISTINCT FROM FALSE
              AND m.current_bin >= $2
              AND NOT (p.source_card_id = ANY($3::bigint[]))
              AND (
                  CASE
                    WHEN p.last_sales_status = 'success'
                    THEN p.last_sales_status_at IS NULL
                      OR p.last_sales_status_at <= NOW() - ($4::int * interval '1 minute')
                    ELSE p.next_sales_due_at IS NULL OR p.next_sales_due_at <= NOW()
                  END
              )
            ORDER BY (p.sales_first_read_at IS NOT NULL),
                     p.last_sales_status_at ASC NULLS FIRST,
                     p.source_card_id
            LIMIT $5
            """,
            int(sales.GAME_YEAR), MIN_BIN,
            [int(r["source_card_id"]) for r in result],
            TARGET_INTERVAL_MINUTES, limit - len(result),
        )
        result.extend(extra)
    log.info(
        "V2 market selection: cards=%d premium=%d base=%d never_read=%d",
        len(result), len(premium), len(result) - len(premium),
        sum(r["sales_first_read_at"] is None for r in result),
    )
    return result



async def report_market_coverage(conn):
    """Report useful-market coverage, not the entire 23k-card catalogue.

    Existing V1 report_coverage counts cheap and no-market cards even
    though V2 intentionally does not schedule them. Snapshot sales_count
    is the same 7-day signal sample the intelligence engine consumes.
    """
    row = await conn.fetchrow(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE COALESCE(m.sales_count, 0) > 0) AS any_sales,
               count(*) FILTER (WHERE COALESCE(m.sales_count, 0) >= 5) AS enough_sales,
               count(*) FILTER (WHERE p.sales_first_read_at IS NULL) AS never_read,
               count(*) FILTER (WHERE p.sales_consecutive_failures >= 5) AS chronic_failures,
               count(*) FILTER (
                   WHERE p.last_sales_status = 'success'
                     AND p.last_sales_status_at >= NOW() - ($3::int * interval '1 minute')
               ) AS recently_successful
        FROM futgg_players p
        JOIN futgg_market_snapshot m
          ON m.source_card_id = p.source_card_id AND m.game_year = p.game_year
        WHERE p.game_year = $1 AND p.is_active
          AND p.is_tradeable IS DISTINCT FROM FALSE
          AND m.current_bin >= $2
        """,
        int(sales.GAME_YEAR), MIN_BIN, TARGET_INTERVAL_MINUTES,
    )
    total = int(row["total"] or 0)
    if total == 0:
        log.warning("V2 coverage: no eligible priced cards")
        return None
    log.info(
        "V2 eligible-market coverage: sales=%d/%d enough_sales=%d/%d "
        "never_successfully_read=%d chronic_failures=%d "
        "checked_successfully_within_target=%d/%d",
        row["any_sales"], total, row["enough_sales"], total,
        row["never_read"], row["chronic_failures"], row["recently_successful"], total,
    )
    return {
        "total": total,
        "any_sales": int(row["any_sales"] or 0),
        "enough_sales": int(row["enough_sales"] or 0),
        "never_read": int(row["never_read"] or 0),
        "chronic_failures": int(row["chronic_failures"] or 0),
    }


if __name__ == "__main__":
    configure()
    asyncio.run(sales.run_forever())
