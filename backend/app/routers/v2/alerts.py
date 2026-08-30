# app/routers/v2/alerts.py
#
# Fired-alert feed + dismissal. Distinct from main.py's
# /api/watchlist-alerts endpoints, which manage alert RULES
# (watchlist_alerts): this router manages the notifications those rules
# produced (alerts_log).
#
# That distinction is the whole reason "deleting an alert didn't make it
# go away": the only delete endpoint that existed removed the rule, so
# the notification the user was actually looking at stayed in the feed
# and stayed counted by the bell badge - there was no way to remove it,
# on the frontend or the backend. See migration 072.
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from app.db import get_core_pool, get_player_pool, get_watchlist_pool
from app.services.alerts_feed import DEFAULT_FEED_LIMIT, fetch_alert_feed

router = APIRouter(tags=["v2-alerts"])
log = logging.getLogger("v2_alerts")


def _user_id(request: Request) -> str:
    user_id = (request.session or {}).get("user_id")
    if not user_id:
        raise HTTPException(401, "Not authenticated")
    return str(user_id)


def _require_json_request(request: Request) -> None:
    """Forces a CORS preflight on state-changing POSTs.

    Session cookies are SameSite=None in production (the app and the API
    are on different origins), so a cross-site POST that qualifies as a
    CORS "simple request" - no custom headers, a text/plain or
    form-encoded body - is sent WITH the cookie and takes effect before
    any CORS check applies; the browser only blocks reading the response.
    Requiring application/json disqualifies the request from being
    simple, so the browser must preflight it, and the preflight is
    checked against the origin allowlist.

    DELETE /alerts/{id} needs no equivalent guard: a non-simple method
    always preflights.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(415, "Expected a JSON request")


@router.get("/alerts")
async def list_alerts(
    request: Request,
    limit: int = Query(DEFAULT_FEED_LIMIT, ge=1, le=100),
) -> Dict[str, Any]:
    """Undismissed alerts, newest first, with the player resolved and
    repeats collapsed. `unreadCount` is the badge number and is a real
    count of the underlying rows, not the length of `items`."""
    user_id = _user_id(request)
    return await fetch_alert_feed(
        watchlist_pool=await get_watchlist_pool(),
        core_pool=await get_core_pool(),
        player_pool=await get_player_pool(),
        user_id=user_id,
        limit=limit,
    )


@router.delete("/alerts/{alert_id}")
async def dismiss_alert(alert_id: int, request: Request) -> Dict[str, Any]:
    """Dismiss one fired alert. A soft delete (dismissed_at) rather than a
    DELETE: alerts_log is also the audit trail for what we notified a user
    about and when, and losing that to a UI tidy-up would make a support
    question like "why did I get pinged at 3am" unanswerable.

    Idempotent by design - dismissing an already-dismissed alert is a
    success, not a 404. A user double-tapping the X, or two tabs racing,
    must not produce an error toast for something that did happen.
    """
    user_id = _user_id(request)
    pool = await get_watchlist_pool()
    async with pool.acquire() as conn:
        owned = await conn.fetchval(
            "SELECT 1 FROM alerts_log WHERE id = $1 AND user_id = $2", alert_id, user_id
        )
        if not owned:
            raise HTTPException(404, "Alert not found")
        await conn.execute(
            "UPDATE alerts_log SET dismissed_at = COALESCE(dismissed_at, now()), read_at = COALESCE(read_at, now()) WHERE id = $1 AND user_id = $2",
            alert_id, user_id,
        )
    return {"ok": True, "id": alert_id}


@router.post("/alerts/dismiss-all")
async def dismiss_all_alerts(request: Request) -> Dict[str, Any]:
    _require_json_request(request)
    user_id = _user_id(request)
    pool = await get_watchlist_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE alerts_log SET dismissed_at = now(), read_at = COALESCE(read_at, now()) WHERE user_id = $1 AND dismissed_at IS NULL",
            user_id,
        )
    return {"ok": True, "dismissed": int(result.rsplit(" ", 1)[-1]) if result.startswith("UPDATE") else 0}


@router.post("/alerts/read")
async def mark_alerts_read(request: Request) -> Dict[str, Any]:
    """Clears the badge without removing anything from the feed - opening
    the panel means "I've seen these", not "delete these"."""
    _require_json_request(request)
    user_id = _user_id(request)
    pool = await get_watchlist_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE alerts_log SET read_at = now() WHERE user_id = $1 AND dismissed_at IS NULL AND read_at IS NULL",
            user_id,
        )
    return {"ok": True, "unreadCount": 0}
