"""Admin-only evidence report for FUT.GG engine calibration.

This endpoint is intentionally read-only. It can say that a threshold deserves
human review; it cannot mutate configuration or promote a new engine version.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.routers.admin import require_admin
from app.services.futgg_calibration import calibration_report
from app.services.futgg_config import ENGINE_VERSION, GRADER_VERSION

router = APIRouter(tags=["v2-calibration"])


def _pool(request: Request):
    return getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "pool", None)


@router.get("/market/calibration")
async def market_calibration(
    request: Request,
    horizon: str = Query("24h", pattern="^(24h|48h|7d)$"),
    window_days: int = Query(90, ge=7, le=730),
    engine_version: str = Query(ENGINE_VERSION, min_length=1, max_length=64),
    grader_version: str = Query(GRADER_VERSION, min_length=1, max_length=64),
    min_sample: int = Query(30, ge=20, le=500),
    min_effect_pp: float = Query(5.0, ge=1.0, le=25.0),
    _admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = _pool(request)
    if pool is None:
        raise HTTPException(503, "Core database pool unavailable")
    try:
        return await calibration_report(
            pool,
            horizon=horizon,
            window_days=window_days,
            engine_version=engine_version,
            grader_version=grader_version,
            min_sample=min_sample,
            min_effect_pp=min_effect_pp,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
