
from __future__ import annotations
from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from app.utils.timebox import now_utc

router = APIRouter()

@router.get("/api/events/next")
async def next_event(request) -> Dict[str, Any]:
    # Not mounted anywhere (main.py has its own /api/events/next handler
    # registered directly) - kept honest anyway rather than left as a
    # dead copy of the fabricated-fallback bug that route just had fixed.
    _NO_EVENT = {"name": None, "kind": None, "start_at": None, "confidence": None}
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return _NO_EVENT
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT name, kind, start_at, confidence FROM events WHERE start_at > $1 ORDER BY start_at ASC LIMIT 1",
            now_utc()
        )
    if row:
        return {"name": row["name"], "kind": row["kind"], "start_at": row["start_at"].isoformat(), "confidence": row["confidence"]}
    return _NO_EVENT
