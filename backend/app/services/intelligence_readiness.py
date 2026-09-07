"""Add persisted FUT.GG intelligence health to the existing readiness report.

Kept as a small bootstrap-installed wrapper rather than rewriting the mature
readiness.py service. main.py and the admin reporting endpoint import
`readiness` at request time, so replacing that module attribute makes both
surfaces see the same additional check while preserving every existing check.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("intelligence_readiness")

STALE_AFTER_MINUTES = 25.0
_installed = False


async def _check_intelligence_state(pool, readiness_module):
    if pool is None:
        return readiness_module._check(
            "intelligence_state",
            readiness_module.DEGRADED,
            reason="pool_missing",
        )

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*) AS current_rows, max(evaluated_at) AS newest_evaluated_at
                FROM futgg_current_intelligence
                """
            )
            player_pool = await conn.fetchval(
                "SELECT count(*) FROM futgg_players WHERE is_active AND is_tradeable IS DISTINCT FROM FALSE"
            )
    except Exception:
        # Migration 080 may not have landed yet, or the table may be briefly
        # unreadable. Market pages still have conservative fallback paths, so
        # this is degraded rather than load-balancer-fatal.
        log.warning("readiness: current intelligence unreadable", exc_info=True)
        return readiness_module._check(
            "intelligence_state",
            readiness_module.DEGRADED,
            reason="unreadable",
        )

    current_rows = int(row["current_rows"] or 0)
    pool_size = int(player_pool or 0)
    newest = row["newest_evaluated_at"]
    detail = {
        "current_rows": current_rows,
        "player_pool": pool_size,
        "coverage_pct": round(100.0 * current_rows / pool_size, 2) if pool_size else None,
    }

    if current_rows == 0 or newest is None:
        return readiness_module._check(
            "intelligence_state",
            readiness_module.DEGRADED,
            reason="no_current_state",
            detail=detail,
        )

    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - newest).total_seconds() / 60.0
    detail["newest_age_minutes"] = round(age_minutes, 1)

    if age_minutes > STALE_AFTER_MINUTES:
        return readiness_module._check(
            "intelligence_state",
            readiness_module.DEGRADED,
            reason="scanner_state_stale",
            detail=detail,
        )

    return readiness_module._check(
        "intelligence_state",
        readiness_module.OK,
        detail=detail,
    )


def install_intelligence_readiness() -> None:
    global _installed
    if _installed:
        return

    from app.services import readiness as readiness_module

    original = readiness_module.readiness

    async def wrapped(core_pool, watchlist_pool=None):
        report = await original(core_pool, watchlist_pool)
        checks = list(report.get("checks") or [])
        checks.append(await _check_intelligence_state(core_pool, readiness_module))
        return {"status": readiness_module.overall(checks), "checks": checks}

    readiness_module.readiness = wrapped
    _installed = True
    log.info("installed current-intelligence readiness check")
