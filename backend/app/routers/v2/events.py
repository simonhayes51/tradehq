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
    # Emitted by the BROWSER on the page Stripe redirects back to, so it
    # means "returned from checkout", not "paid". The authoritative
    # revenue event is subscription_confirmed below, which only trusted
    # server-side code writes.
    "checkout_completed",
    "checkout_cancelled",
    # Server-only. Written by the Stripe webhook handlers, never accepted
    # from a browser - see reject_client_source below.
    "subscription_confirmed",
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

# Events only trusted server-side code may write. A browser POSTing one
# of these is claiming a fact it is not in a position to know - anyone can
# reach the tracking endpoint, and "we were paid" must never be one of the
# things they can assert.
SERVER_ONLY_EVENTS = frozenset({"subscription_confirmed"})

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
    if body.event in SERVER_ONLY_EVENTS:
        # Dropped, not stored with source='client': a row that exists at
        # all would still have to be filtered out of every revenue query
        # forever.
        log.warning("funnel: client attempted to post server-only event %s", body.event)
        return {"recorded": False, "reason": "server_only_event"}

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
                INSERT INTO funnel_events (event, user_id, props, source)
                VALUES ($1, $2, $3::jsonb, 'client')
                """,
                body.event,
                user_id,
                __import__("json").dumps(sanitise(body.props)),
            )
        _note_success()
    except Exception as exc:
        # Includes "table does not exist" before migration 075 is applied.
        # Counted as well as logged: this endpoint answers 200 either way,
        # so without a counter the difference between "nobody is using the
        # product" and "we have recorded nothing for a week" is invisible.
        _note_failure(exc)
        log.warning("funnel event not recorded (event=%s)", body.event, exc_info=True)
        return {"recorded": False, "reason": "unavailable"}

    return {"recorded": True}


# ---------------------------------------------------------------------------
# Server-side recording, and knowing when recording stops working
# ---------------------------------------------------------------------------
#
# The endpoint above always answers 200 by design: instrumentation must
# never fail the action that triggered it. The cost of that decision is
# that total, silent loss of analytics looks exactly like nobody using the
# product. These counters are how the difference becomes visible.

_failure_counter = {"ok": 0, "failed": 0, "last_error_at": None, "last_error_kind": None}


def _note_success() -> None:
    _failure_counter["ok"] += 1


def _note_failure(exc: BaseException) -> None:
    from datetime import datetime, timezone

    _failure_counter["failed"] += 1
    _failure_counter["last_error_at"] = datetime.now(timezone.utc).isoformat()
    # The exception CLASS, never its message: an asyncpg error text can
    # carry a connection string, and this is read by an admin page.
    _failure_counter["last_error_kind"] = type(exc).__name__


def tracking_health() -> Dict[str, Any]:
    """Whether funnel recording is actually working, for the admin view
    and the health check.

    Process-local and reset by a restart. That is honest for what it is -
    a smoke signal, not a metric store - and it is enough to answer "have
    the last N attempts been failing", which is the question.
    """
    ok = _failure_counter["ok"]
    failed = _failure_counter["failed"]
    total = ok + failed
    return {
        "attempts": total,
        "recorded": ok,
        "failed": failed,
        "failure_rate": round(failed / total, 4) if total else 0.0,
        # Deliberately not a bare boolean: "nothing has been tried yet" and
        # "everything is failing" are different states and an admin needs
        # to tell them apart.
        "status": (
            "unknown" if total == 0
            else "failing" if failed and ok == 0
            else "degraded" if failed else "ok"
        ),
        "last_error_at": _failure_counter["last_error_at"],
        "last_error_kind": _failure_counter["last_error_kind"],
    }


def reset_tracking_health() -> None:
    """Tests only."""
    _failure_counter.update({"ok": 0, "failed": 0, "last_error_at": None, "last_error_kind": None})


async def record_server_event(
    event: str,
    user_id: Optional[str],
    props: Optional[Dict[str, Any]] = None,
    *,
    pool=None,
) -> bool:
    """Record a funnel event from trusted server-side code.

    The one way a SERVER_ONLY_EVENTS row can be written. Same allow-list
    on the properties, because "the server wrote it" is not a reason to
    start storing free text.

    Never raises: a Stripe webhook must not fail because a reporting row
    could not be written. The failure is counted and logged instead.
    """
    if event not in ALLOWED_EVENTS:
        log.warning("funnel: refusing to record unknown server event %s", event)
        return False
    try:
        if pool is None:
            from app.db import get_core_pool

            pool = await get_core_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO funnel_events (event, user_id, props, source)
                VALUES ($1, $2, $3::jsonb, 'server')
                """,
                event,
                str(user_id) if user_id else None,
                __import__("json").dumps(sanitise(props or {})),
            )
        _note_success()
        return True
    except Exception as exc:
        _note_failure(exc)
        log.warning("funnel: could not record server event %s", event, exc_info=True)
        return False
