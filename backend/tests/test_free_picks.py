# tests/test_free_picks.py
#
# The free tier's whole credibility rests on these two picks being the
# same for everyone, all day, and being ones we'd defend.
from __future__ import annotations

from datetime import date

import pytest

from app.services import free_picks as fp

DAY = date(2026, 6, 10)


def card(card_id, *, name=None, confidence=0.8, roi=0.15, profit=9000, bin_price=50_000,
         signal="buy", age=10, sales=20, buy_max=48_000, target=60_000):
    return {
        "source_card_id": card_id,
        "name": name or f"Player {card_id}",
        "display_name": name or f"Player {card_id}",
        "signal": signal,
        "confidence_score": confidence,
        "expected_roi": roi,
        "expected_profit_after_tax": profit,
        "current_bin": bin_price,
        "price_age_minutes": age,
        "recent_sale_count": sales,
        "recommended_buy_max": buy_max,
        "recommended_sell_target": target,
    }


class TestDefensibility:
    @pytest.mark.parametrize("override,reason", [
        ({"signal": "watch"}, "not a buy"),
        ({"signal": "avoid"}, "an avoid"),
        ({"confidence": 0.4}, "below the confidence floor"),
        ({"age": 500}, "price too stale"),
        ({"sales": 1}, "too few recent sales"),
        ({"buy_max": None}, "no entry price"),
        ({"target": None}, "no sell target"),
        ({"profit": None}, "no profit figure"),
    ])
    def test_undefensible_candidates_are_excluded(self, override, reason):
        assert fp.select_picks([card(1, **override)], DAY) == [], reason

    def test_a_clean_candidate_is_selected(self):
        assert [r["source_card_id"] for r in fp.select_picks([card(1)], DAY)] == [1]


class TestDeterminism:
    def test_same_pool_and_day_always_gives_the_same_picks(self):
        pool = [card(i, name=f"P{i}", confidence=0.8, bin_price=10_000 * i) for i in range(1, 12)]
        first = [r["source_card_id"] for r in fp.select_picks(pool, DAY)]
        for _ in range(20):
            assert [r["source_card_id"] for r in fp.select_picks(list(reversed(pool)), DAY)] == first

    def test_input_order_does_not_change_the_answer(self):
        # The bug being prevented: "whichever two the feed returned first".
        pool = [card(i, name=f"P{i}") for i in range(1, 8)]
        a = [r["source_card_id"] for r in fp.select_picks(pool, DAY)]
        b = [r["source_card_id"] for r in fp.select_picks(sorted(pool, key=lambda r: -r["source_card_id"]), DAY)]
        assert a == b

    def test_identical_candidates_are_ordered_by_a_day_seeded_hash(self):
        # Everything equal except the id: the order must still be stable
        # within a day, and is allowed to differ between days.
        pool = [card(i, name=f"P{i}", bin_price=50_000) for i in range(1, 30)]
        d1 = [r["source_card_id"] for r in fp.select_picks(pool, date(2026, 6, 10))]
        d2 = [r["source_card_id"] for r in fp.select_picks(pool, date(2026, 6, 11))]
        assert d1 == [r["source_card_id"] for r in fp.select_picks(pool, date(2026, 6, 10))]
        assert d1 != d2


class TestDiversity:
    def test_two_versions_of_one_player_are_not_both_picked(self):
        pool = [
            card(1, name="Kylian Mbappé", confidence=0.95, bin_price=40_000),
            card(2, name="Kylian Mbappé", confidence=0.94, bin_price=900_000),
            card(3, name="Erling Haaland", confidence=0.7, bin_price=120_000),
        ]
        names = {r["display_name"] for r in fp.select_picks(pool, DAY)}
        assert names == {"Kylian Mbappé", "Erling Haaland"}

    def test_picks_prefer_different_budget_bands(self):
        pool = [
            card(1, name="A", confidence=0.95, bin_price=800_000),
            card(2, name="B", confidence=0.94, bin_price=850_000),
            card(3, name="C", confidence=0.60, bin_price=8_000),
        ]
        bands = {fp.budget_band(r["current_bin"]) for r in fp.select_picks(pool, DAY)}
        assert bands == {"premium", "budget"}

    def test_diversity_never_costs_a_pick_when_the_pool_can_support_one(self):
        # Same band, different players: we still return two, because a
        # preference must not become a reason to under-deliver.
        pool = [card(1, name="A", bin_price=50_000), card(2, name="B", bin_price=55_000)]
        assert len(fp.select_picks(pool, DAY)) == 2


