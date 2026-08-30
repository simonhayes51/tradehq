# app/services/funnel.py
#
# The conversion funnel, aggregated.
#
# Collecting events without making them usable is most of the cost of
# analytics and none of the value, so this is the reporting half of
# migration 075. It is deliberately a funnel report and not a general
# analytics platform: a fixed set of questions, answered in SQL.
#
# Three rules shape every query here.
#
# 1. AGGREGATE IN THE DATABASE. Raw funnel rows are a per-account
#    behavioural record and there can be millions of them. Nothing in this
#    module returns a row-per-event to a caller.
#
# 2. COUNT PEOPLE ONCE. Someone who opens the pricing page eleven times is
#    one person considering an upgrade, not eleven. Every user-scoped
#    metric is COUNT(DISTINCT user_id).
#
# 3. SIGNED-OUT EVENTS ARE EVENTS, NOT PEOPLE. They carry no user_id by
#    design (see migration 075), and there is no privacy-safe session
#    identifier in this product. They are reported as event counts and
#    labelled as such. No fingerprinting, and no pretending a count of
#    page views is a count of humans.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("funnel")

# Windows the dashboard offers. "today" is the current UTC day, not the
# last 24 hours - a daily report that slides is impossible to compare.
PRESET_WINDOWS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
MAX_WINDOW_DAYS = 365

# Events with no account attached. Counted, never presented as people.
ANONYMOUS_EVENTS = ("signup_viewed", "signup_started")

# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------
#
# A definition in code, so the number cannot quietly mean something
# different from what the dashboard's label says.
#
# Activation = a new member did at least one thing the product is FOR.
# Signing up is not activation; neither is looking at the pricing page.
# The four below are the smallest set of "this person got value" actions
# the product actually records.
ACTIVATION_EVENTS = (
    "free_opportunity_viewed",       # read one of the day's complete picks
    "first_watchlist_card_added",
    "first_alert_created",
    "first_portfolio_trade_added",
)


def resolve_window(
    window: str = "30d",
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> Tuple[datetime, datetime, str]:
    """(from, to, label) in UTC.

    An explicit start/end wins over the preset, so a custom range is
    supported without a second code path. Both are clamped: an unbounded
    range would let one request scan the whole table.
    """
    now = datetime.now(timezone.utc)
    if start is not None:
        end = end or now.date()
        if end < start:
            start, end = end, start
        span = (end - start).days + 1
        if span > MAX_WINDOW_DAYS:
            start = end - timedelta(days=MAX_WINDOW_DAYS - 1)
        return (
            datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
            datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc),
            f"{start.isoformat()}..{end.isoformat()}",
        )

    days = PRESET_WINDOWS.get(window, 30)
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now, "today"
    return now - timedelta(days=days), now, window


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """A percentage, or None when there is nothing to divide by.

    None rather than 0.0 on purpose: "no signups yet" and "signups that
    all failed to convert" are different facts and a dashboard that shows
    0% for both is lying about one of them.
    """
    if not denominator:
        return None
    return round(100 * numerator / denominator, 1)


async def _counts(conn, frm: datetime, to: datetime) -> Dict[str, Dict[str, int]]:
    """Per-event totals and unique accounts, in one pass.

    One grouped query rather than one query per metric: there are twenty
    numbers on this page and twenty round trips to build it would make the
    dashboard the slowest thing in the product.
    """
    rows = await conn.fetch(
        """
        SELECT event,
               source,
               COUNT(*)                AS events,
               COUNT(DISTINCT user_id) AS users
        FROM funnel_events
        WHERE occurred_at >= $1 AND occurred_at <= $2
        GROUP BY event, source
        """,
        frm, to,
    )
    out: Dict[str, Dict[str, int]] = {}
    for row in rows:
        bucket = out.setdefault(row["event"], {"events": 0, "users": 0, "server_events": 0, "server_users": 0})
        bucket["events"] += row["events"]
        bucket["users"] += row["users"]
        if row["source"] == "server":
            bucket["server_events"] += row["events"]
            bucket["server_users"] += row["users"]
    return out


