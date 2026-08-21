# tests/test_launch_market_safety.py
#
# The new-game-cycle launch safety layer:
#   * the Opportunity Engine's unverifiable-discount guard
#     (futgg_intelligence.py) - the specific launch-day hole where a card
#     repricing downward fast reads as a bargain against its own
#     backward-looking sales median, because the trend layer has no data
#     yet to contradict it.
#   * game_cycle.py - the single source of truth for which game year is
#     active, and the consistency check that catches the silent
#     "ingestion on FC26, engine on FC27" misconfiguration.
#   * investment_engine/readiness.py - "insufficient data is a valid
#     state", with named suppression reasons.
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import game_cycle as gc
from app.services.futgg_config import EngineConfig
from app.services.futgg_intelligence import evaluate_card
from app.services.investment_engine import readiness as rd
from app.services.investment_engine.config import InvestmentEngineConfig

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _sales(n, price, spread=0):
    return [
        {"sold_price": price + ((i * 211) % (spread or 1)),
         "approximate_sold_at": NOW - timedelta(minutes=i + 1)}
        for i in range(n)
    ]


def _snapshot(current_bin, *, sales_count, sales_median, dispersion, span_minutes=60.0, **overrides):
    snap = {
        "source_card_id": 1, "name": "Test", "rating": 90,
        "price_tier": "special", "is_tradeable": True,
        "current_bin": current_bin,
        "sales_count": sales_count, "sales_median": sales_median,
        "sales_trimmed_mean": sales_median,
        "sales_low": int(sales_median * 0.97), "sales_high": int(sales_median * 1.03),
        "sales_dispersion_ratio": dispersion, "sales_interdecile_ratio": 0.03,
        "sales_window_span_minutes": span_minutes,
        "latest_sale_at": NOW - timedelta(minutes=1),
        "bin_checked_at": NOW - timedelta(minutes=3),
        "bin_captured_at": NOW - timedelta(minutes=3),
    }
    snap.update(overrides)
    return snap


class TestUnverifiableDiscountGuard:
    """The launch-day hole: insufficient_trend_data CAPS a buy but does
    not BLOCK one, and in a new cycle's first hours every card is
    insufficient_trend_data."""

    def test_thin_scattered_sales_with_a_huge_discount_is_not_a_buy(self):
        # The new-cycle shape: a card that has repriced hard downward.
        # Few sales, scattered, median still reflecting the older level.
        # This is precisely the card that must NOT be published as a buy.
        snap = _snapshot(40_000, sales_count=6, sales_median=100_000, dispersion=0.30)
        ci = evaluate_card(snap, as_of=NOW, sales=_sales(6, 100_000, spread=30_000))
        assert ci.signal not in ("buy", "strong_buy")

    def test_the_reason_is_explicit_not_silent(self):
        snap = _snapshot(40_000, sales_count=6, sales_median=100_000, dispersion=0.30)
        ci = evaluate_card(snap, as_of=NOW, sales=_sales(6, 100_000, spread=30_000))
        # Either the guard fired, or an earlier gate (dispersion/trend)
        # already blocked it - but SOME explicit reason must be present.
        # A card in free-fall must never be declined silently.
        assert ci.reason_codes, "a suppressed card must always carry a reason"

    def test_an_established_level_is_still_a_buy(self):
        """The qualifier that keeps this guard from eating the core snipe
        product: many tightly-clustered sales establish a real level, so
        a BIN far below it is a genuine mispricing even with no trend
        reading available."""
        snap = _snapshot(66_500, sales_count=30, sales_median=98_000, dispersion=0.02)
        ci = evaluate_card(snap, as_of=NOW, sales=_sales(30, 98_000, spread=3_000))
        assert ci.signal in ("buy", "strong_buy")

    def test_a_modest_discount_on_thin_sales_is_unaffected(self):
        """The guard targets IMPLAUSIBLY large discounts, not ordinary
        ones - a small edge on thin data is governed by the existing
        confidence/ROI bars, not by this."""
        snap = _snapshot(94_000, sales_count=6, sales_median=100_000, dispersion=0.05)
        ci = evaluate_card(snap, as_of=NOW, sales=_sales(6, 100_000, spread=2_000))
        # Whatever it is, it must not have been blocked BY THIS GUARD.
        from app.services import futgg_reasons as rc
        assert rc.UNVERIFIABLE_DISCOUNT not in ci.reason_codes

    def test_guard_can_be_disabled_by_config(self):
        cfg = EngineConfig(unverifiable_discount_guard_enabled=False)
        snap = _snapshot(40_000, sales_count=6, sales_median=100_000, dispersion=0.10)
        ci = evaluate_card(snap, as_of=NOW, sales=_sales(6, 100_000, spread=5_000), config=cfg)
        from app.services import futgg_reasons as rc
        assert rc.UNVERIFIABLE_DISCOUNT not in ci.reason_codes

    def test_guard_is_general_not_launch_date_dependent(self):
        """Explicitly pinned: the guard must contain NO notion of a launch
        date or game year. It is a statement about evidence, true at any
        time - not a rule tuned to one historical launch."""
        import inspect
        from app.services import futgg_intelligence as fi
        source = inspect.getsource(fi.evaluate_card)
        for forbidden in ("game_year", "launch_date", "FULL_RELEASE", "web_app_launch"):
            assert forbidden not in source


