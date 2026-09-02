import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.discord_trading_feed import (
    ServerRecord,
    _TEST_OUTCOMES,
    _TEST_OVERVIEW_STATS,
    build_free_overview_embed,
    build_opportunity_post_embed,
    build_opportunity_update_embed,
    feed_globally_enabled,
    _liquidity_label,
    _within_posting_window,
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


def test_posting_window_unset_never_restricts():
    assert _within_posting_window(None, None, None) is True
    assert _within_posting_window(None, None, "Europe/London") is True


def test_posting_window_simple_range():
    noon = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    three_am = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    assert _within_posting_window(8, 23, "UTC", now=noon) is True
    assert _within_posting_window(8, 23, "UTC", now=three_am) is False


def test_posting_window_overnight_wraparound():
    # 20 -> 6 covers 20:00 through 05:59.
    late_night = datetime(2026, 8, 20, 22, 0, tzinfo=timezone.utc)
    early_morning = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
    midday = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    assert _within_posting_window(20, 6, "UTC", now=late_night) is True
    assert _within_posting_window(20, 6, "UTC", now=early_morning) is True
    assert _within_posting_window(20, 6, "UTC", now=midday) is False


def test_posting_window_equal_bounds_means_unrestricted():
    assert _within_posting_window(9, 9, "UTC", now=datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)) is True


def test_posting_window_uses_the_configured_timezone():
    # 23:30 UTC is 00:30 the next day in Europe/London during BST (+1h in
    # August) - a window of 8-23 local should already be closed by then.
    late_utc = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    assert _within_posting_window(8, 23, "Europe/London", now=late_utc) is False


def test_posting_window_falls_back_to_utc_on_bad_timezone():
    three_am = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    assert _within_posting_window(8, 23, "Not/AZone", now=three_am) is False


def test_liquidity_label_bands():
    assert _liquidity_label(0.9) == "High"
    assert _liquidity_label(0.5) == "Moderate"
    assert _liquidity_label(0.1) == "Limited"
    assert _liquidity_label(None) == "Unknown"


def test_free_overview_embed_reports_completed_trade_stats():
    stats = {
        "successful": 4,
        "unsuccessful": 1,
        "open": 12,
        "total_estimated_profit": 8200,
        "best_trades": [
            {"name": "Sample Player", "entry": 10000, "exit": 13500, "result": 3000},
        ],
    }
    embed = build_free_overview_embed(stats, "https://app.eafc.live")
    field_names = {f["name"] for f in embed["fields"]}
    assert "TRADES" in field_names
    assert any("TOTAL ESTIMATED AFTER-TAX RESULT" in name for name in field_names)
    assert "EAFC.LIVE PREMIUM" in field_names
    best_trades_field = next(f for f in embed["fields"] if f["name"] == "BEST COMPLETED TRADES")
    assert "10,000" in best_trades_field["value"]
    assert "13,500" in best_trades_field["value"]
    assert "3,000" in best_trades_field["value"]
    # Promo line must point free users toward Premium for live trades.
    promo = next(f for f in embed["fields"] if f["name"] == "EAFC.LIVE PREMIUM")
    assert "Premium" in promo["value"]


def test_free_overview_embed_never_shows_a_live_opportunity_field():
    """No BUY/SELL TARGET/RISK/CONFIDENCE fields - those only ever describe
    a currently open opportunity, which the Free tier must never expose."""
    stats = {
        "successful": 2,
        "unsuccessful": 0,
        "open": 5,
        "total_estimated_profit": 1000,
        "best_trades": [{"name": "A", "entry": 100, "exit": 200, "result": 90}],
    }
    embed = build_free_overview_embed(stats, "https://app.eafc.live")
    forbidden = {"BUY", "SELL TARGET", "RISK", "SELLING SPEED", "CONFIDENCE"}
    assert not forbidden.intersection(f["name"] for f in embed["fields"])