async def _activation(conn, frm: datetime, to: datetime) -> Dict[str, Any]:
    """How many people who signed up in the window went on to do something.

    Cohort-based: the denominator is people who SIGNED UP in the window,
    and the numerator is how many of those activated - at any time, not
    only inside the window. Counting activations in the window against
    signups in the window would credit this month's signups with last
    month's members finally doing something.
    """
    row = await conn.fetchrow(
        """
        WITH cohort AS (
            SELECT DISTINCT user_id
            FROM funnel_events
            WHERE event = 'signup_completed'
              AND user_id IS NOT NULL
              AND occurred_at >= $1 AND occurred_at <= $2
        )
        SELECT
            (SELECT COUNT(*) FROM cohort) AS signups,
            (SELECT COUNT(*) FROM cohort c
              WHERE EXISTS (
                  SELECT 1 FROM funnel_events f
                  WHERE f.user_id = c.user_id
                    AND f.event = ANY($3::text[])
              )) AS activated
        """,
        frm, to, list(ACTIVATION_EVENTS),
    )
    signups = int(row["signups"] or 0)
    activated = int(row["activated"] or 0)
    return {
        "cohort_signups": signups,
        "activated": activated,
        "activation_rate": _rate(activated, signups),
        # Stated in the payload so the dashboard's label and the number
        # cannot drift apart.
        "definition": list(ACTIVATION_EVENTS),
    }


async def _medians(conn, frm: datetime, to: datetime) -> Dict[str, Optional[float]]:
    """Median hours from signup to the first useful action, and to checkout.

    Median rather than mean: a single member who signs up and converts
    four months later would drag an average into meaninglessness.

    Both are measured per user from their OWN signup, and only forwards -
    an action before signup (the same person on an older account, a clock
    skew) is excluded rather than contributing a negative interval.
    """
    row = await conn.fetchrow(
        """
        WITH signups AS (
            SELECT user_id, MIN(occurred_at) AS signed_up_at
            FROM funnel_events
            WHERE event = 'signup_completed'
              AND user_id IS NOT NULL
              AND occurred_at >= $1 AND occurred_at <= $2
            GROUP BY user_id
        ),
        first_action AS (
            SELECT s.user_id,
                   MIN(f.occurred_at) FILTER (WHERE f.event = ANY($3::text[])) AS acted_at,
                   MIN(f.occurred_at) FILTER (WHERE f.event = 'checkout_started') AS checkout_at
            FROM signups s
            JOIN funnel_events f
              ON f.user_id = s.user_id
             AND f.occurred_at >= s.signed_up_at
            GROUP BY s.user_id
        )
        SELECT
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (fa.acted_at - s.signed_up_at)) / 3600.0
            ) FILTER (WHERE fa.acted_at IS NOT NULL) AS hours_to_action,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (fa.checkout_at - s.signed_up_at)) / 3600.0
            ) FILTER (WHERE fa.checkout_at IS NOT NULL) AS hours_to_checkout
        FROM signups s
        JOIN first_action fa ON fa.user_id = s.user_id
        """,
        frm, to, list(ACTIVATION_EVENTS),
    )
    def _hours(value):
        return round(float(value), 2) if value is not None else None

    return {
        "median_hours_to_first_action": _hours(row["hours_to_action"] if row else None),
        "median_hours_to_checkout": _hours(row["hours_to_checkout"] if row else None),
    }


async def _active_members(conn, frm: datetime, to: datetime) -> Dict[str, int]:
    """Distinct Free and Elite accounts that did anything in the window.

    Tier comes from the event properties the client already sends (`tier`
    is in the allow-list), so this needs no join against the users table
    and no second entitlement calculation. An account whose tier changed
    mid-window counts under both, which is correct: they were both.
    """
    rows = await conn.fetch(
        """
        SELECT props->>'tier' AS tier, COUNT(DISTINCT user_id) AS users
        FROM funnel_events
        WHERE occurred_at >= $1 AND occurred_at <= $2
          AND user_id IS NOT NULL
          AND props->>'tier' IN ('free', 'elite')
        GROUP BY props->>'tier'
        """,
        frm, to,
    )
    out = {"free": 0, "elite": 0}
    for row in rows:
        out[row["tier"]] = int(row["users"] or 0)
    return out


