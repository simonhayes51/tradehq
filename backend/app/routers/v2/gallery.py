"""Public, read-only FC27 FUT Gallery endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.services import futgg_content

router = APIRouter(tags=["v2-gallery"])


def _pool(request: Request):
    pool = getattr(request.app.state, "player_pool", None)
    if pool is None:
        raise HTTPException(503, "player pool not ready")
    return pool


@router.get("/gallery/sets")
async def list_sets(
    request: Request,
    limit: int = Query(60, ge=1, le=150),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=80),
    category: str | None = Query(None, max_length=80),
    grade: str | None = Query(None, pattern="^[SABCD]$"),
    sort: str = Query("tokens", pattern="^(tokens|cheapest|costliest|grade|name)$"),
) -> dict[str, Any]:
    return await futgg_content.list_gallery_sets(
        _pool(request), limit=limit, offset=offset, search=q,
        category=category, grade=grade, sort=sort,
    )


@router.get("/gallery/sets/{set_id}")
async def get_set(set_id: int, request: Request) -> dict[str, Any]:
    result = await futgg_content.get_gallery_set(_pool(request), set_id)
    if result is None:
        raise HTTPException(404, "Gallery set not found")
    return result


@router.get("/gallery/exposure")
async def get_exposure(
    request: Request,
    limit: int = Query(24, ge=1, le=60),
) -> dict[str, Any]:
    items = await futgg_content.gallery_exposure(_pool(request), limit=limit)
    return {"items": items, "count": len(items)}

