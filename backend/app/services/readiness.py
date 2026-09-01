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
from datetime import datetime, timezone
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


# One client for the process, created on first use.
#
# It used to be built per call. /health/ready is PUBLIC and is exactly the
# kind of endpoint a load balancer polls every few seconds, so a
# per-call client meant a new connection pool every few seconds, none of
# them ever closed - a readiness check that eventually exhausts the thing
# it is checking.
#
# The cached client is still a LIVE check: redis-py reconnects on demand,
# so pinging it detects an outage and detects recovery.
_redis_client = None
_redis_client_url: Optional[str] = None


async def _check_redis() -> Dict[str, Any]:
    global _redis_client, _redis_client_url

    url = os.getenv("REDIS_URL")
    if not url:
        # Optional by design: the AI limiter degrades to per-worker
        # counting without it. Saying "unconfigured" rather than
        # "degraded" keeps a deployment that never wanted Redis from
        # looking permanently broken.
        return _check("redis", UNCONFIGURED, reason="not_configured")
    try:
        import redis.asyncio as aioredis  # type: ignore

        if _redis_client is None or _redis_client_url != url:
            _redis_client = aioredis.from_url(url, decode_responses=True)
            _redis_client_url = url
        await _redis_client.ping()
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


# How long a collector may be silent before it is treated as stopped.
#
# Deliberately generous: this is a "has it died" check, not a latency
# target. `ok` records the outcome of the LAST run and says nothing about
# whether another one ever happened, so a worker that stopped keeps
# reporting ok:true forever - which is how a sales outage emptied every
# market surface with nothing anywhere saying why.
#
# This is the default for the two CONTINUOUS collectors
# (futgg_bulk_price_sync, futgg_sales_sync - both run_forever() loops
# that cycle every few minutes). futgg_player_sync is a different shape
# entirely - see COLLECTOR_SILENT_HOURS_BY_WORKER below - and must not
# use this default.
COLLECTOR_SILENT_HOURS = float(os.getenv("READINESS_COLLECTOR_SILENT_HOURS", "6"))

# futgg_player_sync is a one-shot daily discovery job (Procfile: "Run
# daily as a Cron Job to discover and classify newly-released cards"),
# not a continuous service - a 14-hour gap since its last run is the
# middle of an ordinary day, not an outage. Applying the 6h continuous-
# collector threshold to it produced a false collector_silent for roughly
# 18 of every 24 hours, confirmed live in production (age_hours=14.5,
# detail all zeros - a normal mid-day reading, not a stuck run). 30h
# covers the 24h cadence plus slack for a run that's running a bit late,
# without still being cheap on a genuine multi-day outage.
PLAYER_SYNC_SILENT_HOURS = float(os.getenv("READINESS_PLAYER_SYNC_SILENT_HOURS", "30"))

# Collectors that must be reporting for the market surfaces to work, each
# with the silence threshold that actually matches its own cadence - a
# single flat number across a daily one-shot job and two continuous
# services is wrong for whichever one it wasn't tuned for.
#
# Checked by name rather than by iterating whatever rows happen to exist,
# because the failure that motivated this check was an ABSENT row, not a
# stale one: the live sales collector wrote no heartbeat at all, so a
# loop over the returned rows had nothing to find and reported nothing
# wrong. A collector that never checks in is the same outage as one that
# stopped, and has to read as one.
COLLECTOR_SILENT_HOURS_BY_WORKER: Dict[str, float] = {
    "futgg_player_sync": PLAYER_SYNC_SILENT_HOURS,
    "futgg_bulk_price_sync": COLLECTOR_SILENT_HOURS,
    "futgg_sales_sync": COLLECTOR_SILENT_HOURS,
}
EXPECTED_COLLECTORS = tuple(COLLECTOR_SILENT_HOURS_BY_WORKER)

# Sales older than this are rejected by the engine outright
# (futgg_config.reject_sales_older_than_minutes, 72h). A collector silent
# for longer than this has already emptied the feed, whatever its
# heartbeat says.
SALES_FRESHNESS_HOURS = float(os.getenv("READINESS_SALES_FRESHNESS_HOURS", "72"))


async def _check_market_data(pool) -> Dict[str, Any]:
    """Are the collectors that feed every market surface still running?

    This is the check whose absence let a three-week sales outage look
    like a quiet market. Confidence in the engine is a geometric blend, so
    a single dead input - no sales, or sales past the freshness limit -
    zeroes every card's score and empties Home, Opportunities and Trade
    Finder at once, with no error anywhere.
    """
    if pool is None:
        return _check("market_data", UNAVAILABLE, reason="pool_missing")
    try:
        from app.services.market_data_provider import FutggMarketDataProvider

        summary = await FutggMarketDataProvider(pool).get_freshness_summary()
    except Exception:
        log.warning("readiness: could not read market freshness", exc_info=True)
        return _check("market_data", DEGRADED, reason="freshness_unreadable")

    now = datetime.now(timezone.utc)
    beats = {str(b.get("worker")): b for b in (summary.get("heartbeats") or [])}
    silent: List[str] = []
    failing: List[str] = []
    for worker in EXPECTED_COLLECTORS:
        beat = beats.get(worker)
        if beat is None:
            silent.append(f"{worker} (never reported)")
            continue
        last = beat.get("last_run_at")
        if last is None:
            silent.append(worker)
            continue
        hours = (now - last).total_seconds() / 3600
        threshold = COLLECTOR_SILENT_HOURS_BY_WORKER.get(worker, COLLECTOR_SILENT_HOURS)
        if hours > threshold:
            # The worker name and its age. Not the detail string - that
            # carries scrape internals and is not an operator's first
            # question.
            silent.append(f"{worker} ({hours:.0f}h)")
        elif not beat.get("ok", True):
            # Running, on schedule, and not keeping up. A collector
            # throttled down to near-zero throughput never goes silent -
            # it checks in punctually while the data behind it drains.
            failing.append(worker)

    priced = int(summary.get("cards_priced") or 0)
    stale = int(summary.get("cards_stale") or 0)
    stale_sales = sum((summary.get("stale_sales_by_priority_tier") or {}).values())

    detail: Dict[str, Any] = {
        "cards_priced": priced,
        "cards_stale": stale,
        "cards_with_stale_sales": stale_sales,
    }
    if silent:
        detail["silent_collectors"] = silent
    if failing:
        detail["failing_collectors"] = failing

    if priced == 0:
        return _check("market_data", UNAVAILABLE, reason="no_priced_cards", detail=detail)
    # A silent collector is the earliest and clearest signal, and it is
    # the one that was missing. Reported ahead of the staleness counts it
    # will go on to cause.
    if silent:
        return _check("market_data", DEGRADED, reason="collector_silent", detail=detail)
    if failing:
        return _check("market_data", DEGRADED, reason="collector_failing", detail=detail)
    if stale_sales > priced * 0.5:
        return _check("market_data", DEGRADED, reason="sales_data_stale", detail=detail)
    if stale > priced * 0.5:
        return _check("market_data", DEGRADED, reason="price_data_stale", detail=detail)
    return _check("market_data", OK, detail=detail)

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
        await _check_market_data(core_pool),
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
