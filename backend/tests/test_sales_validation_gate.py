# tests/test_sales_validation_gate.py
#
# Pure tests for app/services/sales_validation_gate.py - no DB, no I/O.
#
# What these protect is a hosting bill. The gate decides which cards get a
# headed-Chromium page navigation, so the tests that matter most are the
# ones proving it CANNOT be made to spend without limit: cooldowns,
# per-card caps, anomaly caps and a global budget that discovery traffic
# can never eat into.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.bin_market_signal import (
    MOVING_DOWN, POSSIBLE_SPIKE, POSSIBLE_UNDERCUT, STABLE, VOLATILE,
    BinMarketSignal,
)
from app.services.sales_validation_gate import (
    BIN_SPIKE_VALIDATION, BIN_UNDERCUT_VALIDATION, BIN_BASELINE_LAGGING,
    EVIDENCE_SALES_CONFIRMED, INSUFFICIENT_TRANSACTION_EVIDENCE, MANUAL,
    NEVER_READ_BACKFILL, PERIODIC_DISCOVERY, REASON_PRIORITY,
    SALES_CONFIRMED_UNDERCUT, SBC_RESOURCE_DEMAND, SKIP_BUDGET_EXHAUSTED,
    SKIP_COOLDOWN, SKIP_EVIDENCE_FRESH, SKIP_NOT_ELIGIBLE,
    SKIP_PER_CARD_LIMIT, TRADE_CANDIDATE, USER_POSITION, WATCHLIST_INTEREST,
    BudgetState, CardContext, EligibilityDecision, SalesValidationConfig,
    DISCOVERY_INCONCLUSIVE, DISCOVERY_NO_USEFUL_DIFFERENCE,
    DISCOVERY_OPPORTUNITY_MISSED, DISCOVERY_USEFUL_LIQUIDITY,
    apply_budget, bin_first_engine_enabled, classify_card,
    classify_discovery_result, eligibility_gate_enabled, evaluate_eligibility,
    request_budget_enabled, select_discovery_sample, validate_against_sales,
)

AS_OF = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
CONFIG = SalesValidationConfig()


def _signal(**overrides) -> BinMarketSignal:
    defaults = dict(
        card_id=1, as_of=AS_OF, current_bin=42000, observed_at=AS_OF,
        median_24h=50000.0, median_1h=50000.0, median_6h=50000.0,
        observation_count_24h=200, observation_count_1h=12,
        price_persistence=0.08, anomaly_score=9.0, market_state=STABLE,
    )
    defaults.update(overrides)
    return BinMarketSignal(**defaults)


def _ctx(**overrides) -> CardContext:
    defaults = dict(
        card_id=1, rating=84, is_active=True, is_tradeable=True,
        latest_sale_at=AS_OF - timedelta(days=10), sales_count_14d=20,
        never_read=False,
        anomaly_persistence_observations=CONFIG.min_anomaly_persistence_observations,
    )
    defaults.update(overrides)
    return CardContext(**defaults)


class TestKillSwitchesDefaultOff:
    def test_nothing_is_enabled_without_explicit_opt_in(self, monkeypatch):
        for var in (
            "BIN_FIRST_MARKET_ENGINE_ENABLED",
            "SALES_SYNC_ELIGIBILITY_GATE_ENABLED",
            "SALES_SYNC_REQUEST_BUDGET_ENABLED",
        ):
            monkeypatch.delenv(var, raising=False)
        assert bin_first_engine_enabled() is False
        assert eligibility_gate_enabled() is False
        assert request_budget_enabled() is False


