# app/services/readiness.py
#
# Is this deployment actually able to do its job.
#
# THREE STATES, NOT TWO
#
# "Healthy / unhealthy" cannot express what an operator needs to know
# here, because most of what this product depends on is optional:
#
#   ok            working
#   degraded      an OPTIONAL feature is not working. The product serves
#                 customers; something is worse than it should be.
#   unconfigured  an external integration was never set up on this
#                 deployment. Not a fault - a staging environment with no
#                 Stripe key is correct, not broken.
#   unavailable   a REQUIRED dependency is down. The product cannot serve.
#
# Collapsing these loses the distinction between "Discord is not connected
# in staging" and "the database is gone", which is the difference between
# ignoring an alert and waking someone up.
#
# WHAT IS NEVER RETURNED
#
# No exception text, no connection strings, no host names, no environment
# variable VALUES. The public endpoint returns a status word and nothing
# else per check; the admin endpoint adds a short reason code chosen from
# a fixed set. `/health` previously returned `str(e)` on a database
# failure, and an asyncpg error message can carry the connection string it
# failed to reach.
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger("readiness")

OK = "ok"
DEGRADED = "degraded"
UNCONFIGURED = "unconfigured"
UNAVAILABLE = "unavailable"

# Migrations this phase's features depend on. Named by the table each one
# creates, because a table either exists or it does not - which is a
# stronger check than reading a migrations ledger that could itself be
# out of step with the schema.
REQUIRED_TABLES = {
    "alerts_log": "072 (alert dismissal)",
    "free_daily_picks": "073 (free daily picks)",
    "member_onboarding": "074 (onboarding state)",
    "funnel_events": "075 (funnel events)",
    "ai_usage_events": "076 (AI usage accounting)",
    "discord_reconciliation_runs": "078 (Discord reconciliation)",
}