async def _daily_trend(conn, frm: datetime, to: datetime) -> List[Dict[str, Any]]:
    """One row per day: the four numbers worth watching move.

    Deliberately four and not twenty. A trend chart with every event on it
    is a chart nobody reads.
    """
    rows = await conn.fetch(
        """
        SELECT (occurred_at AT TIME ZONE 'UTC')::date AS day,
               COUNT(*) FILTER (WHERE event = 'signup_completed')       AS signups,
               COUNT(*) FILTER (WHERE event = 'upgrade_prompt_viewed')  AS prompts,
               COUNT(*) FILTER (WHERE event = 'checkout_started')       AS checkouts,
               COUNT(*) FILTER (WHERE event = 'subscription_confirmed'
                                  AND source = 'server')               AS confirmed
        FROM funnel_events
        WHERE occurred_at >= $1 AND occurred_at <= $2
        GROUP BY 1
        ORDER BY 1
        """,
        frm, to,
    )
    return [
        {
            "day": row["day"].isoformat(),
            "signups": int(row["signups"]),
            "upgrade_prompts": int(row["prompts"]),
            "checkouts_started": int(row["checkouts"]),
            "subscriptions_confirmed": int(row["confirmed"]),
        }
        for row in rows
    ]


async def conversion_report(
    window: str = "30d",
    start: Optional[date] = None,
    end: Optional[date] = None,
    *,
    pool=None,
) -> Dict[str, Any]:
    """Everything the admin dashboard shows, in one call.

    Five queries, not twenty: the per-event counts come from one grouped
    pass and the rest are the questions that genuinely need their own
    shape (cohort activation, medians, tier actives, the daily trend).
    """
    if pool is None:
        from app.db import get_core_pool

        pool = await get_core_pool()

    frm, to, label = resolve_window(window, start, end)

    async with pool.acquire() as conn:
        counts = await _counts(conn, frm, to)
        activation = await _activation(conn, frm, to)
        medians = await _medians(conn, frm, to)
        actives = await _active_members(conn, frm, to)
        trend = await _daily_trend(conn, frm, to)

    def events(name: str) -> int:
        return int(counts.get(name, {}).get("events", 0))

    def users(name: str) -> int:
        return int(counts.get(name, {}).get("users", 0))

    def server_users(name: str) -> int:
        return int(counts.get(name, {}).get("server_users", 0))

    signup_views = events("signup_viewed")
    signup_starts = events("signup_started")
    signup_completions = users("signup_completed")
    checkout_starts = users("checkout_started")
    # Revenue is the SERVER-confirmed number. The browser's
    # checkout_completed is kept beside it as intent, clearly separated -
    # see migration 077 for why they are not the same fact.
    confirmed = server_users("subscription_confirmed")
    returned_from_checkout = users("checkout_completed")

    return {
        "window": label,
        "from": frm.isoformat(),
        "to": to.isoformat(),
        "signed_out": {
            # Event counts, NOT people. These rows carry no user_id by
            # design and this product has no privacy-safe session id, so
            # presenting them as unique visitors would be an invention.
            "note": "Event counts. Signed-out events carry no account, so these are not unique people.",
            "signup_page_views": signup_views,
            "signup_starts": signup_starts,
        },
        "signup": {
            "starts": signup_starts,
            "completions": signup_completions,
            # Starts are anonymous events and completions are people, so
            # this rate mixes two units. It is still the most useful
            # number on the page; the note says what it is.
            "completion_rate": _rate(signup_completions, signup_starts),
            "note": "Completion rate compares signup completions (people) with signup starts (events).",
        },
        "onboarding": {
            "starts": users("onboarding_started"),
            "completions": users("onboarding_completed"),
            "skips": users("onboarding_skipped"),
            "completion_rate": _rate(users("onboarding_completed"), users("onboarding_started")),
        },
        "free_usage": {
            "opportunity_views": users("free_opportunity_viewed"),
            "limit_hits": users("free_limit_reached"),
        },
        "upgrade": {
            "prompt_views": users("upgrade_prompt_viewed"),
            "pricing_views": users("pricing_viewed"),
            "checkout_starts": checkout_starts,
            "checkout_cancellations": users("checkout_cancelled"),
            "returned_from_checkout": returned_from_checkout,
            "subscriptions_confirmed": confirmed,
            "checkout_completion_rate": _rate(confirmed, checkout_starts),
            "signup_to_elite_rate": _rate(confirmed, signup_completions),
            "note": (
                "subscriptions_confirmed comes from Stripe webhooks and is the revenue "
                "number. returned_from_checkout is a browser event and means only that "
                "someone reached the return page."
            ),
        },
        "activation": {
            **activation,
            "first_watchlist_card": users("first_watchlist_card_added"),
            "first_alert": users("first_alert_created"),
            "first_portfolio_trade": users("first_portfolio_trade_added"),
        },
        "timing": medians,
        "active_members": actives,
        "daily": trend,
    }
