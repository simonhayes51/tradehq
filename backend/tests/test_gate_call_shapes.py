# tests/test_gate_call_shapes.py
#
# The gates are called two ways, and both are legitimate:
#
#     @router.get(..., dependencies=[Depends(require_feature("x"))])
#     await require_feature("x")(request)          # inline, mid-handler
#
# The inline form exists where a route is only conditionally gated, or
# where the gate has to run after some other work.
#
# WHAT THIS FILE EXISTS FOR
#
# When these gates were rebuilt on `Depends(get_viewer)`, the route
# decorator sites kept working - FastAPI resolves the dependency - and
# the TWELVE inline sites started passing a Request into the `viewer`
# slot, because outside FastAPI a `Depends(...)` default is just a
# default value. Every one of those endpoints raised AttributeError on
# `viewer.user_id` and returned "Internal server error" to every caller.
#
# It shipped because the tests exercised the helpers around those
# endpoints (_run_tool, chat_status) and never the gate itself in the
# shape the handlers actually call it.
from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
os.environ.setdefault("ENV", "test")

from app.auth.entitlements import (  # noqa: E402
    FEATURE_MIN_TIER,
    Viewer,
    features_for_tier,
    require_feature,
    require_tier,
)


def run(coro):
    return asyncio.run(coro)


def bare_request(path="/api/v2/whatever"):
    """A Request with no session - i.e. an anonymous caller."""
    return Request({"type": "http", "method": "GET", "path": path,
                    "headers": [], "query_string": b""})


def viewer(tier="elite", *, user_id="u1"):
    return Viewer(user_id=user_id, tier=tier, is_admin=False,
                  features=frozenset(features_for_tier(tier)))


class TestBothCallShapesWork:
    def test_a_request_passed_inline_is_resolved_not_crashed(self):
        # The exact shape every inline call site uses. Before the fix this
        # raised AttributeError: 'Request' object has no attribute
        # 'user_id', which the error middleware turned into a 500.
        with pytest.raises(HTTPException) as exc:
            run(require_feature("ai_chat")(bare_request()))
        assert exc.value.status_code == 401

    def test_a_resolved_viewer_still_works(self):
        # The FastAPI path must be unchanged.
        ent = run(require_feature("ai_chat")(viewer("elite")))
        assert ent["tier"] == "elite"

    def test_an_entitled_request_passes_inline_too(self, monkeypatch):
        import app.auth.entitlements as entitlements

        async def fake_get_viewer(_req):
            return viewer("elite")

        monkeypatch.setattr(entitlements, "get_viewer", fake_get_viewer)
        assert run(require_feature("ai_chat")(bare_request()))["tier"] == "elite"

    def test_an_unentitled_request_gets_402_not_500(self, monkeypatch):
        import app.auth.entitlements as entitlements

        async def fake_get_viewer(_req):
            return viewer("free")

        monkeypatch.setattr(entitlements, "get_viewer", fake_get_viewer)
        with pytest.raises(HTTPException) as exc:
            run(require_feature("ai_chat")(bare_request()))
        assert exc.value.status_code == 402
        assert exc.value.detail["feature"] == "ai_chat"

    def test_require_tier_accepts_both_shapes_too(self):
        with pytest.raises(HTTPException) as exc:
            run(require_tier("elite")(bare_request()))
        assert exc.value.status_code == 401
        assert run(require_tier("elite")(viewer("elite")))["tier"] == "elite"


# Every module that calls a gate inline. Found by grepping for the call
# shape; the test below re-derives the list so a NEW inline call site in
# one of these modules is covered automatically, and a new module is
# caught by test_the_module_list_is_complete.
INLINE_GATE_MODULES = [
    "app.routers.trending",
    "app.routers.v2.players",
    "app.routers.v2.recommendations",
    "app.routers.v2.ai_chat",
    "app.routers.v2.analytics",
    "app.routers.v2.sbc",
]

_INLINE_CALL = re.compile(r'require_(?:feature|tier)\(\s*["\'](?P<name>[a-z_]+)["\']\s*\)\s*\(')


class TestEveryInlineCallSiteIsExercised:
    @pytest.mark.parametrize("module_name", INLINE_GATE_MODULES)
    def test_each_inline_gate_in_this_module_refuses_cleanly(self, module_name):
        module = importlib.import_module(module_name)
        source = inspect.getsource(module)
        names = sorted({m.group("name") for m in _INLINE_CALL.finditer(source)})
        assert names, f"{module_name} no longer calls a gate inline - update the list"

        for name in names:
            gate = require_feature(name) if name in FEATURE_MIN_TIER else require_tier(name)
            # An anonymous caller must get a clean 401 from every one of
            # them. Anything else - especially AttributeError - is the
            # regression this file was written for.
            with pytest.raises(HTTPException) as exc:
                run(gate(bare_request()))
            assert exc.value.status_code == 401, f"{module_name}:{name}"

    def test_the_module_list_is_complete(self):
        # A new module that starts calling a gate inline has to be added
        # above, or this fails.
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        found = set()
        for path in list((root / "app").rglob("*.py")) + [root / "main.py"]:
            if path.name == "entitlements.py":
                continue
            if _INLINE_CALL.search(path.read_text()):
                rel = path.relative_to(root).with_suffix("")
                found.add(str(rel).replace("/", "."))
        missing = found - set(INLINE_GATE_MODULES)
        assert not missing, (
            f"these modules call a gate inline and are not covered: {sorted(missing)}"
        )


class TestTheAiChatEndpointSpecifically:
    def test_it_gates_before_doing_any_work(self):
        # The gate is the first thing the handler does, so an unentitled
        # caller never reaches a rate-limit reservation or a model call.
        #
        # Named generically rather than against one spelling: the rate
        # limiter has already moved once (from an in-module _check_rate_limit
        # to ai_usage.acquire), and this property is about ORDER, not about
        # which module owns the limiter today.
        source = inspect.getsource(importlib.import_module("app.routers.v2.ai_chat").chat)
        limiter = next(
            (marker for marker in ("ai_usage.acquire", "_check_rate_limit") if marker in source),
            None,
        )
        assert limiter, "the chat handler no longer rate-limits at all"
        assert source.index("require_feature") < source.index(limiter)
        assert source.index("require_feature") < source.index("OPENAI_API_KEY")

    def test_an_anonymous_caller_gets_401_from_the_real_handler(self, monkeypatch):
        import app.auth.entitlements as entitlements
        from app.routers.v2 import ai_chat

        # An anonymous viewer, built without assuming which keyword
        # arguments features_for_tier takes - that signature is newer than
        # the bug this file exists for, and the test should hold on either
        # side of it.
        async def fake_get_viewer(_req):
            return Viewer(user_id=None, tier="free", is_admin=False, features=frozenset())

        monkeypatch.setattr(entitlements, "get_viewer", fake_get_viewer)

        class Body:
            messages = [type("M", (), {"role": "user", "content": "hi"})()]

        with pytest.raises(HTTPException) as exc:
            run(ai_chat.chat(Body(), bare_request("/api/v2/ai/chat")))
        # 401 or 402 - a refusal, never a 500.
        assert exc.value.status_code in (401, 402)
