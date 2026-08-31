# app/services/funnel_retention.py
#
# Deleting funnel events that are too old to be worth keeping.
#
# funnel_events grows unbounded and was flagged as such when it shipped.
# Twelve months is the default because a year is the shortest window that
# still lets you compare a launch month with the same month a year later,
# and because a behavioural record of individual accounts is not something
# to keep indefinitely just because the disk is cheap.
#
# Two deliberate choices:
#
#   * BOUNDED BATCHES. A single unqualified DELETE over a table with
#     millions of rows takes a long lock and can block the tracking
#     endpoint that writes to it. Deleting in chunks, with a pause, keeps
#     the job invisible to everything else.
#
#   * NO AGGREGATE ROLL-UP IS INVENTED HERE. The brief asks to preserve
#     longer-term aggregates "if such aggregates already exist". They do
#     not: the dashboard computes everything live from the raw rows. So
#     this deletes, and does not silently start writing a summary table
#     nobody asked for - which would be a new, untested source of truth
#     about the product's history. If long-horizon trends are wanted
#     later, a monthly roll-up is the right shape and is a separate piece
#     of work.
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger("funnel_retention")

RETENTION_DAYS = int(os.getenv("FUNNEL_RETENTION_DAYS", "365"))
# Small enough that each statement is short, large enough that a year of
# accumulated rows does not take all day.
BATCH_SIZE = int(os.getenv("FUNNEL_RETENTION_BATCH", "5000"))
# A breather between batches so the deletion never competes with live
# writes for the same pages.
BATCH_PAUSE_SECONDS = float(os.getenv("FUNNEL_RETENTION_PAUSE", "0.5"))
# A ceiling on one pass, so a first run against a huge backlog cannot run
# unbounded. Whatever is left is picked up by the next pass.
MAX_BATCHES = int(os.getenv("FUNNEL_RETENTION_MAX_BATCHES", "200"))

# Same lock discipline as the Discord sweep: every worker runs the same
# lifespan, so every worker will try, and exactly one should act.
RETENTION_LOCK_KEY = 0x0EAFC_11FE_2E7E


@dataclass
class RetentionResult:
    deleted: int = 0
    batches: int = 0
    cutoff: Optional[datetime] = None
    dry_run: bool = False
    status: str = "completed"
    remaining_estimate: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "deleted": self.deleted,
            "batches": self.batches,
            "cutoff": self.cutoff.isoformat() if self.cutoff else None,
            "dry_run": self.dry_run,
            "status": self.status,
            "remaining_estimate": self.remaining_estimate,
        }


async def count_expired(pool, *, retention_days: Optional[int] = None) -> Dict[str, Any]:
    """How many rows are past the retention horizon. Reads nothing else.

    The count-only mode: safe to run any time, answers "what would a
    cleanup delete" without deleting anything.
    """
    days = RETENTION_DAYS if retention_days is None else retention_days
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS expired,
                   MIN(occurred_at) AS oldest
            FROM funnel_events
            WHERE occurred_at < NOW() - ($1::int * INTERVAL '1 day')
            """,
            days,
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM funnel_events")
    return {
        "retention_days": days,
        "expired": int(row["expired"] or 0),
        "total": int(total or 0),
        "oldest": row["oldest"].isoformat() if row["oldest"] else None,
    }


async def purge_expired(
    pool,
    *,
    retention_days: Optional[int] = None,
    dry_run: bool = False,
    batch_size: Optional[int] = None,
    max_batches: Optional[int] = None,
) -> RetentionResult:
    """Delete expired rows in bounded batches."""
    days = RETENTION_DAYS if retention_days is None else retention_days
    size = batch_size or BATCH_SIZE
    ceiling = max_batches or MAX_BATCHES
    result = RetentionResult(dry_run=dry_run)

    try:
        if dry_run:
            counts = await count_expired(pool, retention_days=days)
            result.deleted = counts["expired"]
            result.remaining_estimate = 0
            return result

        while result.batches < ceiling:
            async with pool.acquire() as conn:
                # Delete by primary key, chosen in a subquery with its own
                # LIMIT: `DELETE ... LIMIT n` is not valid Postgres, and
                # deleting by predicate alone would take one enormous lock
                # on the table the tracking endpoint is writing to.
                status = await conn.execute(
                    """
                    DELETE FROM funnel_events
                    WHERE id IN (
                        SELECT id FROM funnel_events
                        WHERE occurred_at < NOW() - ($1::int * INTERVAL '1 day')
                        ORDER BY id
                        LIMIT $2
                    )
                    """,
                    days, size,
                )
            # asyncpg returns the command tag, e.g. "DELETE 5000" - the
            # same parse main.py's own delete handlers use.
            deleted = int(status.split()[-1]) if status.startswith("DELETE ") else 0
            result.batches += 1
            result.deleted += deleted
            if deleted < size:
                # A short batch means the backlog is exhausted.
                result.remaining_estimate = 0
                break
            if BATCH_PAUSE_SECONDS > 0:
                await asyncio.sleep(BATCH_PAUSE_SECONDS)
        else:
            # Hit the ceiling with work left. Not a failure - the next
            # pass continues - but it should be visible.
            result.status = "partial"
    except Exception:
        log.warning("funnel retention: purge failed", exc_info=True)
        result.status = "failed"

    return result


async def purge_with_lock(pool, **kwargs) -> Optional[RetentionResult]:
    """Run a purge if no other worker is already running one."""
    try:
        async with pool.acquire() as conn:
            got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", RETENTION_LOCK_KEY)
            if not got:
                log.debug("funnel retention: another worker holds the lock; skipping")
                return None
            try:
                return await purge_expired(pool, **kwargs)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", RETENTION_LOCK_KEY)
    except Exception:
        log.warning("funnel retention: could not acquire lock", exc_info=True)
        return None


def _is_due(now: Optional[datetime] = None) -> bool:
    """Monthly, on the first of the month.

    Checked daily rather than scheduled monthly, because the worker
    pattern this project uses is a sleep loop and a process that has been
    up for a month is not something to rely on. A day-scoped advisory
    lock is unnecessary: purging twice in one day deletes nothing the
    second time.
    """
    now = now or datetime.now(timezone.utc)
    return now.day == 1


async def retention_loop(pool, interval_seconds: int) -> None:
    """Check daily; act on the first of the month."""
    await asyncio.sleep(min(interval_seconds, 600))
    while True:
        try:
            if _is_due():
                result = await purge_with_lock(pool)
                if result is not None:
                    log.info(
                        "funnel retention: deleted=%s batches=%s status=%s",
                        result.deleted, result.batches, result.status,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("funnel retention loop: pass failed", exc_info=True)
        await asyncio.sleep(interval_seconds)
