# tests/test_funnel_events.py
#
# The internal funnel-event endpoint. No third-party analytics provider is
# involved and none is added; this is the small internal interface, and
# what it must never do is store the things a trading product knows about
# a person: what they searched for, what they typed at the assistant, who
# they are on Discord.
#
# The client (frontend/src/lib/analytics.js) applies the same allow-list,
# but a client-side filter is a convenience, not a control - anything can
# POST to this route. These tests are about what the SERVER accepts.
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Two tests below read main.py's webhook source, and main validates its
# environment at import time. Placeholders satisfy that check without
# reaching anything - nothing here opens a connection or signs a token.
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
os.environ.setdefault("ENV", "test")

from app.routers.v2.events import (
    ALLOWED_EVENTS,
    ALLOWED_PROPS,
    SERVER_ONLY_EVENTS,
    router as events_router,
    sanitise,
)

recorded: list = []


class FakeConn:
    async def execute(self, _sql, event, user_id, props):
        recorded.append({"event": event, "user_id": user_id, "props": json.loads(props)})


class FakeAcquire:
    async def __aenter__(self):
        return FakeConn()

    async def __aexit__(self, *_):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


@pytest.fixture
def client(monkeypatch):
    import app.routers.v2.events as events

    async def fake_pool():
        return FakePool()

    monkeypatch.setattr(events, "get_core_pool", fake_pool)
    recorded.clear()
    app = FastAPI()
    app.include_router(events_router, prefix="/api/v2")
    return TestClient(app)


class TestWhatIsStored:
    def test_a_known_event_is_recorded(self, client):
        resp = client.post("/api/v2/events/track", json={"event": "signup_viewed", "props": {"surface": "gated"}})
        assert resp.status_code == 200
        assert resp.json()["recorded"] is True
        assert recorded == [{"event": "signup_viewed", "user_id": None, "props": {"surface": "gated"}}]

    def test_a_signed_out_step_records_no_identity_at_all(self, client):
        client.post("/api/v2/events/track", json={"event": "upgrade_prompt_viewed", "props": {"feature": "trade_finder"}})
        assert recorded[0]["user_id"] is None

    def test_an_unknown_event_is_dropped_not_stored(self, client):
        # An open-ended event table is how a small internal interface
        # becomes an uncontrolled one.
        resp = client.post("/api/v2/events/track", json={"event": "player_searched", "props": {}})
        assert resp.json() == {"recorded": False, "reason": "unknown_event"}
        assert recorded == []

    def test_instrumentation_never_fails_the_action_that_triggered_it(self, client, monkeypatch):
        import app.routers.v2.events as events

        async def broken():
            raise RuntimeError("pool is down")

        monkeypatch.setattr(events, "get_core_pool", broken)
        resp = client.post("/api/v2/events/track", json={"event": "signup_viewed", "props": {}})
        assert resp.status_code == 200
        assert resp.json()["recorded"] is False

    def test_a_missing_table_is_not_an_error_either(self, client, monkeypatch):
        # Before migration 075 is applied, every insert raises.
        import app.routers.v2.events as events

        class Failing(FakeConn):
            async def execute(self, *_a):
                raise RuntimeError('relation "funnel_events" does not exist')

        class FailingAcquire(FakeAcquire):
            async def __aenter__(self):
                return Failing()

        class FailingPool:
            def acquire(self):
                return FailingAcquire()

        async def pool():
            return FailingPool()

        monkeypatch.setattr(events, "get_core_pool", pool)
        assert client.post("/api/v2/events/track", json={"event": "signup_viewed"}).status_code == 200