def test_free_overview_embed_caps_best_trades_at_three():
    stats = {
        "successful": 5,
        "unsuccessful": 0,
        "open": 0,
        "total_estimated_profit": 5000,
        "best_trades": [
            {"name": f"Player {i}", "entry": 1000, "exit": 1500, "result": 500}
            for i in range(5)
        ],
    }
    embed = build_free_overview_embed(stats, "https://app.eafc.live")
    best_trades_field = next(f for f in embed["fields"] if f["name"] == "BEST COMPLETED TRADES")
    assert best_trades_field["value"].count("•") <= 3


def test_opportunity_post_embed_includes_required_fields():
    row = {"source_card_id": 12345, "name": "Erling Haaland", "version": "TOTS", "current_executable_buy": 500000}

    class CI:
        recommended_sell_target = 620000
        expected_profit_after_tax = 89000.0
        risk_level = "medium"
        liquidity_score = 0.8
        confidence_score = 0.9
        recommended_buy_max = 505000
        current_executable_buy = 500000

    embed, components = build_opportunity_post_embed(row, CI())
    names = {f["name"] for f in embed["fields"]}
    for expected in ("BUY", "SELL TARGET", "EXPECTED PROFIT (after EA tax)", "RISK", "SELLING SPEED", "CONFIDENCE"):
        assert expected in names
    # Branded and links back to the card in the app.
    assert embed["author"]["name"] == "EAFC.Live"
    assert components and components[0]["components"][0]["url"]


def test_the_buy_field_shows_the_price_profit_was_actually_computed_from():
    """Production bug, reported live: a Discord post showed BUY 985,000 /
    SELL TARGET 1,120,000 / EXPECTED PROFIT +129,000 - but 1,120,000 *
    0.95 - 985,000 = 79,000, not 129,000. The BUY field was reading
    row.get('current_executable_buy'), and `row` is _segmented_
    candidates()'s raw futgg_market_snapshot-shaped dict, which has never
    had that column (see market_data_provider.py's _SNAPSHOT_COLUMNS) -
    so it silently always fell through to ci.recommended_buy_max (the
    conservative ceiling), while EXPECTED PROFIT below it was correctly
    computed from ci.expected_profit_after_tax, which futgg_
    intelligence.py derives from the REAL executable entry
    (ci.current_executable_buy) whenever the card is actually
    executable. The two fields described two different buy prices.

    This card is executable (current_executable_buy=935,000, well below
    the 985,000 ceiling) - the BUY field must show the real, lower,
    executable price, and that price must be exactly the one that makes
    the shown EXPECTED PROFIT correct after EA's 5% tax."""
    row = {"source_card_id": 1, "name": "Sophia Wilson", "version": "FUTTIES"}

    class CI:
        recommended_sell_target = 1_120_000
        expected_profit_after_tax = 129_000.0
        risk_level = "low"
        liquidity_score = 0.9
        confidence_score = 0.95
        recommended_buy_max = 985_000
        current_executable_buy = 935_000

    embed, _ = build_opportunity_post_embed(row, CI())
    buy_field = next(f for f in embed["fields"] if f["name"] == "BUY")
    assert buy_field["value"] == "935,000 coins"

    buy_price = Decimal(buy_field["value"].split(" coins")[0].replace(",", ""))
    profit_field = next(f for f in embed["fields"] if f["name"].startswith("EXPECTED PROFIT"))
    shown_profit = Decimal(profit_field["value"].split(" coins")[0].replace(",", ""))
    real_profit = Decimal(CI.recommended_sell_target) * Decimal("0.95") - buy_price
    assert shown_profit == real_profit, (
        "BUY and EXPECTED PROFIT must describe the same entry price - "
        f"shown profit {shown_profit} does not match {real_profit} "
        f"computed from the shown BUY price"
    )


