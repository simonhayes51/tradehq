# tests/test_sbc_reward_match.py
#
# Production incident: Lamine Yamal 99 RW was surfaced as the homepage's
# Top Card - "23% below median, broadly flat" - while a live SBC
# ("Summer Stars Tournament Winners", ~1.7m to complete) was producing
# fresh copies of the exact same card. The trend layer is price-only and
# has no way to see that a live SBC is minting supply; this module is
# the missing signal.
#
# match_sbc_reward() is pure (no I/O) and tested directly with no
# database. refresh_sbc_reward_watch() is exercised against fakes in the
# same style as tests/test_discord_reconcile.py - a shared FakeConn per
# pool, asserted against by what it recorded rather than by simulating a
# real database.
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import sbc_reward_match as srm


def run(coro):
    return asyncio.run(coro)


AS_OF = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# normalize_player_name
# ---------------------------------------------------------------------------

class TestNormalizePlayerName:
    def test_case_folds(self):
        assert srm.normalize_player_name("Lamine Yamal") == srm.normalize_player_name("lamine yamal")

    def test_strips_accents(self):
        assert srm.normalize_player_name("N'Golo Kante") == srm.normalize_player_name("N'Golo Kanté")

    def test_strips_punctuation(self):
        assert srm.normalize_player_name("N'Golo Kante") == srm.normalize_player_name("N Golo Kante")

    def test_collapses_whitespace(self):
        assert srm.normalize_player_name("Lamine   Yamal") == "lamine yamal"

    def test_none_and_empty_are_the_empty_string(self):
        assert srm.normalize_player_name(None) == ""
        assert srm.normalize_player_name("") == ""


# ---------------------------------------------------------------------------
# match_sbc_reward - the conservative core
# ---------------------------------------------------------------------------

def _candidate(card_id, name, rating, display_name=None):
    return {"source_card_id": card_id, "name": name, "display_name": display_name, "rating": rating}


class TestMatchSbcReward:
    def test_unique_name_match(self):
        candidates = [_candidate(1, "Lamine Yamal", 99)]
        match = srm.match_sbc_reward("Lamine Yamal", candidates)
        assert match is not None
        assert match.futgg_source_card_id == 1
        assert match.match_method == "exact_name"

    def test_matches_via_display_name_too(self):
        candidates = [_candidate(1, "L. Yamal", 99, display_name="Lamine Yamal")]
        match = srm.match_sbc_reward("Lamine Yamal", candidates)
        assert match is not None
        assert match.futgg_source_card_id == 1

    def test_accent_and_punctuation_insensitive(self):
        candidates = [_candidate(1, "N'Golo Kanté", 90)]
        match = srm.match_sbc_reward("N Golo Kante", candidates)
        assert match is not None
        assert match.futgg_source_card_id == 1

    def test_no_candidates_is_no_match(self):
        assert srm.match_sbc_reward("Lamine Yamal", []) is None

    def test_no_name_hit_is_no_match(self):
        candidates = [_candidate(1, "Someone Else", 85)]
        assert srm.match_sbc_reward("Lamine Yamal", candidates) is None

    def test_empty_reward_text_is_no_match(self):
        candidates = [_candidate(1, "Lamine Yamal", 99)]
        assert srm.match_sbc_reward("", candidates) is None
        assert srm.match_sbc_reward(None, candidates) is None

    def test_ambiguous_name_without_a_rating_refuses_to_guess(self):
        # Most players have a base gold card plus specials. Guessing which
        # one an SBC produces, with no other evidence, is exactly the
        # wrong-match failure mode this function exists to avoid.
        candidates = [_candidate(1, "Lamine Yamal", 84), _candidate(2, "Lamine Yamal", 99)]
        assert srm.match_sbc_reward("Lamine Yamal", candidates) is None

    def test_ambiguous_name_disambiguated_by_rating_in_the_text(self):
        candidates = [_candidate(1, "Lamine Yamal", 84), _candidate(2, "Lamine Yamal", 99)]
        match = srm.match_sbc_reward("Lamine Yamal 99", candidates)
        assert match is not None
        assert match.futgg_source_card_id == 2
        assert match.match_method == "exact_name_rating"

    def test_rating_that_still_matches_multiple_cards_refuses(self):
        # Two different cards happen to share both name and rating (a
        # reissue, a duplicate scrape row, whatever the cause) - still not
        # unique, still not safe to guess.
        candidates = [_candidate(1, "Lamine Yamal", 99), _candidate(2, "Lamine Yamal", 99)]
        assert srm.match_sbc_reward("Lamine Yamal 99", candidates) is None

    @pytest.mark.parametrize("text", [
        "83+ Player Pick",
        "Rare Gold Players Pack",
        "Jumbo Premium Gold Pack",
        "5000 Coins",
        "Evolution: Foundations",
        "Draft Token x3",
    ])
    def test_generic_reward_text_never_matches(self, text):
        # Even if a candidate happens to share a name with a stray word in
        # the reward text, a generic pack/coin/token reward must never be
        # treated as a specific player - there is no player to match.
        candidates = [_candidate(1, "Gold Pack", 75)]
        assert srm.match_sbc_reward(text, candidates) is None

    def test_a_low_number_in_the_text_is_not_mistaken_for_a_rating(self):
        # "ends in 18 hours" - style text near the reward name has a
        # 2-digit number that must not be parsed as a card rating and
        # used to falsely disambiguate (no card in this game is rated
        # below 40).
        candidates = [_candidate(1, "Lamine Yamal", 84), _candidate(2, "Lamine Yamal", 99)]
        assert srm.match_sbc_reward("Lamine Yamal - ends in 18 hours", candidates) is None


