# tests/test_market_event_reconstruction.py
#
# Pure tests for the content-event grouping rules
# (app/services/market_event_reconstruction.py). No DB.
#
# The rules being pinned here are the ones that decide whether the
# reconstructed timeline is real or fictional:
#   * group by (game_year, squad, release CLUSTER), never by rarity alone
#   * absorb FUT.GG ingestion jitter without merging distinct releases
#   * never invent an event for a card with no release timestamp
#   * keep FC26 and FC27 events separate
#   * distinguish a promo team from a one-off SBC card
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.market_event_reconstruction import (
    BROAD_RELEASE_MIN_CARDS,
    EVENT_PROMO_RELEASE,
    EVENT_TOTW_RELEASE,
    SCALE_BROAD,
    SCALE_ISOLATED,
    SCALE_MINI,
    CardRelease,
    classify_event_type,
    group_release_events,
)

T1 = datetime(2026, 3, 18, 18, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 3, 25, 18, 0, tzinfo=timezone.utc)


def _cards(n, *, squad, rarity="Team of the Week", released_at=T1, game_year=26, start_id=1):
    return [
        CardRelease(
            source_card_id=start_id + i, source_player_id=1000 + i,
            game_year=game_year, rarity=rarity, squad=squad, released_at=released_at,
        )
        for i in range(n)
    ]


class TestGroupingKey:
    def test_a_squad_sharing_one_timestamp_becomes_one_event(self):
        events = group_release_events(_cards(11, squad="TOTW 27"))
        assert len(events) == 1
        assert events[0].card_count == 11
        assert events[0].squad == "TOTW 27"

    def test_weekly_squads_do_not_collapse_into_one_rarity_event(self):
        """The single most important rule: every weekly TOTW shares the
        rarity 'Team of the Week'. Grouping by rarity would fuse a whole
        season into one meaningless mega-event."""
        cards = _cards(11, squad="TOTW 27", released_at=T1) + _cards(
            11, squad="TOTW 28", released_at=T2, start_id=100
        )
        events = group_release_events(cards)
        assert len(events) == 2
        assert {e.squad for e in events} == {"TOTW 27", "TOTW 28"}

    def test_ingestion_jitter_within_a_drop_stays_one_event(self):
        """REGRESSION - found against real production data. FUT.GG stamps
        a single promo drop across adjacent minutes (a live FUTTIES 2
        sample landed at 17:01/17:02/17:03 UTC). Exact-timestamp grouping
        shattered that one drop into three events, two of them then
        mislabelled 'mini' because each fragment held only part of the
        squad."""
        cards = (
            _cards(5, squad="FUTTIES 2", rarity="FUTTIES", released_at=T1)
            + _cards(8, squad="FUTTIES 2", rarity="FUTTIES",
                     released_at=T1 + timedelta(minutes=1), start_id=100)
            + _cards(5, squad="FUTTIES 2", rarity="FUTTIES",
                     released_at=T1 + timedelta(minutes=2), start_id=200)
        )
        events = group_release_events(cards)
        assert len(events) == 1
        assert events[0].card_count == 18
        assert events[0].event_scale == SCALE_BROAD
        # Stamped at the START of the drop - using the latest or a mean
        # would place the event after part of the market had reacted.
        assert events[0].released_at == T1
        # The absorbed jitter stays visible rather than being smoothed away.
        assert events[0].span_seconds == 120

    def test_a_multi_wave_release_does_not_chain_into_one_fictional_event(self):
        """REGRESSION - found against real FC26 data. TOTY releases in
        waves (Attackers/Midfielders/Defenders/GK) roughly a day apart.
        Each wave sits within the GAP tolerance of the previous one, so
        gap-based linkage alone chained 24 TOTY cards into a single
        'event' spanning SEVEN DAYS. Thunderstruck 1 chained across 48h
        the same way. MAX_CLUSTER_SPAN caps the total span so these split
        into the separate market moments they actually were."""
        waves = []
        for wave, offset_days in enumerate((0, 1.75, 3.5, 7)):
            waves += _cards(6, squad="TOTY", rarity="TOTY",
                            released_at=T1 + timedelta(days=offset_days),
                            start_id=wave * 100)
        events = group_release_events(waves)
        assert len(events) == 4
        assert all(e.card_count == 6 for e in events)
        # Each wave is its own tight drop, not a week-long smear.
        assert all(e.span_seconds == 0 for e in events)

    def test_a_slow_but_genuine_single_drop_stays_intact(self):
        """The counterweight to the test above: the slowest real drop
        measured in FC26 (Joga Bonito, 39 cards) spanned 1h47m. That is
        ONE release with a slow import and must NOT be split."""
        cards = (
            _cards(13, squad="Joga Bonito", rarity="Joga Bonito", released_at=T1)
            + _cards(13, squad="Joga Bonito", rarity="Joga Bonito",
                     released_at=T1 + timedelta(minutes=53), start_id=50)
            + _cards(13, squad="Joga Bonito", rarity="Joga Bonito",
                     released_at=T1 + timedelta(seconds=6420), start_id=100)
        )
        events = group_release_events(cards)
        assert len(events) == 1
        assert events[0].card_count == 39

    def test_two_different_promos_in_the_same_hour_do_not_merge(self):
        """Clustering is WITHIN a squad only - simultaneous drops of
        different promos must stay separate events."""
        cards = (
            _cards(7, squad="FUTTIES 2", rarity="FUTTIES", released_at=T1)
            + _cards(8, squad="Cornerstones 1", rarity="Cornerstones",
                     released_at=T1 + timedelta(minutes=1), start_id=100)
        )
        events = group_release_events(cards)
        assert len(events) == 2
        assert {e.squad for e in events} == {"FUTTIES 2", "Cornerstones 1"}

    def test_a_cluster_survives_a_rarity_that_failed_to_parse(self):
        """One card's missing rarity must not blank the event's promo
        family."""
        cards = _cards(4, squad="FUTTIES 2", rarity=None, released_at=T1)
        cards += _cards(4, squad="FUTTIES 2", rarity="FUTTIES",
                        released_at=T1 + timedelta(minutes=1), start_id=100)
        events = group_release_events(cards)
        assert len(events) == 1
        assert events[0].rarity == "FUTTIES"

    def test_cards_added_later_to_the_same_squad_form_their_own_event(self):
        """A late addition to an existing promo is genuinely a separate
        market moment - it must not be retroactively folded into the
        original drop."""
        cards = _cards(10, squad="Cornerstones 1", rarity="Cornerstones", released_at=T1)
        cards += _cards(2, squad="Cornerstones 1", rarity="Cornerstones",
                        released_at=T1 + timedelta(days=3), start_id=500)
        events = group_release_events(cards)
        assert len(events) == 2
        assert sorted(e.card_count for e in events) == [2, 10]

    def test_squad_matching_is_case_insensitive(self):
        cards = _cards(3, squad="Cornerstones 1") + _cards(
            3, squad="cornerstones 1", start_id=50
        )
        assert len(group_release_events(cards)) == 1

    def test_events_are_returned_in_chronological_order(self):
        cards = _cards(6, squad="TOTW 28", released_at=T2, start_id=100) + _cards(
            6, squad="TOTW 27", released_at=T1
        )
        events = group_release_events(cards)
        assert [e.released_at for e in events] == [T1, T2]


