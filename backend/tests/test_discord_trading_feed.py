import os
from datetime import datetime, timedelta, timezone

import pytest

from app.services.discord_trading_feed import (
    ServerRecord,
    build_free_overview_embed,
    build_opportunity_post_embed,
    feed_globally_enabled,
    _liquidity_label,
)

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _server(**overrides):
    base = dict(
        id=1,
        guild_id="123",
        name="Test",
        access_level="paid_partner",
        feed_channel_id="456",
        proof_channel_id=None,
        status="active",
        expires_at=None,
    )
    base.update(overrides)
    return ServerRecord(**base)


def test_feed_globally_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("DISCORD_TRADING_FEED_ENABLED", raising=False)
    assert feed_globally_enabled() is False


def test_feed_globally_enabled_requires_exact_flag(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "true")
    assert feed_globally_enabled() is False
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    assert feed_globally_enabled() is True


def test_paused_server_is_never_postable():
    server = _server(status="paused")
    assert server.is_postable is False


def test_free_server_ignores_expiry():
    server = _server(access_level="free", status="active", expires_at=NOW - timedelta(days=1))
    assert server.is_postable is True


def test_paid_partner_stops_posting_after_expiry():
    now = datetime.now(timezone.utc)
    server = _server(access_level="paid_partner", status="active", expires_at=now - timedelta(minutes=1))
    assert server.is_postable is False


def test_paid_partner_postable_before_expiry():
    now = datetime.now(timezone.utc)
    server = _server(access_level="paid_partner", status="active", expires_at=now + timedelta(days=1))
    assert server.is_postable is True


def test_liquidity_label_bands():
    assert _liquidity_label(0.9) == "High"
    assert _liquidity_label(0.5) == "Moderate"
    assert _liquidity_label(0.1) == "Limited"
    assert _liquidity_label(None) == "Unknown"


def test_free_overview_embed_has_no_player_level_fields():
    """The free-tier daily overview must never carry a card name, price, or
    any field that would let a free server reconstruct a live opportunity -
    only aggregate counts and the promo line."""
    stats = {
        "successful": 4,
        "unsuccessful": 1,
        "open": 12,
        "total_estimated_profit": 8200,
        "best_trades": [{"name": "Sample Player", "result": 3000}],
    }
    embed = build_free_overview_embed(stats, "https://app.eafc.live")
    field_names = {f["name"] for f in embed["fields"]}
    assert "TRADES" in field_names
    assert "TOTAL ESTIMATED PROFIT" in field_names
    assert "EAFC.LIVE" in field_names
    # Best trades are named (marketing proof), but no price/target/risk
    # fields for a *live, open* opportunity are present anywhere.
    assert not any("BUY" in f["name"] or "SELL TARGET" in f["name"] for f in embed["fields"])


def test_opportunity_post_embed_includes_required_fields():
    row = {"name": "Erling Haaland", "version": "TOTS", "current_executable_buy": 500000}

    class CI:
        recommended_sell_target = 620000
        expected_profit_after_tax = 89000.0
        risk_level = "medium"
        liquidity_score = 0.8
        confidence_score = 0.9
        recommended_buy_max = 505000

    embed = build_opportunity_post_embed(row, CI())
    names = {f["name"] for f in embed["fields"]}
    for expected in ("BUY", "SELL TARGET", "EXPECTED PROFIT (after EA tax)", "RISK", "SELLING SPEED", "CONFIDENCE"):
        assert expected in names


def test_test_post_embed_is_clearly_labelled():
    row = {"name": "Sample Player", "current_executable_buy": 1000}

    class CI:
        recommended_sell_target = 1200
        expected_profit_after_tax = 100.0
        risk_level = "low"
        liquidity_score = 0.5
        confidence_score = 0.5
        recommended_buy_max = 1000

    embed = build_opportunity_post_embed(row, CI(), test=True)
    assert "TEST" in embed["title"]
    assert "[TEST]" in embed["description"]
