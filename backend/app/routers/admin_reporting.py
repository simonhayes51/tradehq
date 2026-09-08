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
from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import get_core_pool
from app.routers.admin import require_admin
from app.services import ai_usage, discord_reconcile, funnel, funnel_retention

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
    """The conversion funnel over a period."""
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
    from app.routers.v2.events import tracking_health

    report["tracking"] = tracking_health()
    return report


@router.get("/discord-reconciliation")
async def discord_reconciliation_status(admin=Depends(require_admin)) -> Dict[str, Any]:
    """Is the Discord sweep running, and is it working."""
    pool = await get_core_pool()
    history = await discord_reconcile.last_run(pool)
    if history is None:
        raise HTTPException(
            503,
            {
                "error": "reconciliation_unavailable",
                "message": "Reconciliation history is not available. Check migration 078 has been applied.",
            },
        )
    return {
        **history,
        "config": {
            "batch_size": discord_reconcile.BATCH_SIZE,
            "per_account_delay_seconds": discord_reconcile.PER_ACCOUNT_DELAY_SECONDS,
            "max_consecutive_failures": discord_reconcile.MAX_CONSECUTIVE_FAILURES,
        },
    }


@router.get("/funnel-retention")
async def funnel_retention_status(admin=Depends(require_admin)) -> Dict[str, Any]:
    """How much of the funnel table is past its retention horizon."""
    pool = await get_core_pool()
    try:
        return await funnel_retention.count_expired(pool)
    except Exception:
        log.warning("funnel retention status failed", exc_info=True)
        raise HTTPException(
            503,
            {
                "error": "retention_unavailable",
                "message": "Retention reporting is not available. Check migration 075 has been applied.",
            },
        )


@router.get("/readiness")
async def readiness_report(admin=Depends(require_admin)) -> Dict[str, Any]:
    """The full readiness picture, with reason codes."""
    from app.db import get_watchlist_pool
    from app.services.readiness import readiness

    core = await get_core_pool()
    try:
        watchlist = await get_watchlist_pool()
    except Exception:
        log.warning("readiness: watchlist pool unavailable", exc_info=True)
        watchlist = None
    return await readiness(core, watchlist)


@router.get("/why-no-trades")
async def why_no_trades(
    limit: int = Query(500, ge=50, le=2000),
    examples: int = Query(3, ge=0, le=10),
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    """Why the market feeds are empty, answered from live data."""
    from app.services.market_data_provider import FutggMarketDataProvider
    from app.services.trade_diagnosis import diagnose

    pool = await get_core_pool()
    provider = FutggMarketDataProvider(pool)
    try:
        async with pool.acquire() as conn:
            return await diagnose(conn, provider, limit=limit, examples=examples)
    except Exception:
        log.warning("trade diagnosis failed", exc_info=True)
        raise HTTPException(
            503,
            {
                "error": "diagnosis_unavailable",
                "message": "Could not read the market snapshot to diagnose the feed.",
            },
        )


# ----------------------- Upcoming promos / events -----------------------
# This is the operator-managed calendar behind Home's existing "next promo"
# read. Unlike market_events, these are future dates we intentionally enter by
# hand: promos, game launches, early access, season starts, content drops, etc.

class UpcomingEventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=48)
    start_at: datetime
    end_at: Optional[datetime] = None
    confidence: str = Field(default="confirmed", pattern="^(confirmed|heuristic)$")


class UpcomingEventUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    kind: Optional[str] = Field(None, min_length=1, max_length=48)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    confidence: Optional[str] = Field(None, pattern="^(confirmed|heuristic)$")


def _event_dict(row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "kind": row["kind"],
        "start_at": row["start_at"].isoformat() if row["start_at"] else None,
        "end_at": row["end_at"].isoformat() if row["end_at"] else None,
        "confidence": row["confidence"],
        "source": row["source"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@router.get("/upcoming-events")
async def list_upcoming_events(
    include_past: bool = Query(False),
    limit: int = Query(100, ge=1, le=250),
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = await get_core_pool()
    where = "TRUE" if include_past else "start_at >= now()"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, name, kind, start_at, end_at, confidence, source, created_at
            FROM events
            WHERE {where}
            ORDER BY start_at ASC, id ASC
            LIMIT $1
            """,
            limit,
        )
    return {"items": [_event_dict(row) for row in rows]}


@router.post("/upcoming-events")
async def create_upcoming_event(
    payload: UpcomingEventCreate,
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    if payload.end_at and payload.end_at < payload.start_at:
        raise HTTPException(400, "end_at cannot be before start_at")
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO events (name, kind, start_at, end_at, confidence, source)
            VALUES ($1, $2, $3, $4, $5, 'admin')
            RETURNING id, name, kind, start_at, end_at, confidence, source, created_at
            """,
            payload.name.strip(), payload.kind.strip().lower(), payload.start_at,
            payload.end_at, payload.confidence,
        )
    return _event_dict(row)


@router.patch("/upcoming-events/{event_id}")
async def update_upcoming_event(
    event_id: int,
    payload: UpcomingEventUpdate,
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No changes supplied")

    pool = await get_core_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, name, kind, start_at, end_at, confidence, source, created_at FROM events WHERE id=$1",
            event_id,
        )
        if not existing:
            raise HTTPException(404, "Event not found")

        name = changes.get("name", existing["name"])
        kind = changes.get("kind", existing["kind"])
        start_at = changes.get("start_at", existing["start_at"])
        end_at = changes.get("end_at", existing["end_at"])
        confidence = changes.get("confidence", existing["confidence"])
        if end_at and end_at < start_at:
            raise HTTPException(400, "end_at cannot be before start_at")

        row = await conn.fetchrow(
            """
            UPDATE events
            SET name=$2, kind=$3, start_at=$4, end_at=$5, confidence=$6, source='admin'
            WHERE id=$1
            RETURNING id, name, kind, start_at, end_at, confidence, source, created_at
            """,
            event_id, str(name).strip(), str(kind).strip().lower(), start_at, end_at, confidence,
        )
    return _event_dict(row)


@router.delete("/upcoming-events/{event_id}")
async def delete_upcoming_event(
    event_id: int,
    admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval("DELETE FROM events WHERE id=$1 RETURNING id", event_id)
    if deleted is None:
        raise HTTPException(404, "Event not found")
    return {"ok": True, "id": int(deleted)}
