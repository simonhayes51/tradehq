import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.routers.trade_share as trade_share
import app.services.trade_share_data as trade_share_data
from app.services.trade_share_token import make_render_token, verify_render_token


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Token: signs (user_id, trade_id) TOGETHER - trades has no other unique
# key, so the render-data endpoint's only ownership guarantee is that the
# token's trade_id matches the one requested.
# ---------------------------------------------------------------------------


def test_token_round_trips():
    token = make_render_token("42", 123)
    assert verify_render_token(123, token) == "42"


def test_token_rejects_mismatched_trade_id():
    token = make_render_token("42", 123)
    assert verify_render_token(999, token) is None


def test_token_rejects_garbage():
    assert verify_render_token(123, "not-a-real-token") is None


def test_token_rejects_empty():
    assert verify_render_token(123, "") is None


# ---------------------------------------------------------------------------
# get_owned_trade is the ONLY lookup path into rendering - it must be
# scoped by (user_id, trade_id) together, since trade_id alone is only
# unique per-user, not globally.
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self, row):
        self._row = row
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append(args)
        return self._row


def test_get_owned_trade_scopes_by_user_and_trade_id(monkeypatch):
    pool = _FakePool({"player": "Mbappe", "buy": 100, "sell": 200, "profit": 90})
    monkeypatch.setattr(trade_share_data, "get_core_pool", AsyncMock(return_value=pool))
    result = run(trade_share_data.get_owned_trade("42", 123))
    assert result["player"] == "Mbappe"
    assert pool.calls == [("42", 123)]


def test_get_owned_trade_returns_none_when_not_found(monkeypatch):
    pool = _FakePool(None)
    monkeypatch.setattr(trade_share_data, "get_core_pool", AsyncMock(return_value=pool))
    assert run(trade_share_data.get_owned_trade("42", 123)) is None


# ---------------------------------------------------------------------------
# Card art resolution: card_id (v2 recommendation-flow trades) wins over
# the free-text name search; a trade with neither still produces a valid
# (art-less) render, never an error.
# ---------------------------------------------------------------------------


def test_card_art_prefers_card_id_when_present(monkeypatch):
    monkeypatch.setattr(trade_share_data, "get_card", AsyncMock(return_value={"card_image_url": "https://x/exact.png", "rating": 91}))
    trade = {"player": "Mbappe", "version": "TOTS", "card_id": 555}
    render_data = run(trade_share_data.build_trade_share_render_data({**trade, "buy": 100, "sell": 200, "profit": 90}))
    assert render_data["image_url"] == "https://x/exact.png"
    assert render_data["rating"] == 91


def test_card_art_falls_back_to_name_search_without_card_id(monkeypatch):
    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[
        {"version": "Standard", "card_image_url": "https://x/wrong.png"},
        {"version": "TOTS", "card_image_url": "https://x/right.png"},
    ]))
    trade = {"player": "Mbappe", "version": "TOTS", "buy": 100, "sell": 200, "profit": 90}
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert render_data["image_url"] == "https://x/right.png"


def test_render_data_handles_a_string_timestamp(monkeypatch):
    """Production regression: the pool behind get_core_pool() returned
    timestamp as a plain str, not a datetime, and trade["timestamp"].isoformat()
    crashed the whole share-card request with a 500."""
    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[]))
    trade = {
        "player": "X", "version": "Standard", "buy": 100, "sell": 200, "profit": 90,
        "timestamp": "2026-08-24T15:08:11+00:00",
    }
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert render_data["timestamp"] == "2026-08-24T15:08:11+00:00"


def test_render_data_handles_a_real_datetime_timestamp(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[]))
    trade = {
        "player": "X", "version": "Standard", "buy": 100, "sell": 200, "profit": 90,
        "timestamp": datetime(2026, 8, 24, 15, 8, 11, tzinfo=timezone.utc),
    }
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert render_data["timestamp"] == "2026-08-24T15:08:11+00:00"


def test_render_data_handles_a_missing_timestamp(monkeypatch):
    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[]))
    trade = {"player": "X", "version": "Standard", "buy": 100, "sell": 200, "profit": 90}
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert render_data["timestamp"] is None