class TestGameCycleSingleSourceOfTruth:
    def test_default_game_year_is_explicit(self):
        assert gc.GAME_CYCLE.game_year == gc.active_game_year()

    def test_consistency_report_flags_a_split_brain_configuration(self, monkeypatch):
        """The silent failure this module exists to prevent: ingestion
        writing one game year while the Investment Engine queries
        another produces no error anywhere, just an engine that
        mysteriously finds nothing."""
        monkeypatch.setenv("FUTGG_GAME_YEAR", "26")
        monkeypatch.setenv("INVESTMENT_ENGINE_GAME_YEAR", "27")
        report = gc.consistency_report()
        assert report["consistent"] is False
        assert report["problems"]

    def test_consistency_report_is_clean_when_nothing_overrides(self, monkeypatch):
        monkeypatch.delenv("FUTGG_GAME_YEAR", raising=False)
        monkeypatch.delenv("INVESTMENT_ENGINE_GAME_YEAR", raising=False)
        assert gc.consistency_report()["consistent"] is True

    def test_unset_launch_date_is_unknown_not_a_guess(self):
        cycle = gc.GameCycle(game_year=27, web_app_launch=None, early_access=None, full_release=None)
        assert cycle.phase() == gc.PHASE_UNKNOWN
        # None, never 0 - a 0 would read as "launch just happened".
        assert cycle.hours_since_launch() is None

    def test_phases_progress_in_calendar_order(self):
        cycle = gc.GameCycle(
            game_year=27,
            web_app_launch=date(2026, 9, 17),
            early_access=date(2026, 9, 19),
            full_release=date(2026, 9, 26),
        )

        def at(d):
            return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)

        assert cycle.phase(at(date(2026, 9, 10))) == gc.PHASE_PRE_LAUNCH
        assert cycle.phase(at(date(2026, 9, 17))) == gc.PHASE_WEB_APP
        assert cycle.phase(at(date(2026, 9, 20))) == gc.PHASE_EARLY_ACCESS
        assert cycle.phase(at(date(2026, 9, 30))) == gc.PHASE_FULL_RELEASE
        assert cycle.phase(at(date(2026, 12, 1))) == gc.PHASE_ESTABLISHED

    def test_phase_is_never_used_to_gate_an_investment_decision(self):
        """Pinned deliberately: launch phase is a REPORTING label. If a
        strategy ever starts gating on it, that is the calendar-hardcoding
        the FC26 replay work explicitly warned against."""
        import inspect
        from app.services.investment_engine import state_machine, ranking
        from app.services.investment_engine.strategies import premium_post_crash_rebound
        for module in (state_machine, ranking, premium_post_crash_rebound):
            source = inspect.getsource(module)
            assert "game_cycle" not in source
            assert "PHASE_" not in source


class TestReadinessSuppression:
    def _coverage(self, **overrides):
        base = dict(
            cards_in_cohort=200, cards_with_price=200, cards_with_fresh_price=200,
            cards_with_recent_sales=100, market_state_observations=30,
            max_price_age_minutes=10.0,
        )
        base.update(overrides)
        return rd.CoverageInputs(**base)

    def test_a_healthy_cohort_is_ready(self):
        result = rd.evaluate_readiness(self._coverage(), InvestmentEngineConfig())
        assert result.ready is True
        assert result.reasons == []

    def test_a_brand_new_cycle_is_suppressed_with_a_named_reason(self):
        # Hour one: a handful of cards discovered, nothing else.
        coverage = self._coverage(
            cards_in_cohort=3, cards_with_price=2, cards_with_fresh_price=2,
            cards_with_recent_sales=None, market_state_observations=0,
        )
        result = rd.evaluate_readiness(coverage, InvestmentEngineConfig())
        assert result.ready is False
        assert rd.INSUFFICIENT_CARD_COVERAGE in result.reasons
        assert result.primary_reason in rd.ALL_SUPPRESSION_REASONS

    def test_stale_prices_suppress_even_with_a_full_cohort(self):
        coverage = self._coverage(cards_with_fresh_price=20)  # 20/200 fresh
        result = rd.evaluate_readiness(coverage, InvestmentEngineConfig())
        assert result.ready is False
        assert rd.STALE_MARKET_DATA in result.reasons

    def test_no_price_history_suppresses(self):
        coverage = self._coverage(market_state_observations=1)
        result = rd.evaluate_readiness(coverage, InvestmentEngineConfig())
        assert result.ready is False
        assert rd.INSUFFICIENT_PRICE_HISTORY in result.reasons

    def test_missing_sales_pipeline_is_distinct_from_zero_sales(self):
        """'We do not have sales data' must never be silently coerced into
        'this card has no demand'."""
        config = InvestmentEngineConfig()
        unknown = rd.evaluate_readiness(
            self._coverage(cards_with_recent_sales=None), config, require_sales=True,
        )
        measured_zero = rd.evaluate_readiness(
            self._coverage(cards_with_recent_sales=0), config, require_sales=True,
        )
        # Both suppress, but the detail preserves which situation it was.
        assert unknown.detail["cards_with_recent_sales"] is None
        assert measured_zero.detail["cards_with_recent_sales"] == 0

    def test_sales_are_only_required_when_the_caller_says_so(self):
        config = InvestmentEngineConfig()
        coverage = self._coverage(cards_with_recent_sales=None)
        assert rd.evaluate_readiness(coverage, config, require_sales=False).ready is True
        assert rd.evaluate_readiness(coverage, config, require_sales=True).ready is False

    def test_every_reason_has_plain_language_copy(self):
        """Operator and user surfaces must never have to render a raw
        enum. Also pins that copy stays jargon-free."""
        for reason in rd.ALL_SUPPRESSION_REASONS:
            assert reason in rd.REASON_COPY
            copy = rd.REASON_COPY[reason]
            assert copy and copy[0].isupper()
            for jargon in ("percentile", "z-score", "coefficient", "deceleration", "_pct"):
                assert jargon not in copy.lower()
