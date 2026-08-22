# tests/test_investment_publication_gate.py
#
# The three-state control the whole FC27 launch plan depends on:
#
#     calculate  (INVESTMENT_ENGINE_ENABLED)
#     shadow     (enabled, PUBLISH_SIGNALS off)  <-- the launch setting
#     publish    (enabled + PUBLISH_SIGNALS on)
#
# "I want to be able to deploy with the engine fully active while users
# receive zero unvalidated Investment calls." These tests pin exactly
# that, and pin that it cannot be bypassed accidentally.
from __future__ import annotations

import inspect

import pytest

from app.services.investment_engine import runner
from app.services.investment_engine.config import InvestmentEngineConfig


class TestFlagsDefaultSafe:
    def test_engine_is_inert_by_default(self):
        """An unset environment must never start publishing. Both flags
        default off, so a deploy that forgets to configure anything is
        silent rather than loud."""
        config = InvestmentEngineConfig()
        assert config.enabled is False
        assert config.publish_signals is False

    def test_publishing_is_independent_of_calculating(self, monkeypatch):
        monkeypatch.setenv("INVESTMENT_ENGINE_ENABLED", "true")
        monkeypatch.delenv("INVESTMENT_ENGINE_PUBLISH_SIGNALS", raising=False)
        config = InvestmentEngineConfig()
        assert config.enabled is True
        assert config.publish_signals is False  # the FC27 launch posture

    def test_publish_requires_its_own_explicit_flag(self, monkeypatch):
        monkeypatch.setenv("INVESTMENT_ENGINE_ENABLED", "true")
        monkeypatch.setenv("INVESTMENT_ENGINE_PUBLISH_SIGNALS", "true")
        config = InvestmentEngineConfig()
        assert config.publish_signals is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "  ", "maybe", "TRUE-ish"])
    def test_only_an_explicit_affirmative_enables_publication(self, monkeypatch, value):
        """A typo or an ambiguous value must fail CLOSED (shadow), never
        open. This is the difference between a quiet mistake and
        publishing unvalidated investment calls to real users."""
        monkeypatch.setenv("INVESTMENT_ENGINE_PUBLISH_SIGNALS", value)
        assert InvestmentEngineConfig().publish_signals is False


class TestShadowFlagReachesEveryWrite:
    def test_signals_are_written_shadow_unless_publishing(self):
        """Every open_signal call in the runner must derive `shadow` from
        the publish flag - a single call site hardcoding shadow=False
        would silently publish. Pinned structurally because it is not
        otherwise visible in review."""
        source = inspect.getsource(runner)
        assert "shadow=not config.publish_signals" in source
        # And nothing may hardcode the unsafe value.
        assert "shadow=False" not in source

    def test_decision_log_records_shadow_state_too(self):
        """The learning dataset must record whether a decision was made
        in shadow or in publication, or a later analysis cannot tell
        which decisions users actually saw."""
        source = inspect.getsource(runner)
        assert source.count("shadow=not config.publish_signals") >= 2


class TestApiNeverLeaksShadowSignals:
    def test_public_signal_listing_filters_shadow_rows(self):
        from app.routers.v2 import investments
        source = inspect.getsource(investments.list_signals)
        # The default path must exclude shadow rows...
        assert 's.shadow = FALSE' in source
        # ...and the only way past that is an explicit admin check.
        assert "await require_admin(request)" in source

    def test_ledger_endpoint_is_published_only(self):
        from app.routers.v2 import investments
        source = inspect.getsource(investments.get_ledger)
        assert "s.shadow = FALSE" in source

    def test_budget_plan_is_published_only(self):
        """The budget planner allocates real coins - it must never route
        a user's money into an unvalidated shadow signal."""
        from app.routers.v2 import investments
        source = inspect.getsource(investments.budget_plan)
        assert "shadow = FALSE" in source

    def test_shadow_report_is_admin_gated(self):
        from app.routers.v2 import investments
        source = inspect.getsource(investments.shadow_report)
        assert "await require_admin(request)" in source

    def test_market_state_is_public_by_design(self):
        """Market-state intelligence IS meant to be public from day one -
        it exposes a regime label, never a card-level call. Pinned so a
        future edit does not accidentally start returning card lists
        from this endpoint."""
        from app.routers.v2 import investments
        source = inspect.getsource(investments.get_market_state)
        assert "require_admin" not in source
        assert "investment_signals" not in source


class TestRunnerRespectsTheEnabledFlag:
    def test_disabled_engine_does_no_work_at_all(self):
        """Not merely 'publishes nothing' - a disabled engine must not
        even touch the database. asyncio.run rather than a plugin mark:
        this repo's test suite has no async plugin installed."""
        import asyncio

        class ExplodingPool:
            def acquire(self):
                raise AssertionError("disabled engine must not acquire a connection")

        config = InvestmentEngineConfig()
        assert config.enabled is False
        asyncio.run(runner.run_all_strategies(ExplodingPool(), game_year=27, config=config))
