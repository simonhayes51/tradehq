# app/routers/admin_reporting.py
#
# Admin-only reporting: the conversion funnel, and what the assistant
# costs.
#
# Two rules hold for everything in this file.
#
# 1. AGGREGATED IN SQL. Raw funnel rows and raw AI usage rows are a
#    per-account behavioural record; the questions being asked here are
#    about totals. Nothing sends per-row data to a browser.
#
# 2. ADMIN ONLY, through the entitlement system that already exists
#    (app/routers/admin.require_admin), not a second check of its own.
#    These endpoints are not in customer navigation and are not
#    discoverable from it.
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_core_pool
from app.routers.admin import require_admin
from app.services import ai_usage, funnel

router = APIRouter(prefix="/api/admin", tags=["admin-reporting"])
log = logging.getLogger("admin_reporting")


@router.get("/ai-usage")
async def ai_usage_summary(
    days: int = Query(30, ge=1, le=365),
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    """What the assistant has cost over a window.

    The number that actually decides whether the daily limit is right is
    not the average member but the heaviest one, so that is reported
    alongside the totals. See docs/AI_CHAT_COST.md.
    """
    try:
        return await ai_usage.usage_summary(days)
    except Exception:
        log.warning("ai usage summary failed", exc_info=True)
        # Degraded, not broken: the table may not exist yet (migration
        # 076). Say which, without leaking the exception text.
        raise HTTPException(
            503,
            {
                "error": "usage_unavailable",
                "message": "AI usage reporting is not available. Check migration 076 has been applied.",
            },
        )


@router.get("/funnel")
async def conversion_funnel(
    window: str = Query("30d", description="today|7d|30d|90d - ignored when start is given"),
    start: Optional[date] = Query(None, description="custom range start (UTC date)"),
    end: Optional[date] = Query(None, description="custom range end (UTC date, defaults to today)"),
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    """The conversion funnel over a period.

    Aggregated in SQL - no raw funnel rows ever reach a browser. The
    payload carries its own notes about what each number is, because the
    two easiest mistakes here are reading anonymous event counts as people
    and reading a browser event as revenue.
    """
    if window not in funnel.PRESET_WINDOWS and start is None:
        raise HTTPException(
            400,
            {
                "error": "bad_window",
                "message": f"window must be one of {sorted(funnel.PRESET_WINDOWS)}, or pass start/end.",
            },
        )
    try:
        report = await funnel.conversion_report(window, start, end)
    except Exception:
        log.warning("funnel report failed", exc_info=True)
        raise HTTPException(
            503,
            {
                "error": "funnel_unavailable",
                "message": "Funnel reporting is not available. Check migrations 075 and 077 have been applied.",
            },
        )
    # Whether the numbers can be trusted at all. A funnel that reads zero
    # because nothing is being recorded looks identical to a funnel that
    # reads zero because nobody signed up, and an admin needs to be able
    # to tell those apart without reading logs.
    from app.routers.v2.events import tracking_health

    report["tracking"] = tracking_health()
    return report
