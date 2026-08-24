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


def test_card_art_missing_never_blocks_the_render(monkeypatch):
    monkeypatch.setattr(trade_share_data, "search_cards", AsyncMock(return_value=[]))
    trade = {"player": "Someone Obscure", "version": "Standard", "buy": 100, "sell": 200, "profit": 90}
    render_data = run(trade_share_data.build_trade_share_render_data(trade))
    assert render_data["image_url"] is None
    assert render_data["player"] == "Someone Obscure"


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
