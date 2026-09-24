"""Authenticated HTTPS bridge from the Railway FUT.GG collector."""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.services import futgg_content

router = APIRouter(tags=["internal-content-sync"])


@router.post("/internal/futgg-content")
async def ingest_futgg_content(
    payload: dict[str, Any],
    request: Request,
    x_content_sync_token: str | None = Header(None),
) -> dict[str, Any]:
    expected = (os.getenv("FUTGG_CONTENT_SYNC_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "content sync is not configured")
    if not x_content_sync_token or not hmac.compare_digest(x_content_sync_token, expected):
        raise HTTPException(401, "invalid content sync token")
    pool = getattr(request.app.state, "player_pool", None)
    if pool is None:
        raise HTTPException(503, "player pool not ready")
    try:
        counts = await futgg_content.ingest_content_payload(pool, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, **counts}
