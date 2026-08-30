# tests/test_ai_usage.py
#
# The assistant's commercial safety.
#
# Phase 2 shipped a 200/day ceiling chosen as "generous enough not to be a
# daily allowance" - a UX judgement, never a costed one. Sizing it against
# the actual prompt put 200/day at 124-307% of the £3 subscription for one
# heavy member: the product lost money on anyone using the feature as
# permitted. Worse, the dominant cost term - conversation history - was
# chosen by the CALLER, because ChatRequest.messages had no maximum and
# the client resends the whole transcript on every request.
#
# These tests pin the bounds, the accounting, and the fact that no message
# text ever reaches the usage record.
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.services import ai_usage


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ai_usage.reset_state_for_tests()
    # No Redis in tests: the per-worker fallback is what runs, which is
    # also the path a Redis outage puts production on.
    monkeypatch.setattr(ai_usage, "_redis_checked", True)
    monkeypatch.setattr(ai_usage, "_redis", None)
    yield
    ai_usage.reset_state_for_tests()


def run(coro):
    return asyncio.run(coro)


class TestInputIsBounded:
    def test_history_is_trimmed_to_the_configured_cap(self):
        messages = [{"role": "user", "content": f"m{i}"} for i in range(50)]
        kept = ai_usage.trim_history(messages, max_messages=10)
        assert len(kept) == 10
        # The NEWEST turns are the ones kept: that is where the question is.
        assert kept[-1]["content"] == "m49"

    def test_the_latest_message_always_survives(self):
        # A request can never be trimmed to nothing.
        kept = ai_usage.trim_history([{"role": "user", "content": "only"}], max_messages=0)
        assert len(kept) == 1
        assert kept[0]["content"] == "only"

    def test_one_enormous_message_cannot_replace_a_long_history(self):
        kept = ai_usage.trim_history(
            [{"role": "user", "content": "x" * 100_000}], max_chars=2000,
        )
        assert len(kept[0]["content"]) == 2000

    def test_a_short_conversation_is_untouched(self):
        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        assert ai_usage.trim_history(messages, max_messages=10) == messages

    def test_pydantic_message_objects_work_too(self):
        class M:
            def __init__(self, role, content):
                self.role, self.content = role, content

        kept = ai_usage.trim_history([M("user", "hello")], max_messages=10)
        assert kept == [{"role": "user", "content": "hello"}]

    def test_the_request_body_itself_has_a_hard_ceiling(self):
        from app.routers.v2.ai_chat import ChatRequest

        field = ChatRequest.model_fields["messages"]
        maxima = [m for m in field.metadata if getattr(m, "max_length", None)]
        assert maxima, "ChatRequest.messages has no max_length - history is caller-controlled"

    def test_the_output_side_is_bounded_too(self):
        import inspect

        from app.routers.v2 import ai_chat

        assert "max_tokens=ai_usage.MAX_OUTPUT_TOKENS" in inspect.getsource(ai_chat.chat)


