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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_core_pool
from app.routers.admin import require_admin
from app.services import ai_usage

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
