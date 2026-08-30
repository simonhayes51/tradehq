# tests/test_companion_auth.py
#
# How the browser extension and the userscript authenticate, and what
# each access state actually gets back.
#
# THE PROBLEM THIS FILE EXISTS FOR
#
# Phase 2 gated the companion endpoint and switched both clients to send
# credentials. That closed the bypass, but it made the overlay depend on a
# CROSS-SITE cookie: both clients run inside the EA Web App page on
# www.ea.com and call api.eafc.live.
#
#   * The Chrome extension fetches from its MV3 service worker with
#     host_permissions for api.eafc.live. That is the stronger of the two
#     positions, but it rests on browser behaviour that has changed
#     repeatedly and is changing again.
#   * The userscript uses GM_xmlhttpRequest with withCredentials, and
#     support for that flag differs BETWEEN MANAGERS. This codebase has
#     already been bitten by exactly that class of difference once.
#
# Neither can be verified from a test suite - they are properties of
# browsers and userscript managers. What CAN be verified, and is verified
# here, is everything on our side of the line: the cookie attributes that
# make third-party delivery possible at all, the CORS posture, and a
# bearer path that does not depend on any of it.
from __future__ import annotations

import inspect
import json
import os
import pathlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_PRIVATE_KEY", "test-signing-key")

from app.auth import companion_token  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]


class TestTheShortLivedToken:
    def test_a_token_round_trips_to_the_account_that_owns_it(self):
        issued = companion_token.issue("u1")
        assert companion_token.verify(issued["token"]) == "u1"

    def test_it_is_short_lived_by_default(self):
        # Not a permanent API key sitting in userscript storage.
        assert companion_token.TTL_HOURS <= 24 * 7
        issued = companion_token.issue("u1")
        assert issued["expiresAt"] - int(time.time()) <= companion_token.TTL_HOURS * 3600 + 5

    def test_an_expired_token_is_refused(self):
        issued = companion_token.issue("u1", ttl_hours=-1)
        assert companion_token.verify(issued["token"]) is None

    def test_a_forged_token_is_refused(self):
        assert companion_token.verify("not.a.token") is None
        assert companion_token.verify("") is None

    def test_a_token_signed_with_another_key_is_refused(self, monkeypatch):
        issued = companion_token.issue("u1")
        monkeypatch.setenv("JWT_PRIVATE_KEY", "a-rotated-key")
        # Rotating the signing key revokes every outstanding token, which
        # is the only revocation a stateless token has.
        assert companion_token.verify(issued["token"]) is None

    def test_a_trade_ingest_token_does_not_work_here(self):
        # The signing key is shared, so a valid signature says nothing
        # about what the token was issued FOR. Scope is checked explicitly.
        import jwt

        payload = {
            "sub": "u1", "scope": "trade:ingest", "iat": int(time.time()),
            "exp": int(time.time()) + 3600, "iss": companion_token.ISSUER,
        }
        forged = jwt.encode(payload, os.environ["JWT_PRIVATE_KEY"], algorithm="HS256")
        assert companion_token.verify(forged) is None

    def test_the_scope_is_read_only(self):
        assert companion_token.SCOPE == "companion:read"
        assert "write" not in companion_token.SCOPE

    def test_every_failure_looks_the_same(self):
        # Expired, forged and wrong-key all return None. Telling a caller
        # which one it was helps only an attacker.
        source = inspect.getsource(companion_token.verify)
        assert "return None" in source
        assert "raise" not in source.replace("raising", "")

    @pytest.mark.parametrize("header,expected", [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("Bearer  abc ", "abc"),
        ("Basic abc", None),
        ("", None),
        ("Bearer", None),
    ])
    def test_the_header_is_parsed_conservatively(self, header, expected):
        class Req:
            headers = {"authorization": header}

        assert companion_token.bearer_from(Req()) == expected


class TestTokenIssuing:
    def _client(self, viewer):
        from app.auth.access import get_viewer
        from app.routers.v2.companion import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v2")
        app.dependency_overrides[get_viewer] = lambda: viewer
        return TestClient(app, raise_server_exceptions=False)

    def test_a_signed_in_member_gets_a_token(self):
        from app.auth.access import Viewer
        from app.auth.entitlements import features_for_tier

        viewer = Viewer(user_id="u1", tier="elite", is_admin=False,
                        features=frozenset(features_for_tier("elite")))
        body = self._client(viewer).post("/api/v2/companion/session").json()
        assert companion_token.verify(body["token"]) == "u1"
        assert body["scope"] == "companion:read"
        assert body["expiresAtIso"]

    def test_a_signed_out_caller_gets_nothing(self):
        from app.auth.access import Viewer
        from app.auth.entitlements import features_for_tier

        viewer = Viewer(user_id=None, tier="free", is_admin=False,
                        features=frozenset(features_for_tier("free", authenticated=False)))
        resp = self._client(viewer).post("/api/v2/companion/session")
        assert resp.status_code == 401
        assert "free" in resp.json()["detail"]["message"].lower()

    def test_it_is_a_post_so_a_link_cannot_mint_a_credential(self):
        from app.routers.v2.companion import router

        for route in router.routes:
            if route.path.endswith("/session"):
                assert set(route.methods) == {"POST"}
                break
        else:
            pytest.fail("no /session route")