class TestTheServerReappliesThePrivacyRule:
    @pytest.mark.parametrize("payload", [
        {"query": "mbappe 91"},
        {"search": "haaland"},
        {"email": "someone@example.com"},
        {"discord_id": "123456789012345678"},
        {"message": "should I buy mbappe"},
        {"user_id": "u1"},
        {"ip": "203.0.113.4"},
        {"user_agent": "Mozilla/5.0"},
    ])
    def test_a_client_that_sends_more_than_it_should_has_it_dropped(self, client, payload):
        client.post("/api/v2/events/track", json={"event": "free_limit_reached", "props": payload})
        assert recorded[0]["props"] == {}

    def test_allowed_keys_survive(self, client):
        props = {"tier": "free", "surface": "opportunities", "feature": "trade_finder",
                 "plan": "monthly", "step": 1, "count": 2, "reason": "limit"}
        client.post("/api/v2/events/track", json={"event": "free_limit_reached", "props": props})
        assert recorded[0]["props"] == props

    def test_a_long_string_on_an_allowed_key_cannot_smuggle_content(self):
        smuggled = "who should i buy for 40k " * 20
        assert len(sanitise({"reason": smuggled})["reason"]) == 40

    def test_an_absurd_number_is_dropped(self):
        assert sanitise({"count": 10 ** 15}) == {}
        assert sanitise({"count": 5}) == {"count": 5}

    def test_nested_structures_are_never_stored(self):
        # A dict or list under an allowed key is the obvious way to get
        # free text through a key-name filter.
        assert sanitise({"reason": {"text": "mbappe"}}) == {}
        assert sanitise({"surface": ["a", "b"]}) == {}


class TestTheTwoAllowListsStayInStep:
    def test_the_client_and_server_event_vocabularies_match(self):
        import pathlib
        import re

        client_file = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "analytics.js"
        if not client_file.exists():
            pytest.skip("frontend not present in this checkout")
        source = client_file.read_text()
        block = source[source.index("export const EVENTS = {"):source.index("};", source.index("export const EVENTS = {"))]
        client_events = set(re.findall(r':\s*"([a-z_]+)"', block))
        # The client knows every event EXCEPT the server-only ones. A
        # browser cannot be trusted to assert "we were paid".
        assert client_events == set(ALLOWED_EVENTS) - set(SERVER_ONLY_EVENTS), (
            "an event the client fires that the server does not know is silently discarded"
        )

    def test_the_client_and_server_property_allow_lists_match(self):
        import pathlib
        import re

        client_file = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "analytics.js"
        if not client_file.exists():
            pytest.skip("frontend not present in this checkout")
        source = client_file.read_text()
        block = source[source.index("const ALLOWED_PROPS = new Set(["):source.index("]);", source.index("const ALLOWED_PROPS = new Set(["))]
        # Line-anchored: each entry's own quoted key, not the example
        # values in the trailing comment that documents it.
        client_props = set(re.findall(r'^\s+"([a-z_]+)",', block, re.M))
        assert client_props == set(ALLOWED_PROPS)


# ---------------------------------------------------------------------------
# Who is allowed to say we were paid
# ---------------------------------------------------------------------------
#
# checkout_completed is emitted by the BROWSER, on the page Stripe
# redirects back to. It means "a customer returned from checkout", which
# is not "Stripe confirmed a paid subscription" - and the two differ in
# both directions. Someone who pays and closes the tab never fires it;
# someone who reaches the URL without paying can.

