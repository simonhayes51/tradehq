# tests/test_ai_chat_tools.py
#
# The assistant's guarantee is that every number it says came from a tool
# result. These tests cover the half of that guarantee this codebase
# controls: what the tools return, and what is allowed through to the
# model.
from __future__ import annotations

import asyncio

import pytest

from app.auth.access import Viewer
from app.auth.entitlements import features_for_tier
from app.routers.v2 import ai_chat


def _viewer(tier, user_id="u1"):
    return Viewer(user_id=user_id, tier=tier, is_admin=False,
                  features=frozenset(features_for_tier(tier)))


@pytest.fixture
def as_elite(monkeypatch):
    """The tool layer resolves the caller itself, so a test that wants to
    exercise a tool has to say who is calling."""
    async def _viewer_dep(_request):
        return _viewer("elite")
    monkeypatch.setattr(ai_chat, "get_viewer", _viewer_dep)


@pytest.fixture
def as_free(monkeypatch):
    async def _viewer_dep(_request):
        return _viewer("free")
    monkeypatch.setattr(ai_chat, "get_viewer", _viewer_dep)


def test_chat_safe_card_drops_internal_scoring_fields():
    detail = {
        "source_card_id": 42, "display_name": "Mbappé", "rating": 91, "rarity": "icon",
        "current_bin": 900_000, "price_age_minutes": 12,
        "recommended_buy_max": 880_000, "recommended_sell_target": 1_000_000,
        "expected_profit_after_tax": 70_000, "expected_roi": 0.08,
        "confidence_score": 0.71, "risk_level": "medium", "recent_sale_count": 18,
        "trend_description": "Rising steadily", "signal_reasons": ["a", "b", "c", "d", "e"],
        # Internal noise that must not reach the model.
        "failed_gate_reasons": ["price_age_gate"], "scoring_internals": {"z": 1},
        "intelligence": {"signal": "buy"},
    }
    safe = ai_chat._chat_safe_card(detail)
    assert safe["verdict"] == "buy"
    assert safe["current_price"] == 900_000
    assert safe["price_age_minutes"] == 12
    assert "failed_gate_reasons" not in safe
    assert "scoring_internals" not in safe
    # Reason lists are capped so a model can't be handed an essay of them.
    assert len(safe["reasons"]) == 4


def test_chat_safe_card_carries_price_age_so_staleness_can_be_stated():
    # The system prompt requires the model to disclose an old price. It
    # can only do that if the field is actually present, including when
    # every other number is missing.
    safe = ai_chat._chat_safe_card({"source_card_id": 1, "price_age_minutes": 400})
    assert safe["price_age_minutes"] == 400


def test_system_prompt_forbids_unsourced_numbers_and_predictions():
    prompt = ai_chat.SYSTEM_PROMPT.lower()
    assert "must come from a tool" in prompt
    assert "price_age_minutes" in prompt      # staleness disclosure rule
    assert "never predict" in prompt


def test_no_env_var_or_exception_names_in_customer_facing_strings():
    # A user must never be told to "set OPENAI_API_KEY", nor be shown a
    # Python exception class name - both were previously returned as the
    # assistant's own reply text.
    import inspect
    import re

    source = inspect.getsource(ai_chat.chat)
    user_facing = re.findall(r'raise HTTPException\(\d+, "([^"]+)"\)', source)
    assert user_facing, "expected the unavailable paths to raise with a message"
    for message in user_facing:
        assert "OPENAI" not in message.upper()
        assert "Error" not in message
        assert "type(" not in message


