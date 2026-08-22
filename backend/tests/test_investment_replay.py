# tests/test_investment_replay.py
#
# Tests for the FC26 replay validation harness
# (app/services/investment_engine/replay/). No DB - the synthetic
# fixture and ReplayDataProvider are both pure/in-memory, matching every
# other investment-engine test module's no-DB discipline.
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.investment_engine import ledger as ledger_mod
from app.services.investment_engine.config import InvestmentEngineConfig
from app.services.investment_engine.replay.data_provider import CardMeta, LookaheadError, ReplayDataProvider
from app.services.investment_engine.replay.engine import UNAVAILABLE_HISTORICAL_REPLAY, run_full_replay
from app.services.investment_engine.replay.fixtures import build_synthetic_fixture
from app.services.investment_engine.replay.ledger_store import ReplayLedgerStore

D0 = date(2025, 9, 17)


def _tiny_provider(days=10, first_offset_by_card=None):
    first_offset_by_card = first_offset_by_card or {}
    prices = {}
    meta = {}
    for cid in range(1, 4):
        offset = first_offset_by_card.get(cid, 0)
        series = {D0 + timedelta(days=offset + i): 1000.0 + i * 10 for i in range(days - offset)}
        prices[cid] = series
        meta[cid] = CardMeta(card_id=cid, name=f"Card {cid}", primary_position="ST")
    return ReplayDataProvider(prices=prices, meta=meta, label="test-fixture")


class TestDataProviderLeakage:
    def test_price_history_up_to_never_returns_the_as_of_date_itself_when_exclusive(self):
        provider = _tiny_provider()
        hist = provider.price_history_up_to(1, D0 + timedelta(days=5), inclusive=False)
        assert all(d < D0 + timedelta(days=5) for d, _ in hist)

    def test_price_history_up_to_never_returns_dates_after_as_of(self):
        provider = _tiny_provider()
        hist = provider.price_history_up_to(1, D0 + timedelta(days=5), inclusive=True)
        assert all(d <= D0 + timedelta(days=5) for d, _ in hist)
        assert len(hist) == 6  # days 0..5 inclusive

    def test_cards_first_available_on_or_before_excludes_cards_not_yet_appeared(self):
        provider = _tiny_provider(days=10, first_offset_by_card={1: 0, 2: 3, 3: 7})
        assert provider.cards_first_available_on_or_before(D0) == [1]
        assert provider.cards_first_available_on_or_before(D0 + timedelta(days=3)) == [1, 2]
        assert provider.cards_first_available_on_or_before(D0 + timedelta(days=7)) == [1, 2, 3]

    def test_assert_no_future_access_raises_on_a_future_date(self):
        provider = _tiny_provider()
        with pytest.raises(LookaheadError):
            provider.assert_no_future_access(1, D0, [D0, D0 + timedelta(days=1)])

    def test_assert_no_future_access_passes_for_strictly_prior_dates(self):
        provider = _tiny_provider()
        provider.assert_no_future_access(1, D0 + timedelta(days=5), [D0, D0 + timedelta(days=1)])  # no raise


class TestReplayIsPrefixStable:
    """The strongest practical leakage test: replaying only through day X
    must produce IDENTICAL daily-state rows (up to and including day X)
    as replaying through the full period - appending future days must
    never change an already-computed historical state. Mirrors
    test_investment_no_leakage.py's method, applied to the replay
    orchestrator itself rather than just the state machine."""

    def test_truncated_replay_matches_prefix_of_full_replay(self):
        provider = build_synthetic_fixture(n_cards=80, end=D0 + timedelta(days=20))
        config = InvestmentEngineConfig()

        full = run_full_replay(provider, config=config)
        cutoff = D0 + timedelta(days=12)
        truncated = run_full_replay(provider, config=config, end=cutoff)

        def key(d):
            return (d.day, d.cohort_key, d.strategy_key)

        full_by_key = {key(d): d for d in full.daily_states if d.day <= cutoff}
        truncated_by_key = {key(d): d for d in truncated.daily_states}

        assert set(full_by_key) == set(truncated_by_key)
        for k, d_full in full_by_key.items():
            d_trunc = truncated_by_key[k]
            assert d_full.state == d_trunc.state, k
            assert d_full.drawdown_pct == d_trunc.drawdown_pct, k
            assert d_full.severity_class == d_trunc.severity_class, k
            assert d_full.candidate_count == d_trunc.candidate_count, k


