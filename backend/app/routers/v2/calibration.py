"""Admin-only calibration evidence and decision audit endpoints.

These routes can analyse outcomes and record a human decision. They cannot
mutate live engine configuration or promote an engine version.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.routers.admin import require_admin
from app.services.futgg_calibration import calibration_report
from app.services.futgg_calibration_decisions import list_decisions, record_decision
from app.services.futgg_config import ENGINE_VERSION, GRADER_VERSION

router = APIRouter(tags=["v2-calibration"])


def _pool(request: Request):
    return getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "pool", None)


class CalibrationDecisionIn(BaseModel):
    parameter: str = Field(min_length=1, max_length=128)
    from_engine_version: str = Field(default=ENGINE_VERSION, min_length=1, max_length=64)
    to_engine_version: Optional[str] = Field(default=None, max_length=64)
    old_value: Any = None
    new_value: Any = None
    horizon: Optional[str] = None
    window_days: Optional[int] = Field(default=None, ge=1, le=730)
    lower_bucket: Optional[str] = Field(default=None, max_length=128)
    higher_bucket: Optional[str] = Field(default=None, max_length=128)
    lower_sample: Optional[int] = Field(default=None, ge=0)
    higher_sample: Optional[int] = Field(default=None, ge=0)
    profitable_delta_pp: Optional[float] = None
    p_value: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    adjusted_alpha: Optional[float] = Field(default=None, gt=0.0, le=1.0)
    config_fingerprint: Optional[str] = Field(default=None, max_length=128)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    status: str = "proposed"
    source_commit: Optional[str] = Field(default=None, max_length=128)


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


@router.get("/market/calibration/decisions")
async def calibration_decision_history(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    _admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = _pool(request)
    if pool is None:
        raise HTTPException(503, "Core database pool unavailable")
    items = await list_decisions(pool, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/market/calibration/decisions")
async def create_calibration_decision(
    body: CalibrationDecisionIn,
    request: Request,
    _admin=Depends(require_admin),
) -> Dict[str, Any]:
    """Record evidence/rationale only. Does not alter FUT.GG configuration."""
    pool = _pool(request)
    if pool is None:
        raise HTTPException(503, "Core database pool unavailable")
    try:
        item = await record_decision(pool, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"item": item, "engine_changed": False}
