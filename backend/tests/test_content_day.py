# tests/test_content_day.py
#
# The content-day boundary decides when a Free member's two daily picks
# reset. Getting DST wrong doesn't error - it silently shifts the reset by
# an hour twice a year and produces one 23-hour day and one 25-hour day,
# which for a per-day allowance means a day where someone gets a third
# pick or loses one.
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import content_day as cd


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class TestBoundaryTracksUkLocalTime:
    def test_in_british_summer_time_the_boundary_is_1700_utc(self):
        # June: UK is on BST (UTC+1), so 18:00 local is 17:00Z.
        assert cd.content_day_start(utc(2026, 6, 10, 17, 30)) == utc(2026, 6, 10, 17)

    def test_in_gmt_the_boundary_is_1800_utc(self):
        # December: UK is on GMT (UTC+0), so 18:00 local is 18:00Z.
        assert cd.content_day_start(utc(2026, 12, 10, 18, 30)) == utc(2026, 12, 10, 18)

    def test_a_fixed_utc_offset_would_have_been_wrong_by_an_hour(self):
        summer = cd.content_day_start(utc(2026, 6, 10, 17, 30))
        winter = cd.content_day_start(utc(2026, 12, 10, 18, 30))
        assert summer.hour != winter.hour  # the whole point


class TestDayAssignment:
    def test_just_before_the_boundary_belongs_to_the_previous_day(self):
        # 16:59 local on the 10th is still the content day that started
        # on the 9th.
        assert cd.content_day_key(utc(2026, 6, 10, 16, 59)) == date(2026, 6, 9)

    def test_on_the_boundary_starts_the_new_day(self):
        assert cd.content_day_key(utc(2026, 6, 10, 17, 0)) == date(2026, 6, 10)

    def test_the_key_is_stable_across_the_whole_day(self):
        start = utc(2026, 6, 10, 17, 0)
        keys = {cd.content_day_key(start + timedelta(minutes=m)) for m in range(0, 24 * 60, 37)}
        assert keys == {date(2026, 6, 10)}


class TestDstTransitions:
    # UK 2026: clocks go forward 29 March, back 25 October.
    def test_spring_forward_day_is_23_hours_long(self):
        start = cd.content_day_start(utc(2026, 3, 28, 18, 30))  # GMT still
        nxt = cd.next_content_day_start(utc(2026, 3, 28, 18, 30))
        assert (nxt - start) == timedelta(hours=23)

    def test_autumn_back_day_is_25_hours_long(self):
        start = cd.content_day_start(utc(2026, 10, 24, 17, 30))  # BST still
        nxt = cd.next_content_day_start(utc(2026, 10, 24, 17, 30))
        assert (nxt - start) == timedelta(hours=25)

    def test_the_boundary_is_always_1800_local_even_across_a_transition(self):
        zone = cd.content_zone()
        for moment in (utc(2026, 3, 28, 20), utc(2026, 3, 29, 20), utc(2026, 10, 24, 20), utc(2026, 10, 25, 20)):
            local = cd.content_day_start(moment).astimezone(zone)
            assert (local.hour, local.minute) == (18, 0), moment

    def test_next_day_is_never_computed_by_adding_24_hours(self):
        # If it were, one of these two would be an hour out.
        for moment in (utc(2026, 3, 28, 18, 30), utc(2026, 10, 24, 17, 30)):
            naive = cd.content_day_start(moment) + timedelta(days=1)
            assert cd.next_content_day_start(moment) != naive


class TestOrdering:
    def test_a_day_always_advances(self):
        moment = utc(2026, 1, 1, 12)
        for _ in range(400):
            start = cd.content_day_start(moment)
            nxt = cd.next_content_day_start(moment)
            assert nxt > start
            assert timedelta(hours=23) <= (nxt - start) <= timedelta(hours=25)
            moment = nxt + timedelta(minutes=1)

    def test_countdown_is_never_negative(self):
        assert cd.seconds_until_next_content_day(utc(2026, 6, 10, 16, 59)) > 0
        assert cd.seconds_until_next_content_day(utc(2026, 6, 10, 17, 0)) > 0


def test_naive_datetimes_are_treated_as_utc_not_local():
    assert cd.content_day_start(datetime(2026, 6, 10, 17, 30)) == utc(2026, 6, 10, 17)


@pytest.mark.parametrize("hour", [0, 6, 12, 18, 23])
def test_every_moment_lands_in_exactly_one_content_day(hour):
    moment = utc(2026, 7, 15, hour)
    start = cd.content_day_start(moment)
    assert start <= moment < cd.next_content_day_start(moment)