def test_the_buy_field_falls_back_to_the_ceiling_when_not_executable():
    """A WATCH-shaped candidate (current_executable_buy is None because
    the live price is above what's worth paying) must still show
    something - the ceiling it's watching for, same as before."""
    row = {"source_card_id": 1, "name": "Sample"}

    class CI:
        recommended_sell_target = 1000
        expected_profit_after_tax = None
        risk_level = "medium"
        liquidity_score = 0.5
        confidence_score = 0.5
        recommended_buy_max = 800
        current_executable_buy = None

    embed, _ = build_opportunity_post_embed(row, CI())
    buy_field = next(f for f in embed["fields"] if f["name"] == "BUY")
    assert buy_field["value"] == "800 coins"


def test_test_post_embed_is_clearly_labelled():
    row = {"name": "Sample Player", "current_executable_buy": 1000}

    class CI:
        recommended_sell_target = 1200
        expected_profit_after_tax = 100.0
        risk_level = "low"
        liquidity_score = 0.5
        confidence_score = 0.5
        recommended_buy_max = 1000
        current_executable_buy = 1000

    embed, components = build_opportunity_post_embed(row, CI(), test=True)
    assert "[TEST]" in embed["title"]
    assert "TEST POST" in embed["description"]
    # A test post is never a real trade - no link back to a real card.
    assert components == []


def test_opportunity_post_embed_colors_by_risk_level():
    class CI:
        recommended_sell_target = 100
        expected_profit_after_tax = 10.0
        liquidity_score = 0.5
        confidence_score = 0.5
        recommended_buy_max = 90
        current_executable_buy = 90

        def __init__(self, risk):
            self.risk_level = risk

    row = {"source_card_id": 1, "name": "X", "current_executable_buy": 90}
    low_embed, _ = build_opportunity_post_embed(row, CI("low"))
    medium_embed, _ = build_opportunity_post_embed(row, CI("medium"))
    high_embed, _ = build_opportunity_post_embed(row, CI("high"))
    assert low_embed["color"] != medium_embed["color"] != high_embed["color"]


def test_fixture_overview_stats_are_fixed_not_queried():
    """The smoke-test fixture used by send_test_free_overview is a fixed
    dict, not the result of a query - so a test post can never leak real
    Proof Ledger numbers."""
    embed = build_free_overview_embed(_TEST_OVERVIEW_STATS, "https://app.eafc.live")
    best_trades_field = next(f for f in embed["fields"] if f["name"] == "BEST COMPLETED TRADES")
    assert "[TEST]" in best_trades_field["value"]


@pytest.mark.parametrize("status", sorted(_TEST_OUTCOMES))
def test_fixture_opportunity_update_embed_labels(status):
    embed = build_opportunity_update_embed({"name": "[TEST] Sample Player"}, _TEST_OUTCOMES[status])
    assert "[TEST]" in embed["title"]
    if status == "target_hit":
        assert "TARGET HIT" in embed["title"]
    else:
        assert "LOSS" in embed["title"]


# ---------------------------------------------------------------------------
# publish_live_opportunities must post at most one opportunity per call, so
# cooldown_minutes (checked once, at the top, against the single most
# recent post) actually paces every post via the periodic worker loop -
# not just the first post of a batch. Regression test for several posts
# landing at the identical timestamp in production.
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock, MagicMock


class _FakeAcquireConn:
    """A minimal asyncpg-shaped fake: fetchrow/fetchval/execute all
    configurable, pool.acquire() yields the same conn every time."""

    def __init__(self, *, settings_row, posts_sent_today=0, last_post_at=None, budget_row=None):
        self._settings_row = settings_row
        self._posts_sent_today = posts_sent_today
        self._last_post_at = last_post_at
        self._budget_row = budget_row or {
            "budget_low": None, "budget_medium": None, "budget_high": None,
            "posting_window_start_hour": None, "posting_window_end_hour": None, "posting_timezone": None,
        }
        self.record_calls = 0

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetchrow(self, query, *args):
        if "discord_server_settings" in query:
            return self._settings_row
        if "budget_low, budget_medium, budget_high" in query:
            return self._budget_row
        return None

    async def fetchval(self, query, *args):
        if "COUNT(*)" in query and "kind='opportunity'" in query and "budget_band" not in query:
            return self._posts_sent_today
        if "MAX(sent_at)" in query:
            return self._last_post_at
        if "INSERT INTO discord_server_posts" in query:
            self.record_calls += 1
            return self.record_calls  # a "new" post id each time - never a duplicate in this test
        return 0

    async def execute(self, *a, **kw):
        return None


