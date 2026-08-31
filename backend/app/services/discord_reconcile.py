# app/services/discord_reconcile.py
#
# The periodic Discord role sweep.
#
# WHY, GIVEN THERE IS ALREADY A QUEUE
#
# The queue (migration 056) is event-driven: a billing change enqueues a
# role change and the worker retries it. That is correct right up until a
# queued job is lost - a Discord outage long enough to exhaust the
# retries, a row deleted, a deploy at the wrong moment. After that the
# account stays wrong forever, because nothing will ever look at it again.
# A queue cannot notice it is out of step; only a sweep can.
#
# WHAT IT IS NOT
#
# It is not a second entitlement calculation. Whether an account should
# hold the role is answered by resolve_user_tier() - the same resolver the
# application uses - so this job can never disagree with the rest of the
# product about who has paid. That disagreement is exactly the bug the
# Phase 2 work removed (the account page showing "Current plan: ELITE"
# above "Membership access: Free"), and reintroducing it in a background
# job would be worse, because nobody would be looking.
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("discord_reconcile")

# Bounded work per pass. The sweep is a background correctness job, not an
# urgent one: it is better for it to take several passes over a large
# member list than to hold a connection and hammer Discord for minutes.
BATCH_SIZE = int(os.getenv("DISCORD_RECONCILE_BATCH", "200"))
# Discord's own rate limiting is handled inside DiscordClient._request
# (it honours retry_after). This is politeness on top: a small gap between
# accounts so a sweep never looks like a burst in the first place.
PER_ACCOUNT_DELAY_SECONDS = float(os.getenv("DISCORD_RECONCILE_DELAY", "0.15"))
# How many consecutive Discord failures before a pass gives up. A pass
# that keeps trying through a full outage burns the rate limit and
# achieves nothing; the next pass will pick the work up.
MAX_CONSECUTIVE_FAILURES = int(os.getenv("DISCORD_RECONCILE_MAX_FAILURES", "10"))

# Short codes only. An exception string from an HTTP client can carry a
# bot token or an internal hostname, and this value is stored and shown
# to an admin.
_FAILURE_REASONS = {
    "not_configured",       # no bot token / guild / role configured
    "discord_unavailable",  # Discord said no, repeatedly
    "database_error",
    "aborted",
}


@dataclass
class ReconcileSummary:
    """What one pass did. Every field is a count, never an identity."""
    accounts_checked: int = 0
    roles_granted: int = 0
    roles_revoked: int = 0
    already_correct: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = False
    status: str = "completed"
    failure_reason: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "accounts_checked": self.accounts_checked,
            "roles_granted": self.roles_granted,
            "roles_revoked": self.roles_revoked,
            "already_correct": self.already_correct,
            "skipped": self.skipped,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


_SELECT_LINKED_ACCOUNTS = """
    SELECT id::text AS user_id, discord_id::text AS discord_user_id
    FROM users
    WHERE discord_id IS NOT NULL
    ORDER BY id
    LIMIT $1 OFFSET $2
"""


async def reconcile_once(
    pool,
    *,
    dry_run: bool = False,
    batch_size: Optional[int] = None,
    client=None,
    limit_accounts: Optional[int] = None,
) -> ReconcileSummary:
    """One full sweep over every account with a linked Discord identity.

    Idempotent by construction: it reads the desired state, reads the
    actual state, and acts only on the difference. Running it twice in a
    row does nothing the second time, which is what makes it safe to run
    on a schedule, by hand, and from two workers at once.
    """
    from app.auth.entitlements import resolve_user_tier
    from app.services.discord_integration import DiscordClient

    summary = ReconcileSummary(dry_run=dry_run)
    client = client or DiscordClient()

    if not client.config.role_sync_ready:
        # Not a failure of the sweep - a deployment that has not connected
        # Discord at all. Says so rather than reporting a run that checked
        # nothing as a success.
        summary.status = "failed"
        summary.failure_reason = "not_configured"
        summary.finished_at = datetime.now(timezone.utc)
        return summary

    size = batch_size or BATCH_SIZE
    offset = 0
    consecutive_failures = 0

    try:
        while True:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_SELECT_LINKED_ACCOUNTS, size, offset)
            if not rows:
                break

            for row in rows:
                if limit_accounts is not None and summary.accounts_checked >= limit_accounts:
                    break
                user_id = row["user_id"]
                discord_user_id = row["discord_user_id"]
                if not discord_user_id:
                    # Accounts with no linked Discord identity are skipped,
                    # never "fixed": there is nothing to reconcile, and
                    # this job must not be a reason anyone is asked to
                    # reconnect anything.
                    summary.skipped += 1
                    continue

                summary.accounts_checked += 1
                try:
                    # THE one entitlement answer. Not a second calculation.
                    tier = await resolve_user_tier(pool, user_id, discord_id=discord_user_id)
                    desired = tier == "elite"

                    member = await client.get_member(discord_user_id)
                    if member.state == "not_joined":
                        # In the product, not in the server. Nothing to do
                        # and nothing wrong: the role only exists inside
                        # the guild.
                        summary.skipped += 1
                        consecutive_failures = 0
                        continue
                    if member.state != "joined":
                        summary.failed += 1
                        consecutive_failures += 1
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            summary.status = "failed"
                            summary.failure_reason = "discord_unavailable"
                            log.warning(
                                "discord reconcile: aborting after %s consecutive failures",
                                consecutive_failures,
                            )
                            summary.finished_at = datetime.now(timezone.utc)
                            return summary
                        continue

                    consecutive_failures = 0
                    has_role = str(client.config.member_role_id) in member.roles
                    if has_role == desired:
                        summary.already_correct += 1
                        continue

                    if dry_run:
                        # Counted as if it had happened, so a dry run
                        # answers "what would this change" exactly.
                        if desired:
                            summary.roles_granted += 1
                        else:
                            summary.roles_revoked += 1
                        continue

                    result = await client.reconcile_member_role(discord_user_id, desired)
                    if result.state != "joined":
                        summary.failed += 1
                        consecutive_failures += 1
                        continue
                    if desired:
                        summary.roles_granted += 1
                    else:
                        summary.roles_revoked += 1
                except Exception:
                    # One account's problem must not end the sweep. It is
                    # counted, and the next pass will try it again.
                    summary.failed += 1
                    log.warning("discord reconcile: account failed", exc_info=True)

                if PER_ACCOUNT_DELAY_SECONDS > 0:
                    await asyncio.sleep(PER_ACCOUNT_DELAY_SECONDS)

            if limit_accounts is not None and summary.accounts_checked >= limit_accounts:
                break
            if len(rows) < size:
                break
            offset += size
    except Exception:
        log.warning("discord reconcile: sweep failed", exc_info=True)
        summary.status = "failed"
        summary.failure_reason = "database_error"

    summary.finished_at = datetime.now(timezone.utc)
    return summary


