# app/routers/v2/events.py
#
# The internal funnel-event endpoint.
#
# There is no analytics provider in this project and this does not add
# one: adding a third party is a privacy and contractual decision, not an
# implementation detail. This is the small internal interface instead -
# one endpoint, one table (migration 075), and one named vocabulary shared
# with frontend/src/lib/analytics.js.
#
# The privacy rule is enforced HERE, not only in the client. The client
# already applies the same allow-list, but a client-side filter is a
# convenience, not a control: anything can POST to this route. So the
# event name must be one we recognise, the property keys must be
# allow-listed, and the values are bounded - and the extras are dropped
# rather than the request rejected, because instrumentation must never
# become a way to break a page.
#
# Deliberately never recorded: player searches, chat text, email
# addresses, Discord identities, IP addresses, user agents. `user_id` is
# the internal id, is nullable, and a signed-out funnel step records no
# identity at all.
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.db import get_core_pool

router = APIRouter(tags=["v2-events"])
log = logging.getLogger("funnel_events")

# Must match EVENTS in frontend/src/lib/analytics.js. An unrecognised name
# is dropped rather than stored: an open-ended event table is how a
# "small internal interface" becomes an uncontrolled one.
ALLOWED_EVENTS = frozenset({
    "signup_viewed",
    "signup_started",
    "signup_completed",
    "onboarding_started",
    "onboarding_completed",
    "onboarding_skipped",
    "free_opportunity_viewed",
    "free_limit_reached",
    "upgrade_prompt_viewed",
    "pricing_viewed",
    "checkout_started",
    "checkout_completed",
    "checkout_cancelled",
    "first_watchlist_card_added",
    "first_alert_created",
    "first_portfolio_trade_added",
})

# Must match ALLOWED_PROPS in the same client module. None of these can
# carry free text a user typed.
ALLOWED_PROPS = frozenset({
    "tier",      # "free" | "elite" - never a user id
    "surface",   # which screen
    "feature",   # which gated feature triggered a prompt
    "plan",      # "monthly" | "season"
    "step",      # onboarding step index
    "count",     # a small integer
    "reason",    # a short enum-ish string
})

_MAX_STRING = 40
_MAX_NUMBER = 10 ** 9


class TrackRequest(BaseModel):
    event: str = Field(max_length=64)
    props: Dict[str, Any] = Field(default_factory=dict)


def sanitise(props: Dict[str, Any]) -> Dict[str, Any]:
    """Allow-listed keys, bounded values. Anything else is dropped.

    Subtractive filtering would mean a new field on the client silently
    becoming a new stored field here. The whole reason this function
    exists is that the privacy rule should be readable in one place and
    fail closed.
    """
    out: Dict[str, Any] = {}
    for key, value in (props or {}).items():
        if key not in ALLOWED_PROPS:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            if -_MAX_NUMBER <= value <= _MAX_NUMBER:
                out[key] = value
        elif isinstance(value, str):
            out[key] = value[:_MAX_STRING]
    return out


@router.post("/events/track")
async def track_event(body: TrackRequest, request: Request) -> Dict[str, Any]:
    """Record one funnel event.

    Always returns 200. This is instrumentation: a rejected event must
    never surface to a customer or fail the action that triggered it, so
    an unknown name, a storage failure or a missing table are all
    "recorded: false" rather than an error.
    """
    if body.event not in ALLOWED_EVENTS:
        return {"recorded": False, "reason": "unknown_event"}

    user_id: Optional[str] = None
    try:
        session = request.session or {}
        raw = session.get("user_id")
        user_id = str(raw) if raw else None
    except Exception:
        # No SessionMiddleware on this app instance (tests, or a stripped
        # deployment). An anonymous event is still a useful event.
        user_id = None

    try:
        pool = await get_core_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO funnel_events (event, user_id, props)
                VALUES ($1, $2, $3::jsonb)
                """,
                body.event,
                user_id,
                __import__("json").dumps(sanitise(body.props)),
            )
    except Exception:
        # Includes "table does not exist" before migration 075 is applied.
        log.debug("funnel event not recorded", exc_info=True)
        return {"recorded": False, "reason": "unavailable"}

    return {"recorded": True}