def _check(name: str, status: str, *, reason: Optional[str] = None,
           detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": name, "status": status}
    if reason:
        out["reason"] = reason
    if detail:
        out["detail"] = detail
    return out


async def _check_database(pool, name: str, *, required: bool) -> Dict[str, Any]:
    if pool is None:
        return _check(name, UNAVAILABLE if required else UNCONFIGURED, reason="pool_missing")
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return _check(name, OK)
    except Exception:
        # Logged with the traceback; reported as a code. The message on an
        # asyncpg connection error contains the DSN.
        log.warning("readiness: %s unreachable", name, exc_info=True)
        return _check(name, UNAVAILABLE if required else DEGRADED, reason="unreachable")


async def _check_migrations(pool) -> Dict[str, Any]:
    if pool is None:
        return _check("migrations", UNAVAILABLE, reason="pool_missing")
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY($1::text[])
                """,
                list(REQUIRED_TABLES),
            )
        present = {row["table_name"] for row in rows}
        missing = sorted(set(REQUIRED_TABLES) - present)
    except Exception:
        log.warning("readiness: could not read schema", exc_info=True)
        return _check("migrations", UNAVAILABLE, reason="schema_unreadable")

    if not missing:
        return _check("migrations", OK)
    # Degraded, not unavailable: every feature behind these tables
    # degrades honestly on its own (free picks fall back to an in-memory
    # selection, onboarding reports itself degraded, funnel events return
    # recorded:false). The product serves; the features are incomplete.
    return _check(
        "migrations", DEGRADED, reason="pending",
        detail={"missing": [REQUIRED_TABLES[table] for table in missing]},
    )


async def _check_redis() -> Dict[str, Any]:
    if not os.getenv("REDIS_URL"):
        # Optional by design: the AI limiter degrades to per-worker
        # counting without it. Saying "unconfigured" rather than
        # "degraded" keeps a deployment that never wanted Redis from
        # looking permanently broken.
        return _check("redis", UNCONFIGURED, reason="not_configured")
    try:
        import redis.asyncio as aioredis  # type: ignore

        client = aioredis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        await client.ping()
        return _check("redis", OK)
    except Exception:
        log.warning("readiness: Redis unreachable", exc_info=True)
        # Degraded, not unavailable: nothing stops working without Redis,
        # the AI rate limits just stop being fleet-wide.
        return _check("redis", DEGRADED, reason="unreachable")


def _check_ai() -> Dict[str, Any]:
    from app.services import ai_usage

    if not os.getenv("OPENAI_API_KEY"):
        return _check("ai_chat", UNCONFIGURED, reason="no_provider_key")
    if not ai_usage.chat_enabled():
        # Switched off on purpose. Not a fault, and it must not read as one.
        return _check("ai_chat", UNCONFIGURED, reason="disabled_by_operator")
    return _check("ai_chat", OK, detail={
        "per_day": ai_usage.RATE_LIMIT_PER_DAY,
        "per_month": ai_usage.RATE_LIMIT_PER_MONTH,
    })


def _check_stripe() -> Dict[str, Any]:
    configured = [key for key in ("STRIPE_MONTHLY_PRICE_ID", "STRIPE_SEASON_PRICE_ID")
                  if os.getenv(key)]
    if not os.getenv("STRIPE_SECRET_KEY"):
        return _check("stripe", UNCONFIGURED, reason="no_secret_key")
    if not configured:
        return _check("stripe", UNCONFIGURED, reason="no_price_configured")
    if not os.getenv("STRIPE_WEBHOOK_SECRET"):
        # This one matters more than it looks: without the webhook secret
        # nothing confirms a payment server-side, so subscriptions never
        # activate and the confirmed-revenue metric stays empty.
        return _check("stripe", DEGRADED, reason="no_webhook_secret")
    return _check("stripe", OK, detail={"plans_configured": len(configured)})


def _check_discord() -> Dict[str, Any]:
    from app.services.discord_integration import DiscordConfig

    config = DiscordConfig.from_env()
    if not config.role_sync_ready:
        return _check("discord", UNCONFIGURED, reason="role_sync_not_configured")
    return _check("discord", OK)


async def _check_discord_reconciliation(pool) -> Dict[str, Any]:
    from app.services.discord_integration import DiscordConfig
    from app.services.discord_reconcile import last_run

    if not DiscordConfig.from_env().role_sync_ready:
        return _check("discord_reconciliation", UNCONFIGURED, reason="discord_not_configured")
    history = await last_run(pool)
    if history is None:
        return _check("discord_reconciliation", DEGRADED, reason="history_unreadable")
    successful = history.get("last_successful")
    if not successful:
        # Expected on a fresh deployment - the first sweep is delayed.
        return _check("discord_reconciliation", DEGRADED, reason="no_successful_sweep_yet")
    return _check("discord_reconciliation", OK,
                  detail={"last_successful": successful.get("finished_at")})


async def _check_free_picks(pool) -> Dict[str, Any]:
    from app.services.content_day import content_day_key
    from app.services.free_picks import stored_pick_ids

    try:
        picks = await stored_pick_ids(pool, content_day_key())
    except Exception:
        log.warning("readiness: free pick store unreadable", exc_info=True)
        return _check("free_picks", DEGRADED, reason="unreadable")
    # An empty set before the first feed request of the content day is
    # normal, so this reports the count rather than judging it - a
    # readiness check that cried wolf every morning at 18:00 would be
    # worse than none.
    return _check("free_picks", OK, detail={"picks_today": len(picks)})


async def _check_funnel_events(pool) -> Dict[str, Any]:
    from app.routers.v2.events import tracking_health

    health = tracking_health()
    if health["status"] == "failing":
        return _check("funnel_events", DEGRADED, reason="recording_failing",
                      detail={"failed": health["failed"], "attempts": health["attempts"]})
    if health["status"] == "degraded":
        return _check("funnel_events", DEGRADED, reason="recording_intermittent",
                      detail={"failure_rate": health["failure_rate"]})
    return _check("funnel_events", OK, detail={"status": health["status"]})


_SEVERITY = {OK: 0, UNCONFIGURED: 0, DEGRADED: 1, UNAVAILABLE: 2}


def overall(checks: List[Dict[str, Any]]) -> str:
    """The worst thing that is wrong.

    UNCONFIGURED does not count against the overall status: a deployment
    that was never given a Stripe key is correctly configured for what it
    is, and reporting it as unhealthy trains people to ignore the check.
    """
    worst = max((_SEVERITY.get(c["status"], 0) for c in checks), default=0)
    return {0: OK, 1: DEGRADED, 2: UNAVAILABLE}[worst]


async def readiness(core_pool, watchlist_pool=None) -> Dict[str, Any]:
    """Every check. Admin-facing: includes reason codes and small details."""
    checks = [
        await _check_database(core_pool, "core_database", required=True),
        await _check_database(watchlist_pool, "watchlist_database", required=False),
        await _check_migrations(core_pool),
        await _check_redis(),
        _check_ai(),
        _check_stripe(),
        _check_discord(),
        await _check_discord_reconciliation(core_pool),
        await _check_free_picks(core_pool),
        await _check_funnel_events(core_pool),
    ]
    return {"status": overall(checks), "checks": checks}


def public_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    """The same picture with everything an outsider should not see removed.

    Names and statuses only: no reason codes, no counts, no configuration.
    Enough for a load balancer or an uptime probe, and useless to anyone
    fingerprinting the deployment.
    """
    return {
        "status": report["status"],
        "checks": {check["name"]: check["status"] for check in report["checks"]},
    }
