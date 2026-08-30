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
