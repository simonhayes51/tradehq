# app/routers/v2/sbc.py
#
# Read-only endpoints over market_events/sbc_details/sbc_challenges/
# event_market_impact (migrations 018/019). The impact route is gated
# via an INLINE require_feature(...) call, not a route-decorator
# Depends() - these functions aren't called directly elsewhere today,
# but the inline form is used consistently across v2 so that never
# becomes a silent bypass risk later (a Depends() gate is only enforced
# by FastAPI's own request pipeline; a direct Python call to the
# function - like app/routers/v2/players.py's player_summary() already
# does for several of these - skips it entirely).
from __future__ import annotations

from typing import Any, Dict, List

import asyncpg

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth.entitlements import require_feature
from app.services import futgg_content
from app.services import market_events as me

router = APIRouter(tags=["v2-sbc"])


def _player_pool(request: Request):
    pool = getattr(request.app.state, "player_pool", None)
    if pool is None:
        raise HTTPException(503, "player pool not ready")
    return pool


@router.get("/sbc/events")
async def list_sbc_events(
    request: Request,
    limit: int = Query(60, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=80),
    category: str | None = Query(None, max_length=40),
    status: str = Query("live", pattern="^(live|expired|all)$"),
    sort: str = Query("newest", pattern="^(newest|expiring|cheapest|costliest)$"),
) -> Dict[str, Any]:
    pool = _player_pool(request)
    try:
        return await futgg_content.list_sbc_events(
            pool, limit=limit, offset=offset, search=q, category=category,
            status=status, sort=sort,
        )
    except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError):
        # Safe deploy-order fallback while migration 100 catches up.
        events = await me.get_events(pool, kind="sbc", limit=limit, offset=offset)
        return {"items": events, "count": len(events), "categories": []}


@router.get("/sbc/solution-exposure")
async def get_solution_exposure(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
) -> Dict[str, Any]:
    pool = _player_pool(request)
    items = await futgg_content.solution_exposure(pool, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/sbc/events/{event_id}")
async def get_sbc_event(event_id: int, request: Request) -> Dict[str, Any]:
    pool = _player_pool(request)
    event = await me.get_event(pool, event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    return event


@router.get("/sbc/events/{event_id}/impact")
async def get_sbc_event_impact(event_id: int, request: Request) -> Dict[str, Any]:
    await require_feature("sbc_impact_predictions")(request)
    pool = _player_pool(request)
    event = await me.get_event(pool, event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    impact = await me.get_event_impact(pool, event_id)
    return {"event_id": event_id, "items": impact, "count": len(impact)}
