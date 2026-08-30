# tests/test_access_control.py
#
# The gates, tested from the outside and adversarially.
#
# Every other test file in this repo runs as Elite so it can assert on
# engine behaviour. This file is the opposite: it assumes the caller is
# hostile and checks that what comes back over the wire is what the
# caller is entitled to - not what the frontend would have chosen to
# render. The distinction matters because the failure mode being guarded
# against is a full payload plus a client that hides it.
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.access import Viewer, assert_no_premium_fields, get_viewer, preview_opportunity
from app.auth.entitlements import features_for_tier
from app.routers.v2.futgg_market import get_provider, router as futgg_router
from tests.test_futgg_market_router import FakeProvider, _row


def buy_row(card_id: int = 2, **overrides) -> Dict[str, Any]:
    """A row the engine actually scores as a BUY - the same shape the
    engine tests use. An access test asserting on an empty feed proves
    nothing."""
    base = dict(source_card_id=card_id, current_bin=40000, sales_median=55000,
                sales_trimmed_mean=55000, sales_count=40)
    base.update(overrides)
    return _row(**base)

# Numbers a caller could act on. If any of these reaches an unentitled
# client, the paid product has been given away.
EXECUTABLE_FIELDS = (
    "recommended_buy_max", "buy_below", "recommended_sell_target",
    "expected_profit_after_tax", "expected_roi", "confidence_score",
    "signal_reasons", "liquidity_score", "fair_value", "current_bin",
)


def viewer(tier: str, *, user_id: Optional[str] = "u1") -> Viewer:
    return Viewer(
        user_id=user_id, tier=tier, is_admin=False,
        features=frozenset(features_for_tier(tier)),
    )


ANON = viewer("free", user_id=None)
FREE = viewer("free")
ELITE = viewer("elite")