class TestRevenueIsServerConfirmed:
    def test_a_browser_cannot_claim_a_confirmed_subscription(self, client):
        resp = client.post("/api/v2/events/track",
                           json={"event": "subscription_confirmed", "props": {"plan": "monthly"}})
        assert resp.json() == {"recorded": False, "reason": "server_only_event"}
        # Dropped entirely, not stored with source='client': a row that
        # exists would have to be filtered out of every revenue query
        # forever.
        assert recorded == []

    def test_the_browser_event_is_still_accepted_as_intent(self, client):
        # It is a real signal - it just is not revenue.
        assert client.post("/api/v2/events/track",
                           json={"event": "checkout_completed", "props": {"plan": "monthly"}}
                           ).json()["recorded"] is True

    def test_client_rows_are_marked_as_client(self, client):
        client.post("/api/v2/events/track", json={"event": "signup_viewed"})
        import inspect

        import app.routers.v2.events as events

        assert "'client'" in inspect.getsource(events.track_event)

    def test_the_server_recorder_marks_its_rows_as_server(self):
        import inspect

        import app.routers.v2.events as events

        assert "'server'" in inspect.getsource(events.record_server_event)

    def test_the_server_recorder_applies_the_same_property_allow_list(self):
        # "The server wrote it" is not a reason to start storing free text.
        import inspect

        import app.routers.v2.events as events

        assert "sanitise(" in inspect.getsource(events.record_server_event)

    def test_the_server_recorder_never_raises(self):
        import asyncio

        import app.routers.v2.events as events

        class Pool:
            def acquire(self): raise RuntimeError("db gone")

        # A Stripe webhook must not fail because a reporting row could not
        # be written.
        assert asyncio.run(events.record_server_event(
            "subscription_confirmed", "u1", {"plan": "monthly"}, pool=Pool())) is False

    def test_the_server_recorder_refuses_unknown_events(self):
        import asyncio

        import app.routers.v2.events as events

        assert asyncio.run(events.record_server_event("made_up_event", "u1")) is False

    def test_the_webhook_emits_it_only_on_becoming_entitled(self):
        # Stripe sends subscription.updated for renewals, card changes and
        # cancellation flags. Counting each as a conversion would inflate
        # revenue by whatever Stripe happens to be chatty about.
        import inspect

        import main

        source = inspect.getsource(main.handle_subscription_updated)
        assert "is_active and not was_active" in source
        assert "record_server_event" in source

    def test_the_creation_webhook_emits_it_when_entitled(self):
        import inspect

        import main

        source = inspect.getsource(main.handle_subscription_created)
        assert "subscription_confirmed" in source
        assert "if is_entitled:" in source


class TestTrackingFailureIsVisible:
    def test_a_healthy_run_reports_ok(self, client):
        import app.routers.v2.events as events

        events.reset_tracking_health()
        client.post("/api/v2/events/track", json={"event": "signup_viewed"})
        health = events.tracking_health()
        assert health["status"] == "ok"
        assert health["recorded"] == 1

    def test_total_loss_is_reported_as_failing_not_as_quiet(self, client, monkeypatch):
        import app.routers.v2.events as events

        events.reset_tracking_health()

        async def broken():
            raise RuntimeError("pool is down")

        monkeypatch.setattr(events, "get_core_pool", broken)
        for _ in range(3):
            client.post("/api/v2/events/track", json={"event": "signup_viewed"})

        health = events.tracking_health()
        # This is the whole point: the endpoint answered 200 three times.
        # Without the counter, "we have recorded nothing all week" looks
        # exactly like "nobody used the product".
        assert health["status"] == "failing"
        assert health["failed"] == 3
        assert health["failure_rate"] == 1.0
        events.reset_tracking_health()

    def test_nothing_yet_is_not_reported_as_healthy(self):
        import app.routers.v2.events as events

        events.reset_tracking_health()
        assert events.tracking_health()["status"] == "unknown"

    def test_the_error_kind_is_recorded_but_never_the_message(self, client, monkeypatch):
        import app.routers.v2.events as events

        events.reset_tracking_health()

        async def broken():
            raise RuntimeError("postgres://user:hunter2@db.internal:5432/core is unreachable")

        monkeypatch.setattr(events, "get_core_pool", broken)
        client.post("/api/v2/events/track", json={"event": "signup_viewed"})

        health = events.tracking_health()
        assert health["last_error_kind"] == "RuntimeError"
        # An asyncpg error text can carry a connection string, and this is
        # read by an admin page.
        blob = json.dumps(health)
        assert "hunter2" not in blob
        assert "db.internal" not in blob
        events.reset_tracking_health()

    def test_a_customer_action_still_succeeds_while_analytics_is_down(self, client, monkeypatch):
        import app.routers.v2.events as events

        async def broken():
            raise RuntimeError("pool is down")

        monkeypatch.setattr(events, "get_core_pool", broken)
        resp = client.post("/api/v2/events/track", json={"event": "checkout_started"})
        # 200, always. Instrumentation must never fail the action that
        # triggered it.
        assert resp.status_code == 200
        assert resp.json()["recorded"] is False
        events.reset_tracking_health()
