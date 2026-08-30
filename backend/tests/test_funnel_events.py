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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.v2.events import (
    ALLOWED_EVENTS,
    ALLOWED_PROPS,
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
        assert client_events == set(ALLOWED_EVENTS), (
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
