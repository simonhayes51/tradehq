"""Paid feed of genuine FUT.GG intelligence state changes.

A scanner pass is not news by itself. This endpoint exposes only persisted
transitions where a previously-known card changed signal/status, e.g.
WATCH -> BUY or BUY -> AVOID. Initial table population is excluded at the write
layer, so every row here represents an actual change in the engine's conclusion.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, Request

from app.auth.entitlements import require_feature

router = APIRouter(tags=["v2-market-transitions"])
require_opportunity_feed = require_feature("opportunity_feed")


def _pool(request: Request):
    return getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "pool", None)


def _label(signal: str | None) -> str:
    if not signal:
        return "UNKNOWN"
    return str(signal).replace("_", " ").upper()


def _headline(previous_signal: str | None, new_signal: str | None) -> str:
    return f"{_label(previous_signal)} → {_label(new_signal)}"


@router.get("/market/changes")
async def recent_market_changes(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(30, ge=1, le=100),
    _ent: Dict[str, Any] = Depends(require_opportunity_feed),
) -> Dict[str, Any]:
    pool = _pool(request)
    if pool is None:
        return {"items": [], "count": 0, "status": "unavailable"}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                t.source_card_id,
                t.changed_at,
                t.previous_signal,
                t.new_signal,
                t.previous_status,
                t.new_status,
                t.current_bin AS evaluated_bin,
                t.fair_value,
                t.expected_roi,
                t.confidence_score,
                t.risk_level,
                t.trend_state,
                t.reason_codes,
                t.blocking_codes,
                COALESCE(m.display_name, m.name) AS name,
                m.rating,
                m.rarity,
                m.primary_position,
                m.current_bin AS live_current_bin,
                m.player_image_url,
                m.card_image_stored_url,
                m.card_image_url
            FROM futgg_intelligence_transitions t
            LEFT JOIN futgg_market_snapshot m
              ON m.source_card_id = t.source_card_id
            WHERE t.changed_at >= now() - ($1::int * interval '1 hour')
            ORDER BY t.changed_at DESC, t.id DESC
            LIMIT $2
            """,
            int(hours),
            int(limit),
        )

    items = []
    for row in rows:
        item = dict(row)
        item["changed_at"] = row["changed_at"].isoformat()
        item["headline"] = _headline(row["previous_signal"], row["new_signal"])
        item["fair_value"] = int(row["fair_value"]) if row["fair_value"] is not None else None
        item["expected_roi"] = float(row["expected_roi"]) if row["expected_roi"] is not None else None
        item["confidence_score"] = (
            float(row["confidence_score"]) if row["confidence_score"] is not None else None
        )
        item["composited_image_url"] = row["card_image_stored_url"] or row["card_image_url"]
        items.append(item)

    return {
        "items": items,
        "count": len(items),
        "hours": int(hours),
        "status": "ok",
    }
