# tests/test_companion_router.py
#
# HTTP-layer tests for app/routers/v2/companion.py (the EAFC Live
# Companion browser-extension API), using the same dependency-override
# fake-provider pattern as test_futgg_market_router.py - no real DB.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.v2.companion import router as companion_router
from app.routers.v2.futgg_market import get_provider

AS_OF = datetime.now(timezone.utc)


def _row(**overrides) -> Dict[str, Any]:
    defaults = dict(
        source_card_id=117672259, source_player_id=231747, source_slug="mbappe",
        source_url="http://x/1", game_year=26, name="Kylian Mbappe",
        rating=97, primary_position="ST", alternate_positions=[],
        rarity="Team of the Year", squad=None, price_tier="special",
        club="PSG", league="Ligue 1", nation="France",
        club_source_id=1, league_source_id=1, nation_source_id=1,
        player_image_url="https://example.com/mbappe.png", card_design_image_url=None,
        club_image_url=None, league_image_url=None, nation_image_url=None,
        is_active=True, is_tradeable=True, last_price_status="success",
        metadata_warnings=[], price_updated_at=AS_OF, next_price_due_at=None,
        current_bin=1090000, price_range_low=1080000, price_range_high=1100000,
        bin_source_age_text="4 minutes ago", bin_captured_at=AS_OF - timedelta(minutes=4),
        sales_count=42, sales_median=1260000, sales_trimmed_mean=1260000,
        sales_low=1200000, sales_high=1300000, sales_stddev=15000,
        latest_sale_price=1255000, latest_sale_at=AS_OF - timedelta(minutes=2),
        sales_window_earliest_at=AS_OF - timedelta(hours=6),
        sales_window_latest_at=AS_OF - timedelta(minutes=2),
        sales_window_span_minutes=358.0, sales_dispersion_ratio=0.015,
        snapshot_computed_at=AS_OF,
    )
    defaults.update(overrides)
    return defaults


class FakeProvider:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = rows if rows is not None else [_row()]

    async def get_player(self, card_id: int):
        for r in self.rows:
            if r["source_card_id"] == card_id:
                return r
        return None

    async def get_recent_sales(self, card_id: int, limit: int = 50):
        return [
            {
                "sold_price": 1260000, "ea_tax": 63000, "net_price": 1197000,
                "approximate_sold_at": AS_OF - timedelta(minutes=3),
                "source_age_text": "3 minutes ago", "source_age_seconds": 180,
            }
            for _ in range(10)
        ]