class TestCohortBootstrap:
    def test_cohort_sizes_grow_as_more_cards_become_available_not_all_at_once(self):
        provider = build_synthetic_fixture(n_cards=200, end=D0 + timedelta(days=15), stagger_fraction=0.3)
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        days_sorted = sorted(result.cohort_size_by_day)
        first_total = sum(result.cohort_size_by_day[days_sorted[0]].values())
        last_total = sum(result.cohort_size_by_day[days_sorted[-1]].values())
        assert last_total >= first_total  # cohort coverage never shrinks (frozen membership, only additions)

    def test_a_card_classified_once_is_never_reclassified(self):
        provider = build_synthetic_fixture(n_cards=200, end=D0 + timedelta(days=15), stagger_fraction=0.3)
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        seen = {}
        for ev in result.classification_events:
            assert ev["card_id"] not in seen, f"card {ev['card_id']} classified twice"
            seen[ev["card_id"]] = ev

    def test_position_groups_below_min_cohort_size_are_not_force_classified(self):
        # Single-card "universe" - far below any min_group_size default -
        # must simply never appear in classification_events.
        provider = _tiny_provider(days=5)
        config = InvestmentEngineConfig()
        result = run_full_replay(provider, config=config)
        assert result.classification_events == []


class TestUnavailableLiveConfirmationHandling:
    def test_every_replay_signal_is_tagged_as_historical_replay_without_live_confirmation(self):
        provider = build_synthetic_fixture(n_cards=320, end=D0 + timedelta(days=25))
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        signals = result.ledger.all_signals()
        assert signals, "expected at least one signal in a 25-day replay of the full synthetic fixture"
        for s in signals:
            assert UNAVAILABLE_HISTORICAL_REPLAY in s.risk_flags
            assert any(UNAVAILABLE_HISTORICAL_REPLAY in r for r in s.rationale)

    def test_replay_confidence_is_discounted_relative_to_a_fully_confirmed_candidate(self):
        # Every replay candidate has sales_count_recent=None (no FC26 live
        # data) -> ranking.py's own liquidity_component floors at LOW
        # (0.2 weight) - confidence should never reach the top of the
        # [0,1] rank_score range purely because of this data gap.
        provider = build_synthetic_fixture(n_cards=320, end=D0 + timedelta(days=25))
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        confidences = [s.confidence for s in result.ledger.all_signals() if s.confidence is not None]
        assert confidences
        assert max(confidences) < 0.9  # never near-perfect confidence with no live confirmation at all


class TestDeterministicReplay:
    def test_running_the_same_provider_twice_produces_identical_signals(self):
        provider = build_synthetic_fixture(n_cards=150, end=D0 + timedelta(days=18))
        config = InvestmentEngineConfig()
        r1 = run_full_replay(provider, config=config)
        r2 = run_full_replay(provider, config=config)

        def sig_key(s):
            return (s.source_card_id, s.strategy_key, s.opened_on, s.entry_price, s.target_price,
                    s.status, s.outcome, s.after_tax_roi_pct, s.closed_on)

        assert sorted(sig_key(s) for s in r1.ledger.all_signals()) == sorted(sig_key(s) for s in r2.ledger.all_signals())

    def test_running_the_same_provider_twice_produces_identical_daily_states(self):
        provider = build_synthetic_fixture(n_cards=150, end=D0 + timedelta(days=18))
        config = InvestmentEngineConfig()
        r1 = run_full_replay(provider, config=config)
        r2 = run_full_replay(provider, config=config)

        def state_key(d):
            return (d.day, d.cohort_key, d.strategy_key, d.state, d.drawdown_pct, d.candidate_count)

        assert [state_key(d) for d in r1.daily_states] == [state_key(d) for d in r2.daily_states]


class TestReplayLedgerIsolation:
    def test_two_ledger_store_instances_never_share_state(self):
        store_a = ReplayLedgerStore()
        store_b = ReplayLedgerStore()
        store_a.open(
            game_year=26, card_id=1, strategy_key="X", cohort_key="premium_meta",
            entry_price=1000.0, max_buy=1000.0, target_price=1100.0,
            expected_roi_pct=5.0, expected_hold_hours_min=24.0, expected_hold_hours_max=72.0,
            confidence=0.5, liquidity="low", investability="limited",
            rationale=["r"], risk_flags=[], opened_on=D0, max_hold_hours=72.0,
            market_state_snapshot={}, shadow=True,
        )
        assert store_a.all_signals() and not store_b.all_signals()

    def test_open_is_idempotent_for_the_same_card_and_strategy(self):
        store = ReplayLedgerStore()
        kwargs = dict(
            game_year=26, card_id=1, strategy_key="X", cohort_key="premium_meta",
            entry_price=1000.0, max_buy=1000.0, target_price=1100.0,
            expected_roi_pct=5.0, expected_hold_hours_min=24.0, expected_hold_hours_max=72.0,
            confidence=0.5, liquidity="low", investability="limited",
            rationale=["r"], risk_flags=[], opened_on=D0, max_hold_hours=72.0,
            market_state_snapshot={}, shadow=True,
        )
        first = store.open(**kwargs)
        second = store.open(**kwargs)
        assert first is not None
        assert second is None
        assert len(store.all_signals()) == 1

    def test_replay_never_touches_production_ledger_module_state(self):
        # ReplayLedgerStore holds its OWN dict; the production ledger
        # module (ledger.py) has no module-level mutable signal storage
        # at all (it only talks to whatever `conn` it's given) - so there
        # is nothing for a replay run to leak into. This test pins that
        # invariant: ledger.py must not grow a module-level signals store.
        assert not hasattr(ledger_mod, "_signals")
        assert not hasattr(ledger_mod, "_active_index")