class TestTheLimits:
    def test_a_burst_hits_the_minute_limit(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_MINUTE", 3)
        for _ in range(3):
            assert run(ai_usage.acquire("u1")).allowed
            run(ai_usage.release("u1"))
        decision = run(ai_usage.acquire("u1"))
        assert not decision.allowed
        assert decision.reason == "minute"

    def test_sustained_use_hits_the_daily_ceiling(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_MINUTE", 1000)
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_DAY", 5)
        for _ in range(5):
            assert run(ai_usage.acquire("u1")).allowed
            run(ai_usage.release("u1"))
        decision = run(ai_usage.acquire("u1"))
        assert not decision.allowed
        assert decision.reason == "day"

    def test_the_daily_refusal_says_when_it_resets(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_DAY", 1)
        run(ai_usage.acquire("u1"))
        run(ai_usage.release("u1"))
        decision = run(ai_usage.acquire("u1"))
        assert decision.resets_at is not None
        assert "UTC" in decision.message
        # And says the rest of the plan is fine, so it does not read as
        # the subscription having stopped working.
        assert "everything else on your plan is unaffected" in decision.message

    def test_a_monthly_ceiling_exists_for_the_member_who_maxes_out_daily(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_MINUTE", 10_000)
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_DAY", 10_000)
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_MONTH", 4)
        for _ in range(4):
            assert run(ai_usage.acquire("u1")).allowed
            run(ai_usage.release("u1"))
        decision = run(ai_usage.acquire("u1"))
        assert decision.reason == "month"
        assert "this month" in decision.message

    def test_limits_are_per_account(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_DAY", 1)
        run(ai_usage.acquire("heavy"))
        run(ai_usage.release("heavy"))
        assert not run(ai_usage.acquire("heavy")).allowed
        assert run(ai_usage.acquire("someone_else")).allowed

    def test_a_refused_request_cannot_lower_its_own_count(self, monkeypatch):
        # Otherwise a caller could sit just under the limit forever by
        # being refused and refunded in a loop.
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_DAY", 2)
        for _ in range(2):
            run(ai_usage.acquire("u1"))
            run(ai_usage.release("u1"))
        for _ in range(5):
            assert not run(ai_usage.acquire("u1")).allowed


class TestConcurrency:
    def test_a_second_simultaneous_message_is_refused(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "MAX_CONCURRENT", 1)
        assert run(ai_usage.acquire("u1")).allowed
        second = run(ai_usage.acquire("u1"))
        assert not second.allowed
        assert second.reason == "concurrent"

    def test_the_slot_comes_back_when_the_request_finishes(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "MAX_CONCURRENT", 1)
        run(ai_usage.acquire("u1"))
        run(ai_usage.release("u1"))
        assert run(ai_usage.acquire("u1")).allowed

    def test_concurrency_does_not_consume_the_daily_allowance(self, monkeypatch):
        # A refused-for-concurrency request never reached the provider, so
        # it must not cost the customer a message.
        monkeypatch.setattr(ai_usage, "MAX_CONCURRENT", 1)
        monkeypatch.setattr(ai_usage, "RATE_LIMIT_PER_DAY", 3)
        run(ai_usage.acquire("u1"))
        for _ in range(10):
            assert run(ai_usage.acquire("u1")).reason == "concurrent"
        run(ai_usage.release("u1"))
        # Two of the three remain.
        assert run(ai_usage.acquire("u1")).allowed
        run(ai_usage.release("u1"))
        assert run(ai_usage.acquire("u1")).allowed


class TestTheKillSwitch:
    def test_off_means_no_model_call_at_all(self, monkeypatch):
        monkeypatch.setenv("AI_CHAT_ENABLED", "0")
        decision = run(ai_usage.acquire("u1"))
        assert not decision.allowed
        assert decision.reason == "disabled"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
    def test_the_obvious_spellings_all_work(self, monkeypatch, value):
        monkeypatch.setenv("AI_CHAT_ENABLED", value)
        assert not ai_usage.chat_enabled()

    def test_it_is_read_per_request_not_captured_at_import(self, monkeypatch):
        # Turning the assistant off must not require a deploy.
        monkeypatch.setenv("AI_CHAT_ENABLED", "0")
        assert not ai_usage.chat_enabled()
        monkeypatch.setenv("AI_CHAT_ENABLED", "1")
        assert ai_usage.chat_enabled()

    def test_on_by_default(self, monkeypatch):
        monkeypatch.delenv("AI_CHAT_ENABLED", raising=False)
        assert ai_usage.chat_enabled()


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens, self.completion_tokens = prompt, completion


class _Response:
    def __init__(self, prompt, completion):
        self.usage = _Usage(prompt, completion)


class TestAccounting:
    def test_usage_comes_from_the_provider_not_an_estimate(self):
        tally = ai_usage.UsageTally(model="gpt-4o-mini")
        tally.add(_Response(1500, 200))
        assert tally.input_tokens == 1500
        assert tally.output_tokens == 200
        assert tally.total_tokens == 1700

    def test_every_call_in_the_tool_loop_counts(self):
        # One CUSTOMER message is up to three model calls. Reading only
        # the last response would report a third of the real cost.
        tally = ai_usage.UsageTally(model="gpt-4o-mini")
        tally.add(_Response(1000, 60))
        tally.add(_Response(1800, 220))
        assert tally.model_calls == 2
        assert tally.input_tokens == 2800
        assert tally.output_tokens == 280

    def test_a_provider_that_reports_nothing_yields_zero_not_a_guess(self):
        class NoUsage:
            usage = None

        tally = ai_usage.UsageTally(model="gpt-4o-mini")
        tally.add(NoUsage())
        # Zero reads as "we do not know". A character-count estimate would
        # read as a confident wrong number.
        assert tally.total_tokens == 0
        assert tally.model_calls == 1

    def test_cost_uses_the_configured_rates(self, monkeypatch):
        monkeypatch.setattr(ai_usage, "PRICE_PER_1M_INPUT", 1.0)
        monkeypatch.setattr(ai_usage, "PRICE_PER_1M_OUTPUT", 2.0)
        assert ai_usage.estimate_cost(1_000_000, 1_000_000) == 3.0
        assert ai_usage.estimate_cost(500_000, 0) == 0.5

    def test_pricing_is_configuration_not_a_constant(self, monkeypatch):
        # No pricing existed anywhere in the repo, so the defaults are
        # unverified list rates. They have to be overridable.
        monkeypatch.setenv("AI_CHAT_PRICE_PER_1M_INPUT", "0.99")
        import importlib

        reloaded = importlib.reload(ai_usage)
        try:
            assert reloaded.PRICE_PER_1M_INPUT == 0.99
        finally:
            monkeypatch.delenv("AI_CHAT_PRICE_PER_1M_INPUT", raising=False)
            importlib.reload(ai_usage)

    def test_a_bad_env_value_falls_back_instead_of_crashing_the_import(self, monkeypatch):
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_PER_DAY", "not-a-number")
        import importlib

        reloaded = importlib.reload(ai_usage)
        try:
            assert reloaded.RATE_LIMIT_PER_DAY == 30
        finally:
            monkeypatch.delenv("AI_CHAT_RATE_LIMIT_PER_DAY", raising=False)
            importlib.reload(ai_usage)


class TestTheUsageRecordIsPrivacySafe:
    def test_no_message_text_can_reach_the_row(self):
        recorded = {}

        class Conn:
            async def execute(self, sql, *args):
                recorded["sql"] = sql
                recorded["args"] = args

        class Acquire:
            async def __aenter__(self): return Conn()
            async def __aexit__(self, *a): return False

        class Pool:
            def acquire(self): return Acquire()

        tally = ai_usage.UsageTally(model="gpt-4o-mini")
        tally.add(_Response(1500, 200))
        assert run(ai_usage.record("u1", tally, "ok", pool=Pool())) is True

        # The row is numbers and a status. Nothing in the arguments could
        # carry a question, an answer, a card name or an identity beyond
        # the internal user id.
        blob = json.dumps([str(a) for a in recorded["args"]])
        for forbidden in ("Mbappe", "should i buy", "@", "discord"):
            assert forbidden.lower() not in blob.lower()
        assert "u1" in blob
        assert recorded["args"][3] == 1500      # input tokens
        assert recorded["args"][4] == 200       # output tokens

    def test_the_insert_names_only_the_columns_it_should(self):
        import inspect

        source = inspect.getsource(ai_usage.record)
        for forbidden in ("message", "content", "prompt_text", "reply", "email", "discord"):
            assert forbidden not in source.lower(), forbidden

    def test_recording_never_raises(self):
        class Pool:
            def acquire(self): raise RuntimeError("database is gone")

        tally = ai_usage.UsageTally(model="gpt-4o-mini")
        # Accounting must not fail a request the customer already paid for
        # in tokens.
        assert run(ai_usage.record("u1", tally, "ok", pool=Pool())) is False


class TestTheEndpointWiring:
    def test_a_failed_request_is_still_recorded_with_what_it_spent(self):
        import inspect

        from app.routers.v2 import ai_chat

        source = inspect.getsource(ai_chat.chat)
        assert "finally:" in source
        assert "ai_usage.record" in source
        assert "ai_usage.release" in source

    def test_a_refusal_is_recorded_as_blocked(self):
        import inspect

        from app.routers.v2 import ai_chat

        assert '"blocked"' in inspect.getsource(ai_chat.chat)

    def test_the_refusal_tells_the_customer_when_it_resets(self):
        import inspect

        from app.routers.v2 import ai_chat

        assert "resetsAt" in inspect.getsource(ai_chat.chat)

    def test_no_internal_cost_figure_is_returned_to_a_customer(self):
        import inspect

        from app.routers.v2 import ai_chat

        source = inspect.getsource(ai_chat.chat)
        for internal in ("estimate_cost", "estimated_cost", "PRICE_PER_1M"):
            assert internal not in source


class TestTheDefaultIsCosted:
    def test_the_daily_default_is_not_the_uncosted_200(self):
        # 200/day measured at 124-307% of the GBP 3 subscription. The
        # replacement is a number the cost model supports.
        assert ai_usage.RATE_LIMIT_PER_DAY < 200

    def test_the_default_leaves_room_under_one_subscription(self):
        # Worst measured case per message with history at the cap, in USD.
        worst_case_per_message = 0.00195
        monthly = worst_case_per_message * ai_usage.RATE_LIMIT_PER_DAY * 30
        gbp = monthly * 0.79
        assert gbp < 3.0 * 0.5, (
            f"worst-case AI cost is GBP {gbp:.2f}/month against a GBP 3 subscription"
        )

    def test_a_month_of_daily_maxima_is_capped_below_the_daily_times_thirty(self):
        assert ai_usage.RATE_LIMIT_PER_MONTH < ai_usage.RATE_LIMIT_PER_DAY * 30