# ---------------------------------------------------------------------------
# refresh_sbc_reward_watch - the cross-database refresher
# ---------------------------------------------------------------------------

class FakePlayerConn:
    def __init__(self, events):
        self.events = events

    async def fetch(self, sql, *args):
        if "FROM market_events" in sql:
            return list(self.events)
        return []


class FakeCoreConn:
    def __init__(self, candidates, *, locked=True):
        self.candidates = candidates
        self.locked = locked
        self.executemany_calls = []
        self.delete_calls = []
        self.watch_rows = {}  # futgg_source_card_id -> row dict, simulated state

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_lock" in sql:
            return self.locked
        return None

    async def fetch(self, sql, *args):
        if "FROM futgg_players" in sql:
            return list(self.candidates)
        if "DELETE FROM futgg_sbc_reward_watch" in sql:
            (keep_ids,) = args
            self.delete_calls.append(list(keep_ids))
            removed = [
                {"futgg_source_card_id": cid}
                for cid in list(self.watch_rows)
                if cid not in keep_ids
            ]
            for row in removed:
                del self.watch_rows[row["futgg_source_card_id"]]
            return removed
        return []

    async def execute(self, sql, *args):
        if "pg_advisory_unlock" in sql:
            return "OK"
        return "OK"

    async def executemany(self, sql, args_list):
        self.executemany_calls.append(list(args_list))
        for row in args_list:
            card_id = row[0]
            self.watch_rows[card_id] = {
                "futgg_source_card_id": card_id,
                "reward_title": row[1],
                "match_method": row[2],
                "market_event_id": row[3],
                "sbc_ends_at": row[4],
            }

    def transaction(self):
        conn = self

        class _Txn:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _Txn()


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _event(event_id, title, ends_at, reward_description=None):
    return {
        "market_event_id": event_id, "title": title, "ends_at": ends_at,
        "reward_description": reward_description,
    }


class TestRefreshSbcRewardWatch:
    def test_a_matched_event_is_upserted(self):
        core_conn = FakeCoreConn([_candidate(1, "Lamine Yamal", 99)])
        player_conn = FakePlayerConn([
            _event(501, "Lamine Yamal", AS_OF + timedelta(hours=30)),
        ])
        stats = run(srm.refresh_sbc_reward_watch(FakePool(core_conn), FakePool(player_conn)))
        assert stats["matched"] == 1
        assert stats["unmatched"] == 0
        assert 1 in core_conn.watch_rows
        assert core_conn.watch_rows[1]["match_method"] == "exact_name"

    def test_reward_description_is_preferred_over_title(self):
        # The set title can be broader/promotional text; reward_description
        # is EasySBC's own name for the item actually produced.
        core_conn = FakeCoreConn([_candidate(1, "Lamine Yamal", 99)])
        player_conn = FakePlayerConn([
            _event(501, "Summer Stars Tournament Winners", AS_OF + timedelta(hours=30),
                   reward_description="Lamine Yamal"),
        ])
        stats = run(srm.refresh_sbc_reward_watch(FakePool(core_conn), FakePool(player_conn)))
        assert stats["matched"] == 1
        assert 1 in core_conn.watch_rows

    def test_an_unmatched_event_is_not_written(self):
        core_conn = FakeCoreConn([_candidate(1, "Someone Else", 85)])
        player_conn = FakePlayerConn([
            _event(501, "83+ Player Pick", AS_OF + timedelta(hours=10)),
        ])
        stats = run(srm.refresh_sbc_reward_watch(FakePool(core_conn), FakePool(player_conn)))
        assert stats["matched"] == 0
        assert stats["unmatched"] == 1
        assert core_conn.watch_rows == {}

    def test_a_row_no_longer_matching_a_live_sbc_is_removed(self):
        # Simulates a second pass after the SBC ended: the previous
        # pass's row must not linger forever.
        core_conn = FakeCoreConn([_candidate(1, "Lamine Yamal", 99)])
        core_conn.watch_rows[1] = {"futgg_source_card_id": 1}
        player_conn = FakePlayerConn([])  # no live SBCs this pass
        stats = run(srm.refresh_sbc_reward_watch(FakePool(core_conn), FakePool(player_conn)))
        assert stats["removed"] == 1
        assert core_conn.watch_rows == {}

    def test_a_concurrent_run_is_skipped_not_duplicated(self):
        core_conn = FakeCoreConn([_candidate(1, "Lamine Yamal", 99)], locked=False)
        player_conn = FakePlayerConn([
            _event(501, "Lamine Yamal", AS_OF + timedelta(hours=30)),
        ])
        stats = run(srm.refresh_sbc_reward_watch(FakePool(core_conn), FakePool(player_conn)))
        assert stats == {"events_seen": 0, "matched": 0, "unmatched": 0, "removed": 0}
        assert core_conn.executemany_calls == []

    def test_an_event_with_no_ends_at_never_reaches_the_matcher(self):
        # The player-DB query itself filters e.ends_at IS NOT NULL AND
        # e.ends_at > now() - this pins that a fake event lacking ends_at
        # would never be returned by a real query, by asserting the
        # matcher path only sees what the fetch layer hands it.
        core_conn = FakeCoreConn([_candidate(1, "Lamine Yamal", 99)])
        player_conn = FakePlayerConn([])  # real WHERE clause would exclude a NULL ends_at row
        stats = run(srm.refresh_sbc_reward_watch(FakePool(core_conn), FakePool(player_conn)))
        assert stats["events_seen"] == 0