class TestHonestyAboutThinDays:
    def test_a_thin_day_returns_what_exists_and_never_invents_a_second(self):
        assert len(fp.select_picks([card(1)], DAY)) == 1

    def test_a_day_with_nothing_defensible_returns_nothing(self):
        assert fp.select_picks([card(1, confidence=0.1), card(2, signal="avoid")], DAY) == []

    def test_meta_states_plainly_when_the_day_is_short(self):
        assert fp.free_pick_meta(unlocked=1)["fewerThanAllowance"] is True
        assert fp.free_pick_meta(unlocked=fp.FREE_DAILY_PICKS)["fewerThanAllowance"] is False

    def test_meta_carries_the_next_refresh_time(self):
        meta = fp.free_pick_meta()
        assert meta["nextRefreshAt"]
        assert meta["secondsUntilRefresh"] >= 0
        assert meta["allowance"] == fp.FREE_DAILY_PICKS


def test_never_returns_more_than_the_allowance():
    pool = [card(i, name=f"P{i}", bin_price=1000 * i) for i in range(1, 40)]
    assert len(fp.select_picks(pool, DAY)) == fp.FREE_DAILY_PICKS


# ---------------------------------------------------------------------------
# The pool the picks are chosen from
# ---------------------------------------------------------------------------
#
# "The same two picks for every Free member" is a claim about the POOL as
# much as the ranking. /opportunities resolved the day's picks from
# whatever the requester had filtered and paginated to; because the choice
# is persisted once per content day with ON CONFLICT DO NOTHING, the first
# Free member of the day to load the page with risk=low set decided
# everyone else's picks from a pool nobody else was looking at.

import inspect  # noqa: E402


class TestPicksDoNotDependOnTheRequester:
    def test_opportunities_resolves_from_the_shared_unfiltered_pool(self):
        from app.routers.v2.futgg_market import list_opportunities

        source = inspect.getsource(list_opportunities)
        assert "resolve_pick_ids(lambda: free_pick_candidates(provider))" in source
        # The filtered, paginated list must not be what the day is decided
        # from. `scored` is this request's filtered ranking.
        assert "resolve_pick_ids(\n            lambda: [_item_with_intelligence(r, c) for r, c in scored]" not in source

    def test_the_pool_builder_takes_no_caller_filters(self):
        from app.routers.v2.futgg_market import free_pick_candidates

        params = inspect.signature(free_pick_candidates).parameters
        assert list(params) == ["provider"], (
            "free_pick_candidates must not accept anything a caller controls - "
            "a filterable pool is a pool that differs per requester"
        )

    def test_home_resolves_through_the_same_pool_builder(self):
        from app.routers.v2.dashboard import get_dashboard

        source = inspect.getsource(get_dashboard)
        assert "free_pick_candidates" in source
        assert "resolve_pick_ids" in source

    def test_home_never_hands_an_anonymous_caller_an_unlocked_card(self):
        from app.routers.v2.dashboard import get_dashboard

        source = inspect.getsource(get_dashboard)
        # The daily allowance belongs to an account. /api/v2/dashboard
        # needs none, so the resolution is guarded by is_free (which is
        # False for an anonymous viewer) rather than by "not elite".
        assert "if viewer.is_free:" in source


class TestTheCandidateBuilderMayBeAsync:
    def test_an_async_builder_is_awaited_not_stored_as_a_coroutine(self):
        import asyncio

        from app.services.free_picks import _materialise

        async def builder():
            return [{"source_card_id": 1}]

        assert asyncio.run(_materialise(builder)) == [{"source_card_id": 1}]

    def test_a_plain_builder_still_works(self):
        import asyncio

        from app.services.free_picks import _materialise

        assert asyncio.run(_materialise(lambda: [{"source_card_id": 2}])) == [{"source_card_id": 2}]

    def test_the_builder_is_not_called_when_the_day_is_already_decided(self):
        # The pool is a full market scan. Running it on every request of
        # the day, only to throw the result away because the picks are
        # already in the table, is the cost this laziness exists to avoid.
        import asyncio

        from app.services import free_picks

        calls = []

        async def builder():
            calls.append(1)
            return []

        class Pool:
            pass

        async def stored(_pool, _day):
            return [111, 222]

        original = free_picks.stored_pick_ids
        free_picks.stored_pick_ids = stored
        try:
            result = asyncio.run(free_picks.resolve_pick_ids(builder, pool=Pool()))
        finally:
            free_picks.stored_pick_ids = original

        assert result == [111, 222]
        assert calls == []
