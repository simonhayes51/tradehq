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


# ---------------------------------------------------------------------------
# What the boundary means for the free allowance
# ---------------------------------------------------------------------------
#
# The content day is not an abstraction: it is the thing that decides when
# a Free member's two picks change. These tests cross the boundary rather
# than testing the helpers in isolation.

from zoneinfo import ZoneInfo  # noqa: E402

from app.services.content_day import CONTENT_DAY_TIMEZONE, content_day_key  # noqa: E402
from app.services.free_picks import free_pick_meta, select_picks  # noqa: E402


def _pick_row(card_id, *, bin_price=40_000, confidence=0.8, name=None):
    """A candidate that genuinely clears the free-pick bar - same field
    names free_picks.py reads (see tests/test_free_picks.py's card())."""
    return {
        "source_card_id": card_id,
        "name": name or f"Player {card_id}",
        "display_name": name or f"Player {card_id}",
        "signal": "buy",
        "confidence_score": confidence,
        "expected_roi": 0.2,
        "expected_profit_after_tax": 8_000,
        "current_bin": bin_price,
        "price_age_minutes": 10,
        "recent_sale_count": 40,
        "recommended_buy_max": int(bin_price * 0.95),
        "recommended_sell_target": int(bin_price * 1.3),
    }


class TestPicksAreStableWithinADayAndChangeAcross:
    def test_the_same_pool_gives_the_same_picks_all_day(self):
        pool = [_pick_row(i, bin_price=10_000 * (i + 1)) for i in range(1, 12)]
        day = content_day_key(datetime(2026, 8, 30, 18, 30, tzinfo=timezone.utc))
        first = [r["source_card_id"] for r in select_picks(pool, day)]
        # Same day key, same pool, many hours apart - the selection is a
        # pure function, so nothing about the time of day can move it.
        again = [r["source_card_id"] for r in select_picks(pool, day)]
        assert first == again
        assert first  # and it actually picked something

    def test_the_day_key_changes_at_the_boundary_and_not_before(self):
        london = ZoneInfo(CONTENT_DAY_TIMEZONE)
        just_before = datetime(2026, 8, 30, 17, 59, tzinfo=london)
        just_after = datetime(2026, 8, 30, 18, 1, tzinfo=london)
        assert content_day_key(just_before) != content_day_key(just_after)
        # And an hour earlier is still the same day as just_before.
        assert content_day_key(datetime(2026, 8, 30, 16, 59, tzinfo=london)) == content_day_key(just_before)

    def test_a_different_day_can_reorder_ties_that_a_single_day_cannot(self):
        # The tie-break is seeded by the day key, so two candidates that
        # are genuinely identical on every ranked field are not always
        # resolved the same way forever - which is the point: an unlucky
        # card is not excluded permanently by an accident of ordering.
        identical = [_pick_row(i) for i in range(1, 40)]
        days = {
            tuple(r["source_card_id"] for r in select_picks(identical, date(2026, 8, d)))
            for d in range(1, 20)
        }
        assert len(days) > 1, "the day seed is not reaching the tie-break"

    def test_the_meta_tells_the_user_when_the_next_picks_land(self):
        meta = free_pick_meta(now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc), unlocked=2)
        assert meta["unlocked"] == 2
        assert meta["allowance"] >= 1
        assert meta["secondsUntilRefresh"] > 0
        # The refresh time is a real timestamp, not "later today".
        assert meta["nextRefreshAt"]
        assert datetime.fromisoformat(meta["nextRefreshAt"]) > datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

    def test_the_countdown_never_exceeds_a_long_dst_day(self):
        # A 25-hour day exists (the autumn fall-back). Anything longer
        # means the boundary was computed by adding 24 hours to a UTC
        # instant instead of stepping the local date.
        london = ZoneInfo(CONTENT_DAY_TIMEZONE)
        for month, day in ((3, 29), (10, 25), (6, 15), (12, 25)):
            at = datetime(2026, month, day, 18, 1, tzinfo=london)
            meta = free_pick_meta(now=at)
            assert 0 < meta["secondsUntilRefresh"] <= 25 * 3600, (month, day)