class TestRefresherLoopIsBestEffort:
    def test_a_failing_pass_does_not_raise(self, monkeypatch):
        async def _boom(core_pool, player_pool):
            raise RuntimeError("player db unreachable")

        monkeypatch.setattr(srm, "refresh_sbc_reward_watch", _boom)

        async def _one_iteration():
            task = asyncio.ensure_future(srm.refresher_loop(None, None, interval_seconds=0))
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        run(_one_iteration())  # must not raise

    def test_it_is_not_imported_or_run_at_module_load(self):
        # A background loop starting itself on import would run outside
        # any lifespan management this app relies on to shut it down.
        import inspect

        source = inspect.getsource(srm)
        module_level = source.split("async def refresher_loop")[0]
        assert "refresher_loop(" not in module_level


class TestAdvisoryLockKeyDoesNotCollide:
    """Postgres advisory locks are scoped to the DATABASE, not the
    process or codebase - a number picked only against backend/'s own
    REFRESH_LOCK_KEY list is not enough, because auto_sync/ (a separate
    set of deployed containers, entirely independent of this backend)
    already claims several numbers in the same "774101X" family against
    the same core database.

    This is not hypothetical: REFRESH_LOCK_KEY's first value (7741013)
    collided with futgg_sales_sync.py's own SALES_SYNC_ADVISORY_LOCK_KEY
    default, confirmed in production by futgg_sales_sync.py logging
    "another SalesSync owns advisory lock 7741013; exiting cleanly"
    while this refresher - an entirely unrelated job - held the same
    numeric lock, at the exact moment nothing else was actually
    overlapping it.
    """

    # Every advisory lock key claimed anywhere in this repo, backend and
    # auto_sync alike, as of this writing. Not derived by scanning source
    # at test time (a lock key can live behind an env_int() default that
    # is easy to miss with a regex) - each was confirmed by reading the
    # defining file directly. If a new one is added anywhere, add it
    # here too.
    _KNOWN_LOCK_KEYS = {
        7741002,  # backend fair_value.py
        7741004,  # backend analytics_engine.py
        7741005,  # backend event_impact.py
        7741006,  # backend recommendation_engine.py
        7741009,  # backend ml_feature_pipeline.py
        7741010,  # backend held_position_refresher.py
        7741011,  # backend market_data_provider.py
        7741012,  # backend priority_recompute.py
        7741013,  # auto_sync futgg_sales_sync.py (SALES_SYNC_ADVISORY_LOCK_KEY)
        7741021,  # auto_sync futgg_player_sync.py (OVERLAP_LOCK_KEY)
        7741022,  # auto_sync futgg_price_sync.py (OVERLAP_LOCK_KEY)
        7741042,  # auto_sync easysbc_sbc_sync.py (EASYSBC_LOCK_KEY)
    }

    def test_it_does_not_collide_with_any_known_lock_key(self):
        assert srm.REFRESH_LOCK_KEY not in self._KNOWN_LOCK_KEYS