def _make_app(provider: FakeProvider) -> TestClient:
    """The overlay's payload SHAPE, as an Elite viewer sees it. The access
    states themselves are covered at the bottom of this file."""
    from app.auth.access import Viewer as _Viewer, get_companion_viewer as _get_viewer
    from app.auth.entitlements import features_for_tier as _features

    app = FastAPI()
    app.include_router(companion_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: provider
    # The companion endpoint resolves its viewer from the cookie OR a
    # short-lived bearer token (see app/auth/companion_token.py), so the
    # override targets that resolver rather than get_viewer.
    app.dependency_overrides[_get_viewer] = lambda: _Viewer(
        user_id="elite", tier="elite", is_admin=False,
        features=frozenset(_features("elite")),
    )
    return TestClient(app)


def test_companion_card_maps_source_card_id_directly():
    client = _make_app(FakeProvider())
    resp = client.get("/api/v2/companion/card/117672259")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_card_id"] == 117672259
    assert body["name"] == "Kylian Mbappe"
    assert body["rarity"] == "Team of the Year"
    assert body["reference_bin"] == 1090000
    # sales_count comes from the snapshot's own aggregate column, not the
    # length of the raw sales list get_recent_sales() returns (that list
    # only feeds the trend layer) - see futgg_intelligence.evaluate_card.
    assert body["sales_count"] == 42


def test_companion_card_not_found_is_404_not_500():
    client = _make_app(FakeProvider(rows=[]))
    resp = client.get("/api/v2/companion/card/999999999")
    assert resp.status_code == 404


def test_companion_card_response_never_leaks_ea_session_fields():
    # The companion endpoint has no knowledge of EA session credentials at
    # all (see companion.py's module docstring) - this pins that the
    # response body never grows a field carrying one, in case a future
    # change starts threading request headers through by mistake.
    client = _make_app(FakeProvider())
    body = client.get("/api/v2/companion/card/117672259").json()
    forbidden_keys = {"x-ut-sid", "x-ut-phishing-token", "sid", "session_id", "cookie", "phishing_token"}
    assert forbidden_keys.isdisjoint({k.lower() for k in body.keys()})


def test_companion_card_includes_risk_and_signal_without_second_engine():
    client = _make_app(FakeProvider())
    body = client.get("/api/v2/companion/card/117672259").json()
    assert "risk" in body and "overall" in body["risk"]
    assert body["signal"] in (
        "strong_buy", "buy", "watch", "hold", "sell", "avoid", "insufficient_data",
    )
    assert isinstance(body["confidence"], (float, int, type(None)))


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
#
# This endpoint was the widest paywall bypass in the product: one card per
# request, no authentication, and the complete trade plan in the response.
# Iterating source_card_ids rebuilt the paid feed for free, and the
# extension shipped with `credentials: "omit"`, so nothing about the
# design even suggested a session was expected.

from app.auth.access import Viewer, get_companion_viewer  # noqa: E402
from app.auth.entitlements import features_for_tier  # noqa: E402

TRADE_PLAN_KEYS = {
    "buy_below", "recommended_buy_max", "recommended_sell_target",
    "expected_profit", "expected_profit_basis", "expected_roi",
}


def _viewer(tier, user_id="u1"):
    return Viewer(user_id=user_id, tier=tier, is_admin=False,
                  features=frozenset(features_for_tier(tier)))


def _client_as(viewer):
    app = FastAPI()
    app.include_router(companion_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_companion_viewer] = lambda: viewer
    return TestClient(app)


def test_anonymous_overlay_gets_no_trade_plan():
    body = _client_as(_viewer("free", user_id=None)).get("/api/v2/companion/card/117672259").json()
    assert TRADE_PLAN_KEYS.isdisjoint(body.keys())
    assert body["locked"] is True


def test_anonymous_overlay_gets_no_valuation_either():
    # Phase 3: the overlay used to hand a signed-out visitor the exact
    # reference BIN, the verdict, the fair value and the sales median.
    # Withholding only the trade plan still left a free valuation service
    # for anyone who installed the extension - and the complete price and
    # fair-value analysis is the headline reason to create a Free account.
    body = _client_as(_viewer("free", user_id=None)).get("/api/v2/companion/card/117672259").json()
    for withheld in ("reference_bin", "fair_value", "sales_median", "signal",
                     "status", "confidence", "liquidity_score", "trend_state", "risk"):
        assert withheld not in body, f"{withheld} reached a signed-out overlay"


def test_anonymous_overlay_still_gets_enough_to_be_useful():
    # It stays an identification aid: this IS the card, and something is
    # watching this market. It is just no longer a valuation.
    body = _client_as(_viewer("free", user_id=None)).get("/api/v2/companion/card/117672259").json()
    assert body["name"] == "Kylian Mbappe"
    assert body["rating"] == 97
    assert body["rarity"] == "Team of the Year"
    # Approximate, and labelled as such - enough to tell a 5k card from a
    # 1M one without being a price to trade against.
    assert body["price_is_approximate"] is True
    assert body["reference_bin_approx"] == 1_100_000
    assert body["reference_bin_approx"] != 1_090_000
    # And the way out is a free account, not a checkout.
    assert body["signup_url"].endswith("/#/signup")
    assert "upgrade_url" not in body


def test_a_free_member_gets_the_valuation_the_account_is_for():
    # The Free account's whole promise: the real price and analysis, just
    # not the executable plan on every card.
    body = _client_as(_viewer("free")).get("/api/v2/companion/card/117672259").json()
    assert body["reference_bin"] == 1090000
    assert body["signal"]
    assert body["upgrade_url"].endswith("/#/billing")


def test_free_member_gets_no_trade_plan_for_a_card_that_is_not_a_daily_pick(monkeypatch):
    import app.routers.v2.companion as companion

    async def none_unlocked(*_a, **_k):
        return []
    monkeypatch.setattr(companion, "unlocked_pick_ids", none_unlocked)

    body = _client_as(_viewer("free")).get("/api/v2/companion/card/117672259").json()
    assert TRADE_PLAN_KEYS.isdisjoint(body.keys())


def test_free_member_gets_the_plan_for_one_of_todays_picks(monkeypatch):
    import app.routers.v2.companion as companion

    async def unlocked(*_a, **_k):
        return [117672259]
    monkeypatch.setattr(companion, "unlocked_pick_ids", unlocked)

    body = _client_as(_viewer("free")).get("/api/v2/companion/card/117672259").json()
    assert body["recommended_sell_target"] is not None
    assert not body.get("locked")


def test_elite_overlay_is_unchanged():
    body = _client_as(_viewer("elite")).get("/api/v2/companion/card/117672259").json()
    assert TRADE_PLAN_KEYS.issubset(body.keys())
    assert "locked" not in body