def test_publish_live_opportunities_posts_only_one_per_call(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.routers.v2.futgg_market as futgg_market
    import app.db as db

    conn = _FakeAcquireConn(
        settings_row={"max_posts_per_day": 12, "min_confidence": 0.5, "min_expected_profit": 1000,
                      "cooldown_minutes": 15, "platform": "ps", "live_opportunities_enabled": True},
        posts_sent_today=0,
        last_post_at=None,
    )

    class CI:
        def __init__(self, confidence):
            self.confidence_score = confidence
            self.expected_profit_after_tax = 5000.0
            self.recommended_sell_target = 20000
            self.recommended_buy_max = 15000
            self.current_executable_buy = 15000
            self.risk_level = "low"
            self.liquidity_score = 0.8

    rows = [
        {"source_card_id": 1, "name": "Player A", "current_executable_buy": 15000, "snapshot_computed_at": None},
        {"source_card_id": 2, "name": "Player B", "current_executable_buy": 16000, "snapshot_computed_at": None},
        {"source_card_id": 3, "name": "Player C", "current_executable_buy": 17000, "snapshot_computed_at": None},
    ]
    scored = [(rows[0], CI(0.9)), (rows[1], CI(0.8)), (rows[2], CI(0.7))]

    monkeypatch.setattr(futgg_market, "_segmented_candidates", AsyncMock(return_value=rows))
    monkeypatch.setattr(futgg_market, "_sales_for_rows", AsyncMock(return_value={}))
    monkeypatch.setattr(futgg_market, "_score_and_filter", MagicMock(return_value=scored))
    monkeypatch.setattr(db, "get_core_pool", AsyncMock(return_value=object()))

    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))

    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_live_opportunities

    posted = asyncio.run(publish_live_opportunities(conn, server, fake_client))
    assert posted == 1
    assert fake_client.post_embed.await_count == 1


def test_publish_live_opportunities_skips_outside_posting_window(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.routers.v2.futgg_market as futgg_market
    import app.db as db
    import app.services.discord_trading_feed as feed_module

    # Freeze "now" to 03:00 UTC, outside an 08-23 UTC window.
    monkeypatch.setattr(
        feed_module, "_now", lambda: datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    )

    conn = _FakeAcquireConn(
        settings_row={"max_posts_per_day": 12, "min_confidence": 0.5, "min_expected_profit": 1000,
                      "cooldown_minutes": 15, "platform": "ps", "live_opportunities_enabled": True},
        posts_sent_today=0,
        last_post_at=None,
        budget_row={
            "budget_low": None, "budget_medium": None, "budget_high": None,
            "posting_window_start_hour": 8, "posting_window_end_hour": 23, "posting_timezone": "UTC",
        },
    )

    monkeypatch.setattr(futgg_market, "_segmented_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(futgg_market, "_sales_for_rows", AsyncMock(return_value={}))
    monkeypatch.setattr(futgg_market, "_score_and_filter", MagicMock(return_value=[]))
    monkeypatch.setattr(db, "get_core_pool", AsyncMock(return_value=object()))

    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))

    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_live_opportunities

    posted = asyncio.run(publish_live_opportunities(conn, server, fake_client))
    assert posted == 0
    assert fake_client.post_embed.await_count == 0
