# app/services/content_day.py
#
# The FC "content day" boundary.
#
# EA's content cadence is anchored to 18:00 UK time - promos, SBCs and
# Team of the Week all land on that boundary, and traders think in those
# days, not in calendar days. Free daily picks reset on the same boundary
# so "your next picks arrive at 6pm" matches what a trader already knows.
#
# Europe/London, not UTC and not a fixed offset. Between late March and
# late October the UK is on BST (UTC+1), so 18:00 local is 17:00Z for
# roughly half the year and 18:00Z for the other half. A fixed-offset
# implementation silently shifts the reset by an hour twice a year and
# gives one 23-hour day and one 25-hour day at the transitions - which,
# for a "two picks per day" allowance, means a day where a user gets a
# third pick or loses one.
#
# Everything here is pure and takes an explicit `now`, so the DST
# behaviour is directly testable without freezing the system clock.
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# One setting, one place. Both are env-overridable so a different content
# cadence never means editing call sites.
CONTENT_DAY_TIMEZONE = os.getenv("CONTENT_DAY_TIMEZONE", "Europe/London")
CONTENT_DAY_HOUR = int(os.getenv("CONTENT_DAY_HOUR", "18"))


def content_zone() -> ZoneInfo:
    return ZoneInfo(CONTENT_DAY_TIMEZONE)


def _aware(moment: Optional[datetime]) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _boundary_on(day: date, zone: ZoneInfo) -> datetime:
    """The content boundary for a given local calendar date, as UTC.

    `fold` is left at its default. The boundary hour is 18:00, and neither
    UK transition happens near 18:00 (they are at 01:00/02:00 local), so
    18:00 is never ambiguous or non-existent here. If CONTENT_DAY_HOUR is
    ever moved into the small hours this is the line that needs a fold
    decision - hence saying so rather than leaving it implicit.
    """
    return datetime.combine(day, time(CONTENT_DAY_HOUR), tzinfo=zone).astimezone(timezone.utc)


def content_day_start(now: Optional[datetime] = None) -> datetime:
    """Start (in UTC) of the content day `now` falls in."""
    now = _aware(now)
    zone = content_zone()
    local = now.astimezone(zone)
    start = _boundary_on(local.date(), zone)
    if now < start:
        # Before today's 18:00 boundary - we are still in yesterday's day.
        start = _boundary_on(local.date() - timedelta(days=1), zone)
    return start


def next_content_day_start(now: Optional[datetime] = None) -> datetime:
    """When the current content day ends and the next one begins (UTC).

    Computed by stepping the LOCAL calendar date forward and re-resolving
    the boundary, never by adding 24 hours: across a DST transition the
    real gap is 23 or 25 hours, and adding a fixed day would drift the
    reset by an hour.
    """
    start = content_day_start(now)
    zone = content_zone()
    local_start_date = start.astimezone(zone).date()
    return _boundary_on(local_start_date + timedelta(days=1), zone)


def content_day_key(now: Optional[datetime] = None) -> date:
    """Stable identifier for a content day: the local date it STARTED on.

    So everything from Tuesday 18:00 to Wednesday 17:59 local is keyed to
    Tuesday. Used as the persistence key for the daily free picks and as
    the seed for their deterministic ordering.
    """
    return content_day_start(now).astimezone(content_zone()).date()


def seconds_until_next_content_day(now: Optional[datetime] = None) -> int:
    return max(0, int((next_content_day_start(now) - _aware(now)).total_seconds()))
