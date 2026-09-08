from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.routers.admin import require_admin
from app.services.sbc_market_reinforcement import reinforcement_shadow_report

router = APIRouter(tags=["v2-sbc-reinforcement"])


def _pool(request: Request):
    return getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "pool", None)


@router.get("/market/sbc-reinforcement-shadow")
async def sbc_reinforcement_shadow(
    request: Request,
    lookback_hours: int = Query(72, ge=6, le=168),
    limit: int = Query(40, ge=1, le=100),
    _admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = _pool(request)
    if pool is None:
        raise HTTPException(503, "Core database pool unavailable")
    return await reinforcement_shadow_report(
        pool,
        lookback_hours=lookback_hours,
        limit=limit,
    )