class TestGameYearIsolation:
    def test_fc26_and_fc27_events_never_merge(self):
        cards = _cards(6, squad="TOTW 27", game_year=26) + _cards(
            6, squad="TOTW 27", game_year=27, start_id=100
        )
        events = group_release_events(cards)
        assert len(events) == 2
        assert {e.game_year for e in events} == {26, 27}


class TestNeverInventAnEvent:
    def test_cards_without_a_release_timestamp_are_skipped(self):
        """A timeline built on a missing timestamp is a fabrication - the
        entire point of this layer is that release dates are sourced."""
        cards = [
            CardRelease(source_card_id=1, source_player_id=1, game_year=26,
                        rarity="Cornerstones", squad="Cornerstones 1", released_at=None)
        ]
        assert group_release_events(cards) == []

    def test_cards_without_a_squad_are_skipped(self):
        """Without a squad there is no way to separate one release from
        another sharing a rarity; grouping them would invent events."""
        cards = [
            CardRelease(source_card_id=1, source_player_id=1, game_year=26,
                        rarity="Rare", squad=None, released_at=T1)
        ]
        assert group_release_events(cards) == []

    def test_blank_squad_is_treated_as_missing(self):
        cards = [
            CardRelease(source_card_id=1, source_player_id=1, game_year=26,
                        rarity="Rare", squad="   ", released_at=T1)
        ]
        assert group_release_events(cards) == []

    def test_a_mixed_batch_keeps_only_the_usable_cards(self):
        cards = _cards(6, squad="TOTW 27")
        cards.append(CardRelease(source_card_id=99, source_player_id=99, game_year=26,
                                 rarity="Rare", squad=None, released_at=T1))
        events = group_release_events(cards)
        assert len(events) == 1
        assert events[0].card_count == 6


class TestEventScale:
    def test_a_full_team_is_broad(self):
        events = group_release_events(_cards(BROAD_RELEASE_MIN_CARDS, squad="TOTW 27"))
        assert events[0].event_scale == SCALE_BROAD
        assert events[0].confidence == "high"

    def test_a_small_batch_is_mini(self):
        events = group_release_events(_cards(3, squad="Mini Release"))
        assert events[0].event_scale == SCALE_MINI
        assert events[0].confidence == "medium"

    def test_a_single_card_is_isolated_not_a_promo_team(self):
        """A lone card carrying a squad label is almost always an SBC or
        objective, not a promo. It must be distinguishable so analysis can
        exclude it rather than treating it as a team release."""
        events = group_release_events(_cards(1, squad="Some SBC"))
        assert events[0].event_scale == SCALE_ISOLATED
        assert events[0].confidence == "low"


class TestEventTypeClassification:
    def test_totw_is_recognised(self):
        assert classify_event_type("Team of the Week") == EVENT_TOTW_RELEASE
        assert classify_event_type("TOTW") == EVENT_TOTW_RELEASE

    def test_anything_else_is_a_generic_promo_release(self):
        """Deliberately minimal - over-classifying without evidence would
        bake guesses into the data."""
        assert classify_event_type("Cornerstones") == EVENT_PROMO_RELEASE
        assert classify_event_type("Team of the Year") == EVENT_PROMO_RELEASE
        assert classify_event_type(None) == EVENT_PROMO_RELEASE


class TestEventKeyStability:
    def test_event_key_is_a_stable_slug(self):
        events = group_release_events(_cards(6, squad="Cornerstones 1", rarity="Cornerstones"))
        assert events[0].event_key == "cornerstones_1"
        assert events[0].title == "Cornerstones 1"

    def test_regrouping_the_same_cards_produces_identical_keys(self):
        """Idempotency at the pure layer - the DB upsert relies on the key
        being stable across runs, or every reconstruction would duplicate
        rather than update."""
        cards = _cards(6, squad="TOTW 27")
        first = group_release_events(cards)
        second = group_release_events(list(reversed(cards)))
        assert [(e.event_key, e.released_at, e.card_count) for e in first] == \
               [(e.event_key, e.released_at, e.card_count) for e in second]
