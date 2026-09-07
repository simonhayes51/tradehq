"""Admin report for the read-only information-value SalesSync shadow.

This endpoint reports observations only. It does not alter collector cadence,
request limits, or selection. Its purpose is to provide the evidence required
before any future reallocation of the existing detailed-sales request budget.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.routers.admin import require_admin

router = APIRouter(tags=["v2-sales-shadow"])


def _pool(request: Request):
    return getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "pool", None)


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


@router.get("/market/sales-information-shadow")
async def sales_information_shadow(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    _admin=Depends(require_admin),
) -> Dict[str, Any]:
    pool = _pool(request)
    if pool is None:
        raise HTTPException(503, "Core database pool unavailable")

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT observed_at, due_count, triggered_count,
                       triggered_new_count, user_driven_count,
                       held_position_count, reason_counts, selector_version
                FROM futgg_trigger_shadow_observations
                WHERE observed_at >= now() - ($1::int * interval '1 hour')
                ORDER BY observed_at
                """,
                int(hours),
            )
    except Exception as exc:
        if exc.__class__.__name__ in {"UndefinedTableError", "UndefinedColumnError"}:
            return {
                "status": "warming",
                "hours": int(hours),
                "cycles": 0,
                "reason": "shadow_schema_not_ready",
            }
        raise

    if not rows:
        return {
            "status": "warming",
            "hours": int(hours),
            "cycles": 0,
            "reason": "no_shadow_observations_yet",
        }

    reasons: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    for row in rows:
        reasons.update({str(k): int(v) for k, v in _json_object(row["reason_counts"]).items()})
        versions[str(row["selector_version"])] += 1

    cycles = len(rows)
    due_total = sum(int(row["due_count"] or 0) for row in rows)
    triggered_total = sum(int(row["triggered_new_count"] or 0) for row in rows)
    user_total = sum(int(row["user_driven_count"] or 0) for row in rows)
    held_total = sum(int(row["held_position_count"] or 0) for row in rows)

    return {
        "status": "ok",
        "hours": int(hours),
        "cycles": cycles,
        "first_observed_at": rows[0]["observed_at"].isoformat(),
        "last_observed_at": rows[-1]["observed_at"].isoformat(),
        "live_due_requests_observed": due_total,
        "shadow_priority_events": triggered_total,
        "avg_live_due_per_cycle": round(due_total / cycles, 2),
        "avg_shadow_priority_per_cycle": round(triggered_total / cycles, 2),
        "user_driven_shadow_events": user_total,
        "user_driven_share_pct": round(100.0 * user_total / triggered_total, 2) if triggered_total else 0.0,
        "held_position_shadow_events": held_total,
        "reason_counts": dict(reasons.most_common()),
        "selector_versions": dict(versions),
        "live_scheduler_changed": False,
        "extra_futgg_sales_requests": 0,
        "decision_note": (
            "Use this report to judge which signals deserve slots inside the existing sales budget. "
            "Do not infer that shadow_priority_events are extra capacity."
        ),
    }