def test_status_reports_configuration(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert asyncio.run(ai_chat.chat_status())["configured"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert asyncio.run(ai_chat.chat_status())["configured"] is True


class _Track:
    """Stands in for GET /api/v2/track-record.

    Accepts the same keyword arguments the real route function does -
    ai_chat calls it exhaustively, because an omitted argument on a route
    function keeps its Depends() object instead of becoming a value."""

    def __init__(self, payload):
        self.payload = payload

    async def __call__(self, request, window="90d", viewer=None):
        return self.payload


def test_track_record_tool_withholds_percentages_below_the_sample_floor(monkeypatch, as_elite):
    monkeypatch.setattr(ai_chat, "track_record", _Track({
        "window_days": 90,
        "published": {"has_published_data": True, "has_enough_data": False, "wins": 3},
    }))
    result = asyncio.run(ai_chat._run_tool(None, "get_track_record", {}))
    assert result["has_enough_data"] is False
    assert "win_rate_pct" not in result


def test_track_record_tool_never_exposes_model_validation(monkeypatch, as_elite):
    # model_validation is an internal, much larger engine dataset that is
    # explicitly NOT a customer trade history. A model handed it would
    # paraphrase it as one.
    monkeypatch.setattr(ai_chat, "track_record", _Track({
        "window_days": 90,
        "published": {
            "has_published_data": True, "has_enough_data": True,
            "published_opportunities": 40, "wins": 30, "losses": 10,
            "win_rate_pct": 75.0, "median_roi_pct": 6.1,
        },
        "model_validation": {"graded_signals": 8000, "profitable_rate_pct": 91.0},
    }))
    result = asyncio.run(ai_chat._run_tool(None, "get_track_record", {}))
    assert result["win_rate_pct"] == 75.0
    assert "8000" not in str(result)
    assert "caveat" in result


def test_unknown_tool_is_an_error_not_a_silent_empty_answer(as_elite):
    assert "error" in asyncio.run(ai_chat._run_tool(None, "definitely_not_a_tool", {}))


class TestToolsCannotBypassThePlan:
    """The /chat endpoint is Elite-gated, so in normal operation no Free
    caller reaches a tool. These prove the tool layer refuses on its own
    anyway - the model must not become an unauthenticated proxy for the
    paid feed if a future change opens a second door to it."""

    @pytest.mark.parametrize("tool", ["resolve_and_evaluate_card",
                                       "find_trades_for_budget",
                                       "get_track_record"])
    def test_a_free_caller_is_refused_by_every_premium_tool(self, as_free, tool):
        result = asyncio.run(ai_chat._run_tool(None, tool, {"query": "Mbappe", "budget_max": 40000}))
        assert result["error"] == "not_available_on_this_plan"

    def test_the_refusal_tells_the_model_not_to_guess(self, as_free):
        result = asyncio.run(ai_chat._run_tool(None, "find_trades_for_budget", {"budget_max": 1}))
        assert "do not" in result["explanation"].lower()

    def test_an_anonymous_caller_is_refused(self, monkeypatch):
        async def anon(_request):
            return _viewer("free", user_id=None)
        monkeypatch.setattr(ai_chat, "get_viewer", anon)
        result = asyncio.run(ai_chat._run_tool(None, "get_track_record", {}))
        assert result["error"] == "not_available_on_this_plan"

    def test_a_refusal_carries_no_market_data_at_all(self, as_free):
        result = asyncio.run(ai_chat._run_tool(None, "resolve_and_evaluate_card", {"query": "Mbappe"}))
        assert set(result.keys()) == {"error", "explanation"}


# ---------------------------------------------------------------------------
# Fair use
# ---------------------------------------------------------------------------
#
# The per-minute limit stops a burst; it does not stop sustained use. At 6
# a minute an account could run over eight thousand model calls a day, and
# every one costs real money and reads the full paid dataset. The plan's
# promise of "the trading assistant" needs a documented daily ceiling too.

import pytest as _pytest  # noqa: E402


class TestDailyFairUseLimit:
    @_pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        import app.routers.v2.ai_chat as chat

        chat._in_process_window.clear()
        chat._in_process_day.clear()
        # No Redis in tests: the in-process fallback is what runs, which is
        # the path a Redis outage puts production on too.
        monkeypatch.setattr(chat, "_redis_checked", True)
        monkeypatch.setattr(chat, "_redis", None)
        yield
        chat._in_process_window.clear()
        chat._in_process_day.clear()

    def test_a_burst_is_throttled_by_the_minute_limit(self):
        import app.routers.v2.ai_chat as chat
        from fastapi import HTTPException

        for _ in range(chat._RATE_LIMIT_PER_MINUTE):
            asyncio.run(chat._check_rate_limit("u1"))
        with _pytest.raises(HTTPException) as exc:
            asyncio.run(chat._check_rate_limit("u1"))
        assert exc.value.status_code == 429
        assert "wait a moment" in str(exc.value.detail)

    def test_sustained_use_hits_the_daily_ceiling(self, monkeypatch):
        import app.routers.v2.ai_chat as chat
        from fastapi import HTTPException

        # Walk the clock forward a minute at a time so the per-minute
        # window never blocks - the daily ceiling is what must stop this.
        clock = {"t": 1_000_000.0}
        monkeypatch.setattr(chat.time, "time", lambda: clock["t"])

        for _ in range(chat._RATE_LIMIT_PER_DAY):
            asyncio.run(chat._check_rate_limit("u1"))
            clock["t"] += 61

        with _pytest.raises(HTTPException) as exc:
            asyncio.run(chat._check_rate_limit("u1"))
        assert exc.value.status_code == 429
        assert "fair-use limit" in str(exc.value.detail)
        # And says what is and is not affected, so it does not read as the
        # whole plan having stopped working.
        assert "everything else on your plan is unaffected" in str(exc.value.detail)

    def test_the_daily_ceiling_is_per_account(self, monkeypatch):
        import app.routers.v2.ai_chat as chat
        from fastapi import HTTPException

        clock = {"t": 2_000_000.0}
        monkeypatch.setattr(chat.time, "time", lambda: clock["t"])

        for _ in range(chat._RATE_LIMIT_PER_DAY):
            asyncio.run(chat._check_rate_limit("heavy"))
            clock["t"] += 61
        with _pytest.raises(HTTPException):
            asyncio.run(chat._check_rate_limit("heavy"))

        # A second account is unaffected.
        asyncio.run(chat._check_rate_limit("someone_else"))

    def test_the_ceiling_resets_the_next_day(self, monkeypatch):
        import app.routers.v2.ai_chat as chat
        from fastapi import HTTPException

        clock = {"t": 3_000_000.0}
        monkeypatch.setattr(chat.time, "time", lambda: clock["t"])
        for _ in range(chat._RATE_LIMIT_PER_DAY):
            asyncio.run(chat._check_rate_limit("u1"))
            clock["t"] += 61
        with _pytest.raises(HTTPException):
            asyncio.run(chat._check_rate_limit("u1"))

        clock["t"] += 86_400
        asyncio.run(chat._check_rate_limit("u1"))

    def test_the_ceiling_is_generous_enough_not_to_be_a_daily_allowance(self):
        import app.routers.v2.ai_chat as chat

        # This is a backstop against automation, not a quota a person
        # using the assistant as intended should ever meet.
        assert chat._RATE_LIMIT_PER_DAY >= 100

    def test_the_limit_is_enforced_on_the_endpoint_not_in_the_page(self):
        import inspect

        import app.routers.v2.ai_chat as chat

        assert "_check_rate_limit" in inspect.getsource(chat.chat)