def client(as_viewer: Viewer, rows: Optional[List[Dict[str, Any]]] = None) -> TestClient:
    """Only the data provider and the viewer are faked. Every real gate in
    the router is left in place - that is the point."""
    app = FastAPI()
    app.include_router(futgg_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: FakeProvider(rows or [buy_row()])
    app.dependency_overrides[get_viewer] = lambda: as_viewer
    return TestClient(app)


class TestSignedOutCannotReachThePaidProduct:
    def test_opportunities_returns_no_executable_numbers(self):
        body = client(ANON).get("/api/v2/opportunities").json()
        assert body["items"], "a preview should still show that real cards exist"
        for item in body["items"]:
            assert item["locked"] is True
            for field in EXECUTABLE_FIELDS:
                assert field not in item, f"{field} leaked to an anonymous caller"

    def test_preview_still_carries_enough_to_be_worth_looking_at(self):
        item = client(ANON).get("/api/v2/opportunities").json()["items"][0]
        assert item["name"]
        assert item["signal"]                 # the verdict is the hook
        assert item["current_bin_approx"] is not None  # rough size, not a price

    def test_approximate_price_is_actually_rounded(self):
        item = client(ANON).get("/api/v2/opportunities").json()["items"][0]
        assert item["current_bin_approx"] % 1000 == 0

    def test_trade_finder_is_refused_outright(self):
        assert client(ANON).get("/api/v2/trade-finder").status_code in (401, 402)

    def test_strategy_finder_is_refused_outright(self):
        resp = client(ANON).get("/api/v2/trade-finder/strategy/best_overall")
        assert resp.status_code in (401, 402)

    def test_a_refusal_is_not_an_empty_list(self):
        # An empty 200 would read as "the market is quiet today", which is
        # a lie, and it trains users to distrust the feed.
        resp = client(ANON).get("/api/v2/trade-finder")
        assert resp.status_code != 200


class TestFreeGetsTheDailyPicksAndNothingMore:
    def test_locked_items_carry_no_executable_numbers(self, monkeypatch):
        import app.routers.v2.futgg_market as market

        async def no_picks(*_a, **_k):
            return []
        monkeypatch.setattr(market, "resolve_pick_ids", no_picks)

        body = client(FREE).get("/api/v2/opportunities").json()
        for item in body["items"]:
            assert item["locked"] is True
            assert_no_premium_fields(item)

    def test_the_days_picks_come_back_in_full(self, monkeypatch):
        import app.routers.v2.futgg_market as market

        async def one_pick(*_a, **_k):
            return [buy_row()["source_card_id"]]
        monkeypatch.setattr(market, "resolve_pick_ids", one_pick)

        body = client(FREE).get("/api/v2/opportunities").json()
        unlocked = [i for i in body["items"] if not i.get("locked")]
        assert unlocked, "a Free member must actually receive their daily picks"
        assert unlocked[0]["recommended_sell_target"] is not None

    def test_the_response_says_how_many_are_locked(self, monkeypatch):
        import app.routers.v2.futgg_market as market

        async def no_picks(*_a, **_k):
            return []
        monkeypatch.setattr(market, "resolve_pick_ids", no_picks)

        body = client(FREE).get("/api/v2/opportunities").json()
        assert body["locked_count"] == len(body["items"])
        assert body["free_picks"]["nextRefreshAt"]

    def test_trade_finder_is_refused(self):
        assert client(FREE).get("/api/v2/trade-finder").status_code == 402

    def test_paging_cannot_be_used_to_widen_the_free_allowance(self, monkeypatch):
        # The bypass being closed: request page 2 and hope the picks are
        # resolved per-page rather than over the whole ranked pool.
        import app.routers.v2.futgg_market as market

        async def no_picks(*_a, **_k):
            return []
        monkeypatch.setattr(market, "resolve_pick_ids", no_picks)

        rows = [buy_row(card_id=i) for i in range(1, 30)]
        for page in (1, 2, 3):
            body = client(FREE, rows).get(f"/api/v2/opportunities?page={page}&page_size=5").json()
            for item in body["items"]:
                assert item["locked"] is True

    def test_page_size_cannot_be_inflated_past_the_cap(self):
        resp = client(FREE).get("/api/v2/opportunities?page_size=100000")
        assert resp.status_code == 422


class TestEliteGetsEverything:
    def test_opportunities_are_unredacted(self):
        body = client(ELITE).get("/api/v2/opportunities").json()
        assert body["locked_count"] == 0
        assert body["items"][0]["recommended_sell_target"] is not None
        assert "free_picks" not in body, "Elite has no daily allowance to explain"

    def test_trade_finder_is_allowed(self):
        assert client(ELITE).get("/api/v2/trade-finder").status_code == 200

    def test_strategy_finder_is_allowed(self):
        assert client(ELITE).get("/api/v2/trade-finder/strategy/best_overall").status_code == 200


class TestAdminKeepsEliteAccess:
    def test_admin_resolves_to_elite_features(self):
        admin = Viewer(user_id="admin", tier="elite", is_admin=True,
                       features=frozenset(features_for_tier("elite")))
        assert client(admin).get("/api/v2/trade-finder").status_code == 200


class TestPreviewSerialiserIsAnAllowList:
    def test_an_unknown_future_field_is_not_forwarded(self):
        # The property that makes this safe to maintain: a new premium
        # field added to the engine is absent from the preview by
        # default, rather than leaking until someone updates a blocklist.
        item = dict(buy_row())
        item["some_new_premium_metric_2027"] = 1234
        assert "some_new_premium_metric_2027" not in preview_opportunity(item)

    def test_every_executable_field_is_stripped(self):
        item = {f: 123 for f in EXECUTABLE_FIELDS}
        item["signal_reasons"] = ["a reason", "a second reason"]
        item.update({"source_card_id": 1, "name": "X", "signal": "buy"})
        assert_no_premium_fields(preview_opportunity(item))

    def test_the_leak_detector_actually_detects_a_leak(self):
        with pytest.raises(AssertionError):
            assert_no_premium_fields({"items": [{"expected_roi": 0.2}]})


# ---------------------------------------------------------------------------
# Home (the dashboard feed)
# ---------------------------------------------------------------------------
#
# Home was the last surface still deciding for itself who gets what. It
# took the current top-ranked BUY and returned it complete to anyone
# without the opportunity_feed feature - signed-out callers included,
# since /api/v2/dashboard needs no account. That was a third free pick
# nobody counted, re-derived on every request, so it changed as the market
# moved and disagreed with the two picks /api/v2/opportunities had already
# committed to for the day.

from app.auth.access import (  # noqa: E402
    preview_dashboard_recommendation,
    serialise_dashboard_feed,
    assert_no_premium_fields,
)


def _home_card(card_id: int = 111, name: str = "Kylian Mbappe"):
    """The shape dashboard.py's _futgg_to_recommendation produces."""
    return {
        "cardId": card_id,
        "player": {"name": name, "rating": 91, "version": "Team of the Week",
                   "position": "ST", "imageUrl": "https://example.com/a.png"},
        "status": "BUY", "recommendation": "BUY", "confidence": 78.0,
        "expectedRoi": 20.0, "netRoi": {"likely": 20.0},
        "entryPrice": 415000, "recommendedBuyMax": 410000,
        "recommendedSellTarget": 520000, "buyBelow": 410000,
        "expectedProfitBasis": 415000, "breakEvenPrice": 436000,
        "holdingPeriod": "2h", "risk": {"overall": "low", "score": 0.2},
        "qualifiedStrategies": ["quick_flip"], "failedGateReasons": [],
        "currentBin": 420431, "fairValue": 505000,
        "reasoning": "Trading below recent median; strong sales volume",
        "marketDrivers": ["sbc_demand"], "scores": {"momentum": 0.7},
        "updatedAt": "2026-08-30T11:50:00+00:00",
    }


class TestHomeFeedGate:
    def test_elite_home_is_untouched(self):
        cards = [_home_card(), _home_card(222, "Erling Haaland")]
        items, locked = serialise_dashboard_feed(cards, ELITE)
        assert locked == 0
        assert items == cards

    def test_a_free_member_gets_only_the_days_picks_in_full(self):
        cards = [_home_card(111), _home_card(222, "Erling Haaland"), _home_card(333, "Jude Bellingham")]
        items, locked = serialise_dashboard_feed(cards, FREE, unlocked_ids=[111])
        assert locked == 2
        assert items[0]["recommendedSellTarget"] == 520000
        for item in items[1:]:
            assert item["locked"] is True

    def test_a_signed_out_caller_gets_no_unlocked_card_at_all(self):
        # /api/v2/dashboard requires no account, so "free member with no
        # picks resolved" and "anonymous" must land in the same place.
        cards = [_home_card(), _home_card(222, "Erling Haaland")]
        items, locked = serialise_dashboard_feed(cards, ANON)
        assert locked == 2
        for item in items:
            assert_no_premium_fields(item)

    def test_a_locked_home_card_carries_no_executable_number(self):
        preview = preview_dashboard_recommendation(_home_card())
        assert_no_premium_fields(preview)
        for banned in ("recommendedBuyMax", "recommendedSellTarget", "buyBelow",
                       "entryPrice", "expectedRoi", "confidence", "fairValue",
                       "breakEvenPrice", "currentBin", "reasoning"):
            assert banned not in preview, f"{banned} survived into a locked Home card"

    def test_a_locked_home_card_is_still_worth_looking_at(self):
        # The preview has to prove the call is about a real, current card.
        preview = preview_dashboard_recommendation(_home_card())
        assert preview["player"]["name"] == "Kylian Mbappe"
        assert preview["recommendation"] == "BUY"
        assert preview["risk"] == {"overall": "low"}
        assert preview["headlineReason"] == "Trading below recent median"
        # Approximate, and labelled as such: enough to tell a 5k card from
        # a 500k one, not a price to buy against.
        assert preview["currentBinApprox"] == 420000
        assert preview["currentBinApprox"] % 1000 == 0

    def test_the_risk_score_does_not_survive_even_though_the_label_does(self):
        preview = preview_dashboard_recommendation(_home_card())
        assert "score" not in preview["risk"]

    def test_a_card_with_no_price_previews_without_inventing_one(self):
        card = _home_card()
        card["currentBin"] = None
        assert preview_dashboard_recommendation(card)["currentBinApprox"] is None

    def test_a_new_premium_field_cannot_leak_by_default(self):
        # The allow-list is the point: a field added to the Home
        # recommendation shape tomorrow is absent from a locked card
        # without anyone remembering to exclude it.
        card = _home_card()
        card["someNewProfitField"] = 99999
        assert "someNewProfitField" not in preview_dashboard_recommendation(card)


# ---------------------------------------------------------------------------
# Signing in, when it goes wrong
# ---------------------------------------------------------------------------
#
# The OAuth callback is reached by top-level navigation from Discord, not
# by XHR. Every failure in it used to raise HTTPException, which rendered
# a raw JSON body on a blank page with no way back - and the single most
# common one is a person pressing Cancel on the Discord consent dialog,
# which is not an error at all.

import os as _os  # noqa: E402
import re  # noqa: E402

_os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
_os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
_os.environ.setdefault("ENV", "test")


class TestOAuthFailuresLandSomewhereUsable:
    def test_a_cancelled_sign_in_goes_back_to_signup(self):
        import main

        response = main._oauth_failure_redirect("access_denied")
        assert response.status_code in (307, 302)
        assert "/#/signup?error=access_denied" in response.headers["location"]

    def test_the_origin_is_carried_back_so_the_retry_lands_where_they_started(self):
        import main

        location = main._oauth_failure_redirect("access_denied", return_to="/trade-finder").headers["location"]
        assert "from=%2Ftrade-finder" in location

    def test_a_bare_root_origin_is_not_carried_as_noise(self):
        import main

        assert "from=" not in main._oauth_failure_redirect("access_denied", return_to="/").headers["location"]

    def test_nothing_from_discord_is_reflected_into_the_url(self):
        # `error` and `error_description` arrive on a URL anyone can
        # construct. Only our own allow-listed codes reach the address bar.
        import main

        location = main._oauth_failure_redirect("<script>alert(1)</script>").headers["location"]
        assert "script" not in location
        assert "error=server_error" in location

    def test_every_code_the_callback_can_emit_is_allow_listed(self):
        import inspect

        import main

        source = inspect.getsource(main.callback)
        emitted = set(re.findall(r'_oauth_failure_redirect\(\s*"([a-z_]+)"', source))
        assert emitted, "the callback no longer redirects on failure"
        assert emitted <= main._OAUTH_ERROR_CODES, emitted - main._OAUTH_ERROR_CODES

    def test_the_state_check_still_fails_closed(self):
        # Redirecting instead of raising must not have turned the CSRF
        # check into a fail-open path: an unknown state still never
        # reaches the token exchange.
        import inspect

        import main

        source = inspect.getsource(main.callback)
        state_check = source.index("if state_meta is None:")
        exchange = source.index("DISCORD_OAUTH_TOKEN")
        assert state_check < exchange, "the OAuth state is no longer validated before the code is spent"
        assert "expired_state" in source[state_check:exchange]

    def test_the_extension_flow_still_gets_an_error_not_a_redirect(self):
        # There is no page to send an extension's OAuth tab back to.
        import inspect

        import main

        source = inspect.getsource(main.callback)
        assert "is_browser_flow" in source
        assert 'state_meta.get("flow") == "dashboard"' in source


class TestAccountDeletionRemovesWhatThisPhaseAdded:
    def test_onboarding_answers_go_with_the_account(self):
        import inspect

        import main

        source = inspect.getsource(main.delete_account)
        assert "member_onboarding" in source, (
            "migration 074 stores the user's stated coin budget and alert "
            "preference; deleting an account must delete them too"
        )

    def test_the_entitlements_cache_is_cleared_on_deletion(self):
        # The tier is cached for up to ENTITLEMENTS_CACHE_TTL. A deleted
        # account answering "elite" from cache for another minute is a
        # deleted account that still has access.
        import inspect

        import main

        assert "invalidate_entitlements_cache" in inspect.getsource(main.delete_account)

    def test_deletion_revokes_the_discord_role(self):
        import inspect

        import main

        source = inspect.getsource(main.delete_account)
        assert "enqueue_role_sync" in source
        assert "desired=False" in source

    def test_billing_history_is_never_deleted(self):
        import inspect

        import main

        source = inspect.getsource(main.delete_account)
        assert "DELETE FROM subscriptions" not in source
        assert "DELETE FROM payments" not in source
