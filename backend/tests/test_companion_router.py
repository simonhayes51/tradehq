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
    app = FastAPI()
    app.include_router(companion_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: provider
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