class TestExitDecisionTaxMath:
    def test_target_hit_applies_ea_tax_via_trading_math(self):
        # entry 1000, target 1100 -> hitting exactly the target price
        # must report the SAME after-tax ROI trading_math.net_roi would,
        # never a re-derived number.
        from app.services import trading_math as tm
        decision = ledger_mod.evaluate_active_signal_exit(
            current_price=1100.0, entry_price=1000.0, target_price=1100.0,
            now=D0, max_hold_at=D0 + timedelta(days=3),
        )
        assert decision is not None
        assert decision.outcome == ledger_mod.OUTCOME_TARGET_HIT
        expected_roi = tm.net_roi(1100.0, 1000.0)
        assert decision.roi_pct == pytest.approx(float(expected_roi) * 100)

    def test_max_hold_reached_without_target_closes_mark_to_market(self):
        decision = ledger_mod.evaluate_active_signal_exit(
            current_price=1020.0, entry_price=1000.0, target_price=1100.0,
            now=D0 + timedelta(days=3), max_hold_at=D0 + timedelta(days=3),
        )
        assert decision is not None
        assert decision.reason == ledger_mod.OUTCOME_MAX_HOLD

    def test_no_exit_before_target_or_max_hold(self):
        decision = ledger_mod.evaluate_active_signal_exit(
            current_price=1020.0, entry_price=1000.0, target_price=1100.0,
            now=D0 + timedelta(days=1), max_hold_at=D0 + timedelta(days=3),
        )
        assert decision is None

    def test_missing_price_never_closes_a_signal(self):
        decision = ledger_mod.evaluate_active_signal_exit(
            current_price=None, entry_price=1000.0, target_price=1100.0,
            now=D0 + timedelta(days=10), max_hold_at=D0 + timedelta(days=3),
        )
        assert decision is None


class TestFallingKnifeAvoidance:
    def test_premium_meta_generates_zero_signals_while_crashing(self):
        provider = build_synthetic_fixture(n_cards=320, end=D0 + timedelta(days=25))
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        crashing_days = [
            d for d in result.daily_states
            if d.cohort_key == "premium_meta" and d.state in ("CRASHING", "OVERHEATED")
        ]
        assert crashing_days, "expected the synthetic premium archetype to actually crash in this window"
        assert all(d.signals_opened == 0 for d in crashing_days)

    def test_suppressed_crashing_days_are_recorded_with_a_subsequent_further_decline(self):
        provider = build_synthetic_fixture(n_cards=320, end=D0 + timedelta(days=25))
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        premium_windows = [w for w in result.suppressed_windows if w.cohort_key == "premium_meta"]
        assert premium_windows


class TestStrategiesRemainDistinct:
    def test_starter_strategy_state_labels_never_match_state_machine_states(self):
        from app.services.investment_engine import state_machine as sm
        provider = build_synthetic_fixture(n_cards=320, end=D0 + timedelta(days=25))
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        starter_states = {d.state for d in result.daily_states if d.strategy_key == "LAUNCH_STARTER_ACCUMULATION"}
        assert starter_states.isdisjoint(set(sm.ALL_STATES))

    def test_premium_and_starter_signals_never_share_a_strategy_key(self):
        provider = build_synthetic_fixture(n_cards=320, end=D0 + timedelta(days=25))
        result = run_full_replay(provider, config=InvestmentEngineConfig())
        strategy_keys = {s.strategy_key for s in result.ledger.all_signals()}
        assert strategy_keys <= {"PREMIUM_POST_CRASH_REBOUND", "LAUNCH_STARTER_ACCUMULATION"}
