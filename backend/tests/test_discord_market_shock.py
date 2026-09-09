from __future__ import annotations

from datetime import datetime, timezone

from app.services import discord_market_shock as shock


def test_market_shock_active_for_fc26_before_expiry(monkeypatch):
    monkeypatch.setenv("FUTGG_GAME_YEAR", "26")
    monkeypatch.setenv("EAFC_MARKET_SHOCK_PAUSE_UNTIL", "2026-09-17T00:00:00Z")
    assert shock.market_shock_active(datetime(2026, 9, 10, 12, tzinfo=timezone.utc)) is True


def test_market_shock_does_not_apply_to_fc27(monkeypatch):
    monkeypatch.setenv("FUTGG_GAME_YEAR", "27")
    monkeypatch.setenv("EAFC_MARKET_SHOCK_PAUSE_UNTIL", "2026-09-17T00:00:00Z")
    assert shock.market_shock_active(datetime(2026, 9, 10, 12, tzinfo=timezone.utc)) is False


def test_market_shock_expires_automatically(monkeypatch):
    monkeypatch.setenv("FUTGG_GAME_YEAR", "26")
    monkeypatch.setenv("EAFC_MARKET_SHOCK_PAUSE_UNTIL", "2026-09-17T00:00:00Z")
    assert shock.market_shock_active(datetime(2026, 9, 17, 0, tzinfo=timezone.utc)) is False


def test_market_shock_can_be_disabled(monkeypatch):
    monkeypatch.setenv("FUTGG_GAME_YEAR", "26")
    monkeypatch.setenv("EAFC_MARKET_SHOCK_PAUSE_UNTIL", "off")
    assert shock.market_shock_active(datetime(2026, 9, 10, 12, tzinfo=timezone.utc)) is False


def test_market_alert_copy_explains_pause_without_fake_trade(monkeypatch):
    monkeypatch.setenv("EAFC_MARKET_SHOCK_PAUSE_UNTIL", "2026-09-17T00:00:00Z")
    embed = shock.build_market_shock_embed()
    rendered = str(embed)
    assert "Market Alert" in rendered
    assert "paused new trade calls" in rendered
    assert "collecting prices, completed sales and outcomes" in rendered
    assert "BUY NOW" not in rendered
