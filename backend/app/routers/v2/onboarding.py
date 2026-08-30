# app/routers/v2/onboarding.py
#
# Onboarding state for a new member. Short, skippable, and persisted -
# see migration 074 for why persistence is the point and why only two
# preferences are stored.
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import get_core_pool

router = APIRouter(tags=["v2-onboarding"])
log = logging.getLogger("onboarding")


def _user_id(request: Request) -> str:
    user_id = (request.session or {}).get("user_id")
    if not user_id:
        raise HTTPException(401, "Not authenticated")
    return str(user_id)


class OnboardingUpdate(BaseModel):
    # Bounded rather than free: a budget is a coin amount, and validating
    # it here means the Trade Finder can use it without re-checking.
    coin_budget: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    wants_price_alerts: Optional[bool] = None
    skipped: bool = False


@router.get("/onboarding")
async def get_onboarding(request: Request) -> Dict[str, Any]:
    """Whether this member still needs the welcome flow.

    A missing row means "new member" - the table is written on first
    completion, not on signup, so nothing has to backfill existing
    accounts. Existing members simply see it once.
    """
    user_id = _user_id(request)
    try:
        pool = await get_core_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT completed_at, skipped, coin_budget, wants_price_alerts
                   FROM member_onboarding WHERE user_id = $1""",
                user_id,
            )
    except Exception:
        # Never block someone from using the app because we could not read
        # a preferences row. Treating a failure as "already done" is the
        # safe direction: the worst case is a member who does not see the
        # welcome flow, not one trapped in it.
        log.warning("onboarding: read failed", exc_info=True)
        return {"needsOnboarding": False, "degraded": True}

    if row is None:
        return {"needsOnboarding": True, "coinBudget": None, "wantsPriceAlerts": None}
    return {
        "needsOnboarding": row["completed_at"] is None,
        "skipped": bool(row["skipped"]),
        "coinBudget": row["coin_budget"],
        "wantsPriceAlerts": row["wants_price_alerts"],
    }


@router.post("/onboarding")
async def complete_onboarding(body: OnboardingUpdate, request: Request) -> Dict[str, Any]:
    """Finish or skip. Both set completed_at - a member who skipped has
    made a decision, and asking again next login would ignore it."""
    user_id = _user_id(request)
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO member_onboarding
                (user_id, completed_at, skipped, coin_budget, wants_price_alerts, updated_at)
            VALUES ($1, now(), $2, $3, $4, now())
            ON CONFLICT (user_id) DO UPDATE SET
                completed_at = now(),
                skipped = EXCLUDED.skipped,
                -- COALESCE so re-running the flow, or a partial save,
                -- cannot blank a preference the member already set.
                coin_budget = COALESCE(EXCLUDED.coin_budget, member_onboarding.coin_budget),
                wants_price_alerts = COALESCE(EXCLUDED.wants_price_alerts, member_onboarding.wants_price_alerts),
                updated_at = now()
            """,
            user_id, bool(body.skipped), body.coin_budget, body.wants_price_alerts,
        )
    return {"ok": True, "needsOnboarding": False}