# ---------------------------------------------------------------------------
# Persistence and scheduling
# ---------------------------------------------------------------------------

# Any 64-bit constant will do; it only has to be unique among this
# project's advisory locks. Two workers starting the sweep at the same
# moment is normal (every worker runs the same lifespan), and the second
# one must quietly do nothing rather than double the Discord traffic.
RECONCILE_LOCK_KEY = 0x0EAFC_11FE_D15C


async def record_run(pool, summary: ReconcileSummary) -> bool:
    """Store what a pass did. Never raises."""
    reason = summary.failure_reason if summary.failure_reason in _FAILURE_REASONS else None
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO discord_reconciliation_runs
                    (started_at, finished_at, status, dry_run, accounts_checked,
                     roles_granted, roles_revoked, already_correct, skipped,
                     failed, failure_reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                summary.started_at, summary.finished_at, summary.status, summary.dry_run,
                summary.accounts_checked, summary.roles_granted, summary.roles_revoked,
                summary.already_correct, summary.skipped, summary.failed, reason,
            )
        return True
    except Exception:
        log.warning("discord reconcile: could not record run", exc_info=True)
        return False


async def last_run(pool) -> Optional[Dict[str, Any]]:
    """The most recent pass, and the most recent SUCCESSFUL one.

    Both, because "it ran an hour ago" and "it last worked a week ago" are
    different facts and the second is the one that matters.
    """
    try:
        async with pool.acquire() as conn:
            latest = await conn.fetchrow(
                "SELECT * FROM discord_reconciliation_runs ORDER BY started_at DESC LIMIT 1"
            )
            succeeded = await conn.fetchrow(
                """
                SELECT * FROM discord_reconciliation_runs
                WHERE status = 'completed' AND dry_run = FALSE
                ORDER BY finished_at DESC NULLS LAST LIMIT 1
                """
            )
    except Exception:
        log.warning("discord reconcile: could not read run history", exc_info=True)
        return None

    def _row(row):
        if row is None:
            return None
        out = dict(row)
        for key in ("started_at", "finished_at"):
            if out.get(key) is not None:
                out[key] = out[key].isoformat()
        return out

    return {"latest": _row(latest), "last_successful": _row(succeeded)}


async def reconcile_with_lock(pool, *, dry_run: bool = False, **kwargs) -> Optional[ReconcileSummary]:
    """Run one sweep if no other worker is already running one.

    Returns None when the lock was not acquired - not an error, just
    "somebody else has this". Uses a session-scoped advisory lock held for
    the WHOLE pass rather than released after the SELECT, for the same
    reason the alerts poller does: releasing early lets a second worker
    start the identical work and race it.
    """
    try:
        async with pool.acquire() as conn:
            got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", RECONCILE_LOCK_KEY)
            if not got:
                log.debug("discord reconcile: another worker holds the lock; skipping")
                return None
            try:
                summary = await reconcile_once(pool, dry_run=dry_run, **kwargs)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", RECONCILE_LOCK_KEY)
    except Exception:
        log.warning("discord reconcile: could not acquire lock", exc_info=True)
        return None

    await record_run(pool, summary)
    return summary


async def reconcile_loop(pool, interval_seconds: int) -> None:
    """The scheduled sweep.

    Uses the project's existing worker pattern - an asyncio task started
    in the app lifespan, guarded by an advisory lock - rather than adding
    a scheduler. The first pass is deliberately delayed: a worker that
    starts a Discord sweep the instant it boots makes every deploy a
    thundering herd against the Discord API.
    """
    await asyncio.sleep(min(interval_seconds, 300))
    while True:
        try:
            summary = await reconcile_with_lock(pool)
            if summary is not None:
                log.info(
                    "discord reconcile: checked=%s granted=%s revoked=%s correct=%s "
                    "skipped=%s failed=%s status=%s",
                    summary.accounts_checked, summary.roles_granted, summary.roles_revoked,
                    summary.already_correct, summary.skipped, summary.failed, summary.status,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("discord reconcile loop: pass failed", exc_info=True)
        await asyncio.sleep(interval_seconds)