def test_card_art_missing_never_blocks_the_render(monkeypatch):
    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[]))
    trade = {"player": "Someone Obscure", "version": "Standard", "buy": 100, "sell": 200, "profit": 90}
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert render_data["image_url"] is None
    assert render_data["player"] == "Someone Obscure"


# ---------------------------------------------------------------------------
# ROI: computed server-side (app/services/trade_economics.py) and handed
# to the frontend as data.roi - the frontend never recomputes it. Prior
# to this, TradeShareCardArt.jsx computed roi = profit / data.buy * 100
# itself; since `profit` is already quantity-scaled, that overstated ROI
# by a factor of quantity for any qty>1 trade.
# ---------------------------------------------------------------------------


def test_render_data_roi_accounts_for_quantity(monkeypatch):
    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[]))
    trade = {
        "player": "Haaland", "version": "TOTS", "buy": 997_000, "sell": 1_450_000,
        "quantity": 4, "profit": 1_522_000, "ea_tax": 290_000,
    }
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert round(render_data["roi"], 2) == 38.16
    # The bug this guards: dividing profit by buy alone (ignoring
    # quantity) would have produced 152.66%, not 38.16%.
    assert round(render_data["roi"], 2) != round((1_522_000 / 997_000) * 100, 2)


def test_render_data_roi_defaults_quantity_to_one(monkeypatch):
    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[]))
    trade = {"player": "X", "version": "Standard", "buy": 1_000_000, "sell": 1_500_000, "profit": 425_000}
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert render_data["roi"] == 42.5


def test_build_share_caption_labels_loss_correctly():
    caption = trade_share_data.build_share_caption({"player": "X", "version": "TOTS", "profit": -500, "buy": 1000, "sell": 400})
    assert "loss" in caption.lower()
    assert "-500" not in caption  # negative shown as "loss" wording, not a signed number


# ---------------------------------------------------------------------------
# Router: render-data endpoint rejects a bad token / not-found trade;
# the share-card endpoint requires auth and ownership.
# ---------------------------------------------------------------------------


def test_render_data_endpoint_rejects_invalid_token():
    with pytest.raises(HTTPException) as exc:
        run(trade_share.get_trade_share_render_data(123, token="garbage"))
    assert exc.value.status_code == 403


def test_render_data_endpoint_404s_when_trade_missing(monkeypatch):
    token = make_render_token("42", 123)
    monkeypatch.setattr(trade_share, "get_owned_trade", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        run(trade_share.get_trade_share_render_data(123, token=token))
    assert exc.value.status_code == 404


class _FakeRequest:
    def __init__(self, user_id=None):
        self._user_id = user_id

    def __getattr__(self, name):
        raise AttributeError(name)


def test_share_card_requires_auth(monkeypatch):
    monkeypatch.setattr(trade_share, "compute_entitlements", AsyncMock(return_value={}))
    with pytest.raises(HTTPException) as exc:
        run(trade_share.create_trade_share_card(123, _FakeRequest()))
    assert exc.value.status_code == 401


def test_share_card_404s_for_unowned_trade(monkeypatch):
    monkeypatch.setattr(trade_share, "compute_entitlements", AsyncMock(return_value={"user_id": "42"}))
    monkeypatch.setattr(trade_share, "get_owned_trade", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        run(trade_share.create_trade_share_card(123, _FakeRequest()))
    assert exc.value.status_code == 404


def test_share_card_maps_render_failure_to_502(monkeypatch):
    monkeypatch.setattr(trade_share, "compute_entitlements", AsyncMock(return_value={"user_id": "42"}))
    monkeypatch.setattr(trade_share, "get_owned_trade", AsyncMock(return_value={
        "player": "Mbappe", "version": "TOTS", "buy": 100, "sell": 200, "profit": 90,
    }))
    monkeypatch.setattr(trade_share, "build_trade_share_render_data", AsyncMock(return_value={
        "player": "Mbappe", "version": "TOTS", "buy": 100, "sell": 200, "profit": 90,
    }))

    import app.services.trade_share_render as trade_share_render

    async def raise_render_error(*a, **kw):
        raise trade_share_render.TradeShareRenderError("boom")

    monkeypatch.setattr(trade_share_render, "render_trade_share_png", raise_render_error)
    with pytest.raises(HTTPException) as exc:
        run(trade_share.create_trade_share_card(123, _FakeRequest()))
    assert exc.value.status_code == 502