class TestTheDefaultStateIsCheap:
    def test_an_ordinary_dormant_card_earns_nothing(self):
        """The whole saving. ~10k cards with no user interest, no SBC
        relevance and a flat asking price must simply stop being scraped
        on a timer."""
        decision = evaluate_eligibility(
            _ctx(rating=64, sales_count_14d=0), _signal(market_state=STABLE),
            as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is False
        assert decision.skip_reason == SKIP_NOT_ELIGIBLE
        assert decision.card_class == "dormant"

    def test_a_stable_high_rated_card_is_cold_not_active(self):
        decision = evaluate_eligibility(
            _ctx(rating=91), _signal(market_state=STABLE), as_of=AS_OF, config=CONFIG,
        )
        assert decision.card_class == "cold"
        assert decision.eligible is False

    def test_an_untradeable_card_is_never_eligible(self):
        decision = evaluate_eligibility(
            _ctx(is_tradeable=False, is_trade_candidate=True),
            _signal(market_state=POSSIBLE_UNDERCUT), as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is False
        assert "inactive_or_untradeable" in decision.notes


class TestEarningARequest:
    def test_a_bin_undercut_earns_a_validation(self):
        decision = evaluate_eligibility(
            _ctx(), _signal(market_state=POSSIBLE_UNDERCUT), as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is True
        assert decision.reason == BIN_UNDERCUT_VALIDATION
        assert decision.card_class == "active"

    def test_a_trade_candidate_outranks_a_bin_anomaly(self):
        decision = evaluate_eligibility(
            _ctx(is_trade_candidate=True), _signal(market_state=POSSIBLE_UNDERCUT),
            as_of=AS_OF, config=CONFIG,
        )
        assert decision.reason == TRADE_CANDIDATE

    def test_a_user_position_earns_a_request_on_a_stable_card(self):
        decision = evaluate_eligibility(
            _ctx(has_user_position=True), _signal(market_state=STABLE),
            as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is True
        assert decision.reason == USER_POSITION

    def test_a_never_read_card_always_keeps_its_backfill_path(self):
        """select_due()'s never-read-first rule exists so no card is
        stranded permanently at zero sales. Removing that wake-up path
        while cutting cost would silently strand the tail of the
        catalogue forever."""
        decision = evaluate_eligibility(
            _ctx(never_read=True, latest_sale_at=None, rating=52),
            _signal(market_state=STABLE), as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is True
        assert decision.reason == NEVER_READ_BACKFILL

    def test_external_demand_is_optional_not_required(self):
        """BIN-first must work while the SBC layer is mid-revision - the
        explicit constraint. A card with zero SBC signal still earns a
        request on BIN movement alone."""
        decision = evaluate_eligibility(
            _ctx(sbc_resource_demand_weight=0.0, has_constrained_sbc_trigger=False),
            _signal(market_state=POSSIBLE_SPIKE), as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is True
        assert decision.reason == BIN_SPIKE_VALIDATION


class TestAntiCostExplosionSafeguards:
    def test_fresh_evidence_means_there_is_nothing_to_buy(self):
        decision = evaluate_eligibility(
            _ctx(latest_sale_at=AS_OF - timedelta(minutes=5)),
            _signal(market_state=POSSIBLE_UNDERCUT), as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is False
        assert decision.skip_reason == SKIP_EVIDENCE_FRESH

    def test_cooldown_blocks_re_validation_however_loud_the_signal(self):
        """Without this a card oscillating around a threshold re-triggers
        on every bulk cycle - 288 page loads a day for one card."""
        decision = evaluate_eligibility(
            _ctx(last_validated_at=AS_OF - timedelta(minutes=10)),
            _signal(market_state=POSSIBLE_UNDERCUT, anomaly_score=99.0),
            as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is False
        assert decision.skip_reason == SKIP_COOLDOWN

    def test_per_card_daily_cap_stops_one_card_eating_the_budget(self):
        decision = evaluate_eligibility(
            _ctx(validations_today=CONFIG.max_validations_per_card_per_day),
            _signal(market_state=POSSIBLE_UNDERCUT), as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is False
        assert decision.skip_reason == SKIP_PER_CARD_LIMIT

    def test_an_anomaly_that_has_not_persisted_cannot_spend(self):
        """The bulk feed demonstrably produces malformed observations. One
        of them must never be sufficient to buy a page load."""
        decision = evaluate_eligibility(
            _ctx(anomaly_persistence_observations=0),
            _signal(market_state=POSSIBLE_UNDERCUT), as_of=AS_OF, config=CONFIG,
        )
        assert decision.eligible is False

    def test_a_thin_market_needs_a_stronger_anomaly(self):
        # FC26 is full of cards whose ask wanders because listings expire.
        weak = evaluate_eligibility(
            _ctx(), _signal(market_state=POSSIBLE_UNDERCUT, anomaly_score=5.0, thin_market=True),
            as_of=AS_OF, config=CONFIG,
        )
        strong = evaluate_eligibility(
            _ctx(), _signal(market_state=POSSIBLE_UNDERCUT, anomaly_score=12.0, thin_market=True),
            as_of=AS_OF, config=CONFIG,
        )
        assert weak.eligible is False
        assert strong.eligible is True


class TestBudget:
    def _eligible(self, n, reason, start=100):
        return [
            EligibilityDecision(
                card_id=start + i, eligible=True, reason=reason,
                urgency=REASON_PRIORITY.index(reason), card_class="active",
            )
            for i in range(n)
        ]

    def test_the_budget_is_a_hard_ceiling(self):
        config = SalesValidationConfig(max_requests_per_hour=10, max_requests_per_day=10)
        decisions = self._eligible(50, TRADE_CANDIDATE)
        admitted = apply_budget(decisions, BudgetState(), config=config)
        assert len(admitted) == 10
        assert all(d.skip_reason == SKIP_BUDGET_EXHAUSTED for d in decisions if not d.eligible)

    def test_trade_candidates_win_over_discovery_when_capacity_is_short(self):
        """The explicit requirement: discovery traffic must never consume
        capacity a genuine opportunity needed."""
        config = SalesValidationConfig(max_requests_per_hour=5, max_requests_per_day=5)
        decisions = (
            self._eligible(5, PERIODIC_DISCOVERY, start=200)
            + self._eligible(5, TRADE_CANDIDATE, start=300)
        )
        admitted = apply_budget(decisions, BudgetState(), config=config)
        assert len(admitted) == 5
        assert all(d.reason == TRADE_CANDIDATE for d in admitted)

    def test_priority_order_is_deterministic(self):
        config = SalesValidationConfig(max_requests_per_hour=3, max_requests_per_day=3)
        decisions = (
            self._eligible(1, WATCHLIST_INTEREST, start=400)
            + self._eligible(1, USER_POSITION, start=500)
            + self._eligible(1, TRADE_CANDIDATE, start=600)
            + self._eligible(1, SBC_RESOURCE_DEMAND, start=700)
        )
        admitted = apply_budget(decisions, BudgetState(), config=config)
        assert [d.reason for d in admitted] == [
            TRADE_CANDIDATE, USER_POSITION, SBC_RESOURCE_DEMAND,
        ]

    def test_anomaly_validations_are_capped_per_cycle(self):
        """A market-wide event moves thousands of cards at once. Without a
        separate per-cycle cap that converts directly into thousands of
        page loads."""
        config = SalesValidationConfig(
            max_anomaly_validations_per_cycle=5,
            max_requests_per_hour=1000, max_requests_per_day=1000,
        )
        decisions = self._eligible(100, BIN_UNDERCUT_VALIDATION)
        admitted = apply_budget(decisions, BudgetState(), config=config)
        assert len(admitted) == 5

    def test_discovery_has_its_own_ceiling_below_the_global_one(self):
        config = SalesValidationConfig(
            discovery_sample_per_day=3, max_requests_per_hour=100, max_requests_per_day=100,
        )
        decisions = self._eligible(50, PERIODIC_DISCOVERY)
        admitted = apply_budget(decisions, BudgetState(), config=config)
        assert len(admitted) == 3

    def test_already_spent_budget_is_respected(self):
        config = SalesValidationConfig(max_requests_per_hour=10, max_requests_per_day=1000)
        decisions = self._eligible(10, TRADE_CANDIDATE)
        admitted = apply_budget(decisions, BudgetState(requests_this_hour=8), config=config)
        assert len(admitted) == 2


class TestValidationOutcome:
    def test_sales_still_at_the_old_level_confirm_a_real_undercut(self):
        signal = _signal(market_state=POSSIBLE_UNDERCUT, current_bin=36000, median_24h=42000.0)
        outcome = validate_against_sales(
            signal, reason=BIN_UNDERCUT_VALIDATION,
            sales_prices=[41500, 42000, 42250, 41750, 42000, 42500],
        )
        assert outcome.outcome == SALES_CONFIRMED_UNDERCUT
        assert outcome.evidence_type == EVIDENCE_SALES_CONFIRMED

    def test_sales_that_followed_the_ask_mean_our_baseline_was_stale(self):
        signal = _signal(market_state=POSSIBLE_UNDERCUT, current_bin=36000, median_24h=42000.0)
        outcome = validate_against_sales(
            signal, reason=BIN_UNDERCUT_VALIDATION,
            sales_prices=[35500, 36000, 36250, 35750, 36000, 36100],
        )
        assert outcome.outcome == BIN_BASELINE_LAGGING

    def test_too_few_sales_is_inconclusive_never_a_confirmation(self):
        signal = _signal(market_state=POSSIBLE_UNDERCUT, current_bin=36000, median_24h=42000.0)
        outcome = validate_against_sales(
            signal, reason=BIN_UNDERCUT_VALIDATION, sales_prices=[42000, 41000],
        )
        assert outcome.outcome == INSUFFICIENT_TRANSACTION_EVIDENCE
        assert "too_few_sales" in outcome.notes

    def test_a_zero_price_series_is_inconclusive_not_a_crash(self):
        signal = _signal(market_state=POSSIBLE_UNDERCUT, current_bin=36000, median_24h=42000.0)
        outcome = validate_against_sales(
            signal, reason=BIN_UNDERCUT_VALIDATION, sales_prices=[0, 0, 0, 0, 0, 0],
        )
        assert outcome.outcome == INSUFFICIENT_TRANSACTION_EVIDENCE


class TestClassification:
    def test_classes_cover_the_documented_spread(self):
        assert classify_card(_ctx(is_trade_candidate=True), _signal(), CONFIG) == "active"
        assert classify_card(_ctx(), _signal(market_state=POSSIBLE_UNDERCUT), CONFIG) == "active"
        assert classify_card(_ctx(watcher_count=3), _signal(), CONFIG) == "warm"
        assert classify_card(_ctx(), _signal(market_state=VOLATILE), CONFIG) == "warm"
        assert classify_card(_ctx(rating=91), _signal(), CONFIG) == "cold"
        assert classify_card(
            _ctx(rating=60, sales_count_14d=0), _signal(), CONFIG
        ) == "dormant"

    def test_each_class_has_a_progressively_looser_evidence_tolerance(self):
        ages = [CONFIG.max_evidence_age_minutes(c)
                for c in ("active", "warm", "cold", "dormant")]
        assert ages == sorted(ages)


class TestDiscoverySampling:
    """The control sample. Without it BIN-first is self-confirming: it
    only ever pays to look at cards it already found interesting, so
    "we missed nothing" is guaranteed by construction rather than
    measured. This is what makes the false-negative rate observable, and
    it is therefore a precondition for trusting suppression."""

    def _skipped(self, n, card_class, start=1000):
        return [
            EligibilityDecision(
                card_id=start + i, eligible=False, skip_reason=SKIP_NOT_ELIGIBLE,
                card_class=card_class,
            )
            for i in range(n)
        ]

    def test_selection_is_bounded_by_the_daily_cap(self):
        config = SalesValidationConfig(
            discovery_sample_per_day=20, max_requests_per_day=100000,
        )
        sampled = select_discovery_sample(
            self._skipped(5000, "dormant"), as_of=AS_OF, config=config,
        )
        assert 0 < len(sampled) <= 20

    def test_selection_is_reproducible_for_the_same_time_bucket(self):
        """Deterministic on purpose: 'why was this card sampled?' has to
        be answerable months later from two integers. True randomness
        would buy nothing and cost that."""
        config = SalesValidationConfig(discovery_sample_per_day=25)
        first = select_discovery_sample(
            self._skipped(2000, "dormant"), as_of=AS_OF, config=config,
        )
        second = select_discovery_sample(
            self._skipped(2000, "dormant"), as_of=AS_OF, config=config,
        )
        assert [d.card_id for d in first] == [d.card_id for d in second]

    def test_selection_rotates_across_days(self):
        config = SalesValidationConfig(discovery_sample_per_day=40)
        day1 = select_discovery_sample(
            self._skipped(3000, "dormant"), as_of=AS_OF, config=config,
        )
        day2 = select_discovery_sample(
            self._skipped(3000, "dormant"),
            as_of=AS_OF + timedelta(days=1), config=config,
        )
        ids1, ids2 = {d.card_id for d in day1}, {d.card_id for d in day2}
        assert ids1 != ids2
        # Rotation, not a completely disjoint redraw - overlap is fine,
        # total identity is not.
        assert len(ids1 & ids2) < len(ids1)

    def test_all_populations_are_represented_not_just_the_biggest(self):
        """An unstratified sample of this catalogue would be ~80% dormant
        low-rated cards and would say nothing about the warm population,
        where a missed signal actually costs something."""
        config = SalesValidationConfig(discovery_sample_per_day=60)
        candidates = (
            self._skipped(4000, "dormant", start=1000)
            + self._skipped(800, "cold", start=20000)
            + self._skipped(200, "warm", start=40000)
        )
        sampled = select_discovery_sample(candidates, as_of=AS_OF, config=config)
        strata = {d.card_class for d in sampled}
        assert strata == {"dormant", "cold", "warm"}

    def test_every_sampled_card_is_auditable_as_periodic_discovery(self):
        config = SalesValidationConfig(discovery_sample_per_day=15)
        sampled = select_discovery_sample(
            self._skipped(1000, "cold"), as_of=AS_OF, config=config,
        )
        assert sampled
        for d in sampled:
            assert d.reason == PERIODIC_DISCOVERY
            assert d.eligible is True
            assert d.skip_reason is None
            assert any(n.startswith("discovery_bucket=") for n in d.notes)
            assert any(n.startswith("discovery_stratum=") for n in d.notes)

    def test_only_skipped_cards_are_ever_promoted(self):
        # Promoting an already-eligible card is not a control observation
        # and would double-count it against the budget.
        already = EligibilityDecision(
            card_id=999, eligible=True, reason=TRADE_CANDIDATE, card_class="active",
        )
        sampled = select_discovery_sample([already], as_of=AS_OF)
        assert sampled == []

    def test_a_card_skipped_for_cooldown_is_not_treated_as_a_control(self):
        # It was skipped because we JUST paid for it, not because the gate
        # judged it uninteresting - sampling it measures nothing.
        cooling = EligibilityDecision(
            card_id=998, eligible=False, skip_reason=SKIP_COOLDOWN, card_class="dormant",
        )
        assert select_discovery_sample([cooling], as_of=AS_OF) == []

    def test_the_days_budget_is_respected_across_repeated_passes(self):
        config = SalesValidationConfig(discovery_sample_per_day=30)
        sampled = select_discovery_sample(
            self._skipped(2000, "dormant"), as_of=AS_OF, config=config,
            already_sampled_today=30,
        )
        assert sampled == []

    def test_discovery_never_exceeds_its_share_of_the_global_budget(self):
        # Belt and braces: the count cap AND a share-of-budget cap.
        config = SalesValidationConfig(
            discovery_sample_per_day=10000, max_requests_per_day=200,
            discovery_max_budget_share=0.05,
        )
        sampled = select_discovery_sample(
            self._skipped(5000, "dormant"), as_of=AS_OF, config=config,
        )
        assert len(sampled) <= 10   # 5% of 200

    def test_discovery_loses_to_every_real_reason_under_pressure(self):
        config = SalesValidationConfig(max_requests_per_hour=2, max_requests_per_day=2)
        discovery = select_discovery_sample(
            self._skipped(500, "dormant"), as_of=AS_OF,
            config=SalesValidationConfig(discovery_sample_per_day=50),
        )
        real = [
            EligibilityDecision(
                card_id=7001, eligible=True, reason=TRADE_CANDIDATE,
                urgency=REASON_PRIORITY.index(TRADE_CANDIDATE), card_class="active",
            ),
            EligibilityDecision(
                card_id=7002, eligible=True, reason=USER_POSITION,
                urgency=REASON_PRIORITY.index(USER_POSITION), card_class="active",
            ),
        ]
        admitted = apply_budget(real + discovery, BudgetState(), config=config)
        assert [d.reason for d in admitted] == [TRADE_CANDIDATE, USER_POSITION]
        assert all(d.reason != PERIODIC_DISCOVERY for d in admitted)

    def test_discovery_is_dropped_first_when_the_budget_runs_out(self):
        # 5% of 20 admits exactly one discovery scrape; the hourly cap of
        # 1 then closes the door on the rest. (A day budget of 1 would
        # round the 5% share to zero and admit none at all - correct, but
        # it would not exercise the drop path.)
        config = SalesValidationConfig(max_requests_per_hour=1, max_requests_per_day=20)
        discovery = select_discovery_sample(
            self._skipped(200, "dormant"), as_of=AS_OF,
            config=SalesValidationConfig(discovery_sample_per_day=20),
        )
        assert discovery
        admitted = apply_budget(list(discovery), BudgetState(), config=config)
        assert len(admitted) == 1
        dropped = [d for d in discovery if not d.eligible]
        assert dropped and all(d.skip_reason == SKIP_BUDGET_EXHAUSTED for d in dropped)


class TestDiscoveryResultClassification:
    """What a control scrape actually taught us. If this keeps returning
    NO_USEFUL_DIFFERENCE the suppression is safe; a material rate of
    POTENTIAL_OPPORTUNITY_MISSED means the headline reduction is being
    bought with lost signal."""

    def test_sales_agreeing_with_the_bin_baseline_taught_us_nothing(self):
        signal = _signal(current_bin=42000, median_24h=42000.0)
        assert classify_discovery_result(
            signal, sales_prices=[41800, 42000, 42200, 41900, 42100, 42000],
        ) == DISCOVERY_NO_USEFUL_DIFFERENCE

    def test_sales_well_above_the_ask_are_an_opportunity_the_gate_missed(self):
        # Buyable at 30,000, actually trading at 42,000, and nothing in
        # the BIN stream flagged it. This is the failure mode that would
        # invalidate the whole reduction.
        signal = _signal(current_bin=30000, median_24h=30000.0)
        assert classify_discovery_result(
            signal, sales_prices=[42000, 41500, 42500, 42000, 41800, 42200],
        ) == DISCOVERY_OPPORTUNITY_MISSED

    def test_too_few_sales_is_inconclusive_and_that_is_informative(self):
        # It says the card has no tradeable market, which is why it was
        # skipped. Not a miss.
        signal = _signal(current_bin=42000, median_24h=42000.0)
        assert classify_discovery_result(
            signal, sales_prices=[42000, 41000],
        ) == DISCOVERY_INCONCLUSIVE

    def test_a_card_with_no_bin_baseline_still_yields_liquidity_information(self):
        signal = _signal(current_bin=None, median_24h=None, market_state="INSUFFICIENT_BIN_HISTORY")
        assert classify_discovery_result(
            signal, sales_prices=[9000, 9200, 8800, 9100, 9000, 9050],
        ) == DISCOVERY_USEFUL_LIQUIDITY