class TestTheTokenSaysWhoNotWhat:
    def test_entitlements_are_resolved_fresh_not_carried_in_the_token(self):
        # A token minted while someone was Elite must give them nothing
        # once the subscription lapses.
        source = inspect.getsource(__import__("app.auth.access", fromlist=["x"]).get_companion_viewer)
        assert "resolve_user_tier" in source
        # The token payload carries no tier, plan or feature list.
        issued = companion_token.issue("u1")
        import jwt

        payload = jwt.decode(issued["token"], os.environ["JWT_PRIVATE_KEY"],
                             algorithms=["HS256"], issuer=companion_token.ISSUER)
        for forbidden in ("tier", "plan", "features", "is_admin", "premium"):
            assert forbidden not in payload

    def test_a_live_cookie_session_wins_over_a_token(self):
        # Someone signed in on this browser is who they are; a stale token
        # in an extension must not quietly override a live session.
        source = inspect.getsource(__import__("app.auth.access", fromlist=["x"]).get_companion_viewer)
        assert source.index("get_viewer(req)") < source.index("verify(")

    def test_only_the_companion_uses_the_bearer_path(self):
        # A bearer path is one more way to be authenticated. It belongs
        # where it is needed, not everywhere.
        import app.routers.v2.futgg_market as market
        import app.routers.players as legacy

        for module in (market, legacy):
            assert "get_companion_viewer" not in inspect.getsource(module)


class TestCookiePostureIsUnchanged:
    def _main_source(self):
        return (REPO / "backend" / "main.py").read_text()

    def test_the_session_cookie_is_cross_site_capable_in_production(self):
        source = self._main_source()
        # SameSite=None is what allows the cookie to be sent from the EA
        # Web App's page context at all. Secure is mandatory with it.
        assert '"same_site": "none" if IS_PROD else "lax"' in source
        assert '"https_only": IS_PROD' in source

    def test_cors_still_uses_an_allow_list_not_a_wildcard(self):
        source = self._main_source()
        assert "allow_origins=ALLOWED_ORIGINS" in source
        assert 'allow_origins=["*"]' not in source

    def test_the_extension_origin_was_not_added_to_the_cors_allow_list(self):
        # It does not need to be: an MV3 service-worker fetch with
        # host_permissions is exempt from CORS. Adding chrome-extension://
        # origins would widen the allow-list for every caller to fix
        # something that is not broken.
        source = self._main_source()
        assert "chrome-extension://" not in source

    def test_credentials_are_still_required_for_cors(self):
        assert "allow_credentials=True" in self._main_source()


class TestClientConfiguration:
    def test_the_extension_has_host_permission_for_the_api(self):
        manifest = json.loads((REPO / "eafc-live-extension" / "manifest.json").read_text())
        assert "https://api.eafc.live/*" in manifest["host_permissions"]

    def test_the_extension_fetches_from_the_service_worker_not_the_page(self):
        # A content script's fetch carries the PAGE's origin for CORS.
        # The service worker's does not, and host_permissions exempt it.
        background = (REPO / "eafc-live-extension" / "src" / "background.js").read_text()
        assert 'credentials: "include"' in background
        manifest = json.loads((REPO / "eafc-live-extension" / "manifest.json").read_text())
        assert manifest["background"]["service_worker"].endswith("background.js")

    def test_the_userscript_declares_connect_for_the_api(self):
        # Managers refuse GM_xmlhttpRequest to any host not declared.
        metadata = (REPO / "eafc-live-userscript" / "src" / "metadata.js").read_text()
        assert "@connect      api.eafc.live" in metadata

    def test_the_userscript_asks_for_credentials(self):
        client = (REPO / "eafc-live-userscript" / "src" / "api-client.js").read_text()
        assert "withCredentials: true" in client

    def test_neither_client_sends_an_ea_credential(self):
        # The overlay reads the EA Web App page; it must never forward
        # anything from that session to our API.
        for path in (
            REPO / "eafc-live-extension" / "src" / "background.js",
            REPO / "eafc-live-userscript" / "src" / "api-client.js",
        ):
            source = path.read_text()
            for forbidden in ("X-UT-SID", "x-ut-sid", "phishing", "nucleus"):
                assert forbidden not in source, f"{path.name} references {forbidden}"

    def test_both_clients_run_on_the_ea_web_app_not_futgg(self):
        # Worth pinning because it is easy to assume otherwise: the
        # overlay's page context is www.ea.com.
        manifest = json.loads((REPO / "eafc-live-extension" / "manifest.json").read_text())
        matches = manifest["content_scripts"][0]["matches"]
        assert all("ea.com" in m for m in matches)
        metadata = (REPO / "eafc-live-userscript" / "src" / "metadata.js").read_text()
        assert "@match        https://www.ea.com/" in metadata
        assert "fut.gg" not in metadata.lower()
