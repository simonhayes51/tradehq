import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services import trading_math as tm
from app.services.discord_trading_feed import (
    ServerRecord,
    _TEST_OUTCOMES,
    _TEST_OVERVIEW_STATS,
    build_free_overview_embed,
    build_opportunity_post_embed,
    build_opportunity_update_embed,
    feed_globally_enabled,
    _dedup_amount,
    _liquidity_label,
    _opportunity_dedup_key,
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
    forbidden = {
        "BUY", "LIVE PRICE", "MAX BUY — DO NOT CHASE", "SELL TARGET",
        "PRICE CHECKED", "ENTRY WINDOW", "RISK", "SELLING SPEED", "CONFIDENCE",
    }
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
    for expected in (
        "LIVE PRICE", "MAX BUY — DO NOT CHASE", "SELL TARGET",
        "MIN PROFIT AT MAX BUY (after EA tax)", "PRICE CHECKED",
        "ENTRY WINDOW", "RISK", "SELLING SPEED", "CONFIDENCE",
    ):
        assert expected in names
    # Branded and links back to the card in the app.
    assert embed["author"]["name"] == "EAFC.Live"
    assert components and components[0]["components"][0]["url"]
    # ?src=discord_feed - PlayerPage.jsx's signed-out preview reads this
    # to acknowledge the visitor already saw this trade's numbers here,
    # rather than presenting a generic "sign up to see what it's worth"
    # wall for numbers that were just given away for free.
    link_url = components[0]["components"][0]["url"]
    assert link_url.endswith("/#/players/12345?src=discord_feed")


def test_opportunity_post_prefers_familiar_display_name():
    row = {
        "source_card_id": 12345,
        "name": "Gabriel Teodoro Martinelli Silva",
        "display_name": "Gabriel Martinelli",
        "version": "Rare",
    }

    class CI:
        recommended_sell_target = 18_250
        expected_profit_after_tax = 1_338.0
        risk_level = "medium"
        liquidity_score = 0.8
        confidence_score = 0.65
        recommended_buy_max = 16_000
        current_executable_buy = 16_000

    embed, _ = build_opportunity_post_embed(row, CI())

    assert embed["title"] == "Gabriel Martinelli — Rare"
    assert "Gabriel Teodoro Martinelli Silva" not in embed["title"]


def test_opportunity_embed_separates_live_price_from_conservative_max_buy():
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
    live_field = next(f for f in embed["fields"] if f["name"] == "LIVE PRICE")
    max_field = next(f for f in embed["fields"] if f["name"] == "MAX BUY — DO NOT CHASE")
    assert live_field["value"] == "935,000 coins"
    assert max_field["value"] == "985,000 coins"

    max_buy = Decimal(max_field["value"].split(" coins")[0].replace(",", ""))
    profit_field = next(f for f in embed["fields"] if f["name"].startswith("MIN PROFIT"))
    shown_profit = Decimal(profit_field["value"].split(" coins")[0].replace(",", ""))
    real_profit = Decimal(CI.recommended_sell_target) * Decimal("0.95") - max_buy
    assert shown_profit == real_profit, (
        "MAX BUY and minimum profit must describe the same conservative entry price - "
        f"shown profit {shown_profit} does not match {real_profit} "
        f"computed from the shown MAX BUY"
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
    live_field = next(f for f in embed["fields"] if f["name"] == "LIVE PRICE")
    max_field = next(f for f in embed["fields"] if f["name"] == "MAX BUY — DO NOT CHASE")
    assert live_field["value"] == "800 coins"
    assert max_field["value"] == "800 coins"


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
# _opportunity_dedup_key must NOT change just because
# futgg_market_snapshot's REFRESH MATERIALIZED VIEW bumped
# snapshot_computed_at - otherwise the same unchanged opportunity gets a
# fresh dedup_key every refresh and is reposted once cooldown_minutes
# elapses. Regression test for identical embeds posted ~20 min apart in
# production (same buy/sell/profit, different snapshot_computed_at).
# ---------------------------------------------------------------------------


class _CI:
    def __init__(self, buy=15000, sell=20000, profit=5000, current_executable_buy=None):
        self.recommended_buy_max = buy
        self.current_executable_buy = current_executable_buy
        self.recommended_sell_target = sell
        self.expected_profit_after_tax = profit


def test_dedup_key_is_stable_across_snapshot_refreshes():
    """Same card, same buy/sell/profit - only snapshot_computed_at (not
    passed to the key at all) differs between two MV refresh cycles."""
    key_before = _opportunity_dedup_key(42, _CI())
    key_after = _opportunity_dedup_key(42, _CI())
    assert key_before == key_after


def test_dedup_key_changes_when_the_opportunity_actually_changes():
    key_a = _opportunity_dedup_key(42, _CI(sell=20000))
    key_b = _opportunity_dedup_key(42, _CI(sell=21000))
    assert key_a != key_b


def test_dedup_key_differs_by_card():
    key_a = _opportunity_dedup_key(42, _CI())
    key_b = _opportunity_dedup_key(43, _CI())
    assert key_a != key_b


def test_dedup_key_prefers_current_executable_buy_over_ceiling():
    key_ceiling_only = _opportunity_dedup_key(42, _CI(buy=15000, current_executable_buy=None))
    key_lower_executable = _opportunity_dedup_key(42, _CI(buy=15000, current_executable_buy=13000))
    assert key_ceiling_only != key_lower_executable


@pytest.mark.parametrize(
    "a, b",
    [
        (Decimal("25000"), Decimal("25000.00")),
        (25000, Decimal("25000")),
        (Decimal("25000.4"), Decimal("25000")),
    ],
)
def test_dedup_amount_normalizes_equivalent_values(a, b):
    assert _dedup_amount(a) == _dedup_amount(b)


def test_dedup_amount_handles_none():
    assert _dedup_amount(None) == "none"


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

    def __init__(self, *, settings_row, posts_sent_today=0, last_post_at=None, budget_row=None,
                 recently_posted_card_ids=()):
        self._settings_row = settings_row
        self._posts_sent_today = posts_sent_today
        self._last_post_at = last_post_at
        self._budget_row = budget_row or {
            "budget_low": None, "budget_medium": None, "budget_high": None,
            "posting_window_start_hour": None, "posting_window_end_hour": None, "posting_timezone": None,
        }
        self._recently_posted_card_ids = set(recently_posted_card_ids)
        self.record_calls = 0

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, query, *args):
        if "source_card_id" in query and "discord_server_posts" in query:
            return [{"card_id": cid} for cid in self._recently_posted_card_ids]
        return []

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


def _live_recheck_provider(now, latest_price):
    provider = MagicMock()
    provider.get_player = AsyncMock(return_value={
        "source_card_id": 1,
        "bin_checked_at": now - timedelta(minutes=1),
    })
    provider.get_live_card_market_state = AsyncMock(return_value={
        "current_bin": latest_price,
        "bin_checked_at": now - timedelta(minutes=1),
    })
    provider.get_recent_sales = AsyncMock(return_value=[{"sold_price": 11_000}])
    provider.get_bin_observations_by_ids = AsyncMock(return_value={1: []})
    return provider


def test_final_recheck_recalculates_from_live_market_data(monkeypatch):
    import app.services.discord_trading_feed as feed_module
    import app.services.futgg_intelligence as intelligence

    now = datetime(2026, 9, 18, 19, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)

    latest_ci = MagicMock(
        signal="buy",
        current_executable_buy=10_400,
        recommended_buy_max=10_500,
    )
    evaluate = MagicMock(return_value=latest_ci)
    monkeypatch.setattr(intelligence, "evaluate_card", evaluate)
    provider = _live_recheck_provider(now, 10_400)

    original_ci = MagicMock(current_executable_buy=10_000, recommended_buy_max=10_500)
    result = asyncio.run(
        feed_module._final_recheck_opportunity(
            provider, {"source_card_id": 1}, original_ci,
        )
    )

    assert result is not None
    assert result[0]["price_age_minutes_at_post"] == 1
    provider.get_live_card_market_state.assert_awaited_once_with(1)
    evaluate.assert_called_once()
    assert evaluate.call_args.kwargs["sales"] == [{"sold_price": 11_000}]


@pytest.mark.parametrize(
    ("latest_price", "age_minutes"),
    [(10_600, 1), (10_400, 4)],
)
def test_final_recheck_rejects_moved_or_stale_live_prices(
    monkeypatch, latest_price, age_minutes,
):
    import app.services.discord_trading_feed as feed_module
    import app.services.futgg_intelligence as intelligence

    now = datetime(2026, 9, 18, 19, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)

    latest_ci = MagicMock(
        signal="buy",
        current_executable_buy=latest_price,
        recommended_buy_max=11_000,
    )
    monkeypatch.setattr(intelligence, "evaluate_card", MagicMock(return_value=latest_ci))
    provider = _live_recheck_provider(now, latest_price)
    provider.get_live_card_market_state.return_value["bin_checked_at"] = (
        now - timedelta(minutes=age_minutes)
    )
    original_ci = MagicMock(current_executable_buy=10_000, recommended_buy_max=10_500)

    assert asyncio.run(
        feed_module._final_recheck_opportunity(
            provider, {"source_card_id": 1}, original_ci,
        )
    ) is None

def test_publish_live_opportunities_posts_only_one_per_call(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.routers.v2.futgg_market as futgg_market
    import app.db as db
    import app.services.discord_trading_feed as feed_module

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
    monkeypatch.setattr(futgg_market, "_score_and_filter", AsyncMock(return_value=scored))
    monkeypatch.setattr(db, "get_core_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        feed_module,
        "_final_recheck_opportunity",
        AsyncMock(side_effect=lambda _provider, row, ci: (row, ci)),
    )

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
    score_kwargs = futgg_market._score_and_filter.await_args.kwargs
    assert score_kwargs["min_confidence"] == feed_module.DISCORD_FEED_MIN_CONFIDENCE
    assert score_kwargs["min_roi"] == feed_module.DISCORD_FEED_MIN_ROI
    assert score_kwargs["min_liquidity"] == feed_module.DISCORD_FEED_MIN_LIQUIDITY
    assert score_kwargs["require_confirmed_sales"] is True
    assert score_kwargs["max_evidence_age_minutes"] == feed_module.DISCORD_FEED_MAX_EVIDENCE_AGE_MINUTES


def test_discord_result_window_is_72_hours():
    from app.services.discord_trading_feed import OPPORTUNITY_UPDATE_HORIZON
    from app.services.futgg_outcome_grader import HORIZONS

    assert OPPORTUNITY_UPDATE_HORIZON == "72h"
    assert HORIZONS["72h"] == timedelta(hours=72)


def test_live_opportunity_has_track_trade_button():
    from app.services.discord_trading_feed import _FakeIntelligence, build_opportunity_post_embed

    _embed, components = build_opportunity_post_embed(
        {"source_card_id": 123, "name": "Test Player", "rating": 85, "rarity": "Rare"},
        _FakeIntelligence(),
    )
    buttons = components[0]["components"]
    assert buttons[0]["label"] == "🔔 Track this trade"
    assert buttons[0]["custom_id"].startswith("track_trade:123:")
    assert buttons[1]["label"] == "View in EAFC.Live"


def test_opportunity_embed_rejects_stale_cross_cycle_stored_image():
    from app.services.discord_trading_feed import (
        _FakeIntelligence,
        build_opportunity_post_embed,
    )

    embed, _components = build_opportunity_post_embed(
        {
            "source_card_id": 1,
            "game_year": 27,
            "name": "Marco Palestra",
            "rating": 78,
            "rarity": "Rare",
            "card_image_stored_url": (
                "https://cdn.eafc.live/fc26/futgg-card-images/1/old.webp"
            ),
            "card_image_url": "https://cdn.fut.gg/fc27/palestra.webp",
        },
        _FakeIntelligence(),
    )

    assert embed["thumbnail"]["url"] == "https://cdn.fut.gg/fc27/palestra.webp"


def test_card_repost_cooldown_is_one_rolling_day():
    from app.services.discord_trading_feed import CARD_REPOST_COOLDOWN_MINUTES

    assert CARD_REPOST_COOLDOWN_MINUTES == 1440


def test_publish_live_opportunities_skips_a_card_posted_too_recently(monkeypatch):
    """Regression test: the same card (e.g. Marcelo) qualifying again a
    few minutes later with a slightly different current_executable_buy
    must NOT repost - a price wobble mints a new dedup_key but isn't a
    new opportunity. Confirmed live: BUY 75,500 at 18:15, then BUY 77,000
    for the identical card at 18:36."""
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.routers.v2.futgg_market as futgg_market
    import app.db as db

    conn = _FakeAcquireConn(
        settings_row={"max_posts_per_day": 12, "min_confidence": 0.5, "min_expected_profit": 1000,
                      "cooldown_minutes": 15, "platform": "ps", "live_opportunities_enabled": True},
        posts_sent_today=0,
        last_post_at=None,
        recently_posted_card_ids={1},
    )

    class CI:
        def __init__(self):
            self.confidence_score = 0.9
            self.expected_profit_after_tax = 5000.0
            self.recommended_sell_target = 20000
            self.recommended_buy_max = 15000
            self.current_executable_buy = 15500  # moved slightly since the earlier post
            self.risk_level = "low"
            self.liquidity_score = 0.8

    rows = [{"source_card_id": 1, "name": "Marcelo", "current_executable_buy": 15500, "snapshot_computed_at": None}]
    scored = [(rows[0], CI())]

    monkeypatch.setattr(futgg_market, "_segmented_candidates", AsyncMock(return_value=rows))
    monkeypatch.setattr(futgg_market, "_sales_for_rows", AsyncMock(return_value={}))
    monkeypatch.setattr(futgg_market, "_score_and_filter", AsyncMock(return_value=scored))
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
    monkeypatch.setattr(futgg_market, "_score_and_filter", AsyncMock(return_value=[]))
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


# ---------------------------------------------------------------------------
# publish_opportunity_updates must grade a posted opportunity against ITS
# OWN buy_price/sell_target (stored in the post's payload), never against
# "whatever futgg_recommendation_outcomes last graded for this card_id" -
# that table is fed by a separately-scheduled scanner that can (and did,
# in production) attach a completely different trade's result to the one
# actually shown to users. Regression test for a TARGET HIT result whose
# profit was mathematically impossible from the posted buy price.
# ---------------------------------------------------------------------------


class _FakeUpdatesConn:
    def __init__(self, *, pending, bin_history):
        self._pending = pending
        self._bin_history = bin_history  # dict[card_id] -> list[{"lowest_bin", "captured_at"}]
        self.record_calls = 0

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, query, *args):
        if "FROM discord_server_posts p" in query:
            return self._pending
        if "FROM futgg_bin_history" in query:
            return self._bin_history.get(args[0], [])
        return []

    async def fetchval(self, query, *args):
        if "INSERT INTO discord_server_posts" in query:
            self.record_calls += 1
            return self.record_calls
        return None

    async def execute(self, *a, **kw):
        return None


def _pending_post(*, payload, sent_at, dedup_key="dk1"):
    return {"post_id": 1, "payload": payload, "dedup_key": dedup_key, "sent_at": sent_at}


def test_publish_opportunity_updates_grades_the_posts_own_prices(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)
    sent_at = now - timedelta(hours=2)

    card_id = 999
    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={"source_card_id": card_id, "name": "Gregor Kobel", "buy_price": 5500.0, "sell_target": 9600.0},
            sent_at=sent_at,
        )],
        bin_history={
            card_id: [
                {"lowest_bin": 5400, "captured_at": sent_at + timedelta(minutes=5)},
                {"lowest_bin": 9700, "captured_at": sent_at + timedelta(minutes=30)},
            ],
        },
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_opportunity_updates

    updated = asyncio.run(publish_opportunity_updates(conn, server, fake_client))
    assert updated == 1
    embed = fake_client.post_embed.call_args.args[1]
    result_field = next(f for f in embed["fields"] if f["name"] == "RESULT")
    # Profit must be derivable from THIS post's own 5,500 buy - not some
    # other, unrelated recommendation's much lower entry.
    assert "TARGET HIT" in embed["title"]
    assert result_field["value"] != "n/a"
    expected = tm.net_profit(Decimal("9600"), Decimal("5500"))
    assert result_field["value"] == f"{expected:+,.0f} coins"


def test_publish_opportunity_updates_silently_expires_an_unavailable_entry(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 9, 18, 19, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)
    sent_at = now - timedelta(minutes=30)
    entry_expires_at = sent_at + timedelta(minutes=15)
    card_id = 777
    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={
                "source_card_id": card_id,
                "name": "Fast Market Card",
                "buy_price": 10_000,
                "sell_target": 12_000,
                "entry_expires_at": entry_expires_at.isoformat(),
            },
            sent_at=sent_at,
        )],
        bin_history={
            card_id: [
                # The price only became affordable after the published
                # entry window, so this must never count as an entry.
                {"lowest_bin": 10_500, "captured_at": sent_at + timedelta(minutes=5)},
                {"lowest_bin": 9_900, "captured_at": sent_at + timedelta(minutes=20)},
            ],
        },
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    updated = asyncio.run(feed_module.publish_opportunity_updates(conn, server, fake_client))

    assert updated == 0
    assert fake_client.post_embed.await_count == 0
    assert conn.record_calls == 1


def test_publish_opportunity_updates_reports_the_advertised_profit_not_a_glitched_entry(monkeypatch):
    """A confirmed target_hit's reported profit must equal what the
    original post promised (buy_price -> sell_target), never grade_
    recommendation()'s own realised entry/exit prices - those come from
    whatever futgg_bin_history actually recorded, and its "entry = first
    observation at or below buy_price" rule means one bad/glitched row
    (an 89-rated card briefly showing a few-hundred-coin price is not a
    real fill) silently inflates the reported result far past what was
    ever advertised. Regression test for a BUY 5,000 / SELL TARGET 9,600
    post - "+4,120 expected" in its own embed - reported as "TARGET HIT
    +8,605" (implying an entry near 515) after this exact fix shipped."""
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)
    sent_at = now - timedelta(hours=2)

    card_id = 999
    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={"source_card_id": card_id, "name": "Mariona", "buy_price": 5000.0, "sell_target": 9600.0},
            sent_at=sent_at,
        )],
        bin_history={
            card_id: [
                # A glitched near-worthless reading, at or below buy_price
                # so the raw grader would lock onto it as "entry" - not a
                # real listing for an 89-rated card.
                {"lowest_bin": 515, "captured_at": sent_at + timedelta(minutes=2)},
                {"lowest_bin": 9700, "captured_at": sent_at + timedelta(minutes=30)},
            ],
        },
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_opportunity_updates

    updated = asyncio.run(publish_opportunity_updates(conn, server, fake_client))
    assert updated == 1
    embed = fake_client.post_embed.call_args.args[1]
    result_field = next(f for f in embed["fields"] if f["name"] == "RESULT")
    expected = tm.net_profit(Decimal("9600"), Decimal("5000"))  # matches the original post's "+4,120 expected"
    assert result_field["value"] == f"{expected:+,.0f} coins"
    # The bad reading would have implied roughly an 8,605 result - never that.
    assert "8,605" not in result_field["value"]


def test_publish_opportunity_updates_posts_to_the_feed_channel_by_default(monkeypatch):
    """No sell_channel_id configured - unchanged behavior: results post
    into the same channel as the original opportunity."""
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)
    sent_at = now - timedelta(hours=2)
    card_id = 999
    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={"source_card_id": card_id, "name": "Gregor Kobel", "buy_price": 5500.0, "sell_target": 9600.0},
            sent_at=sent_at,
        )],
        bin_history={card_id: [
            {"lowest_bin": 5400, "captured_at": sent_at + timedelta(minutes=5)},
            {"lowest_bin": 9700, "captured_at": sent_at + timedelta(minutes=30)},
        ]},
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_opportunity_updates

    asyncio.run(publish_opportunity_updates(conn, server, fake_client))
    assert fake_client.post_embed.call_args.args[0] == "chan1"


def test_publish_opportunity_updates_posts_to_the_configured_sell_channel(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)
    sent_at = now - timedelta(hours=2)
    card_id = 999
    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={"source_card_id": card_id, "name": "Gregor Kobel", "buy_price": 5500.0, "sell_target": 9600.0},
            sent_at=sent_at,
        )],
        bin_history={card_id: [
            {"lowest_bin": 5400, "captured_at": sent_at + timedelta(minutes=5)},
            {"lowest_bin": 9700, "captured_at": sent_at + timedelta(minutes=30)},
        ]},
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None, sell_channel_id="chan-sell",
    )

    from app.services.discord_trading_feed import publish_opportunity_updates

    asyncio.run(publish_opportunity_updates(conn, server, fake_client))
    assert fake_client.post_embed.call_args.args[0] == "chan-sell"


def test_publish_opportunity_updates_skips_posts_without_stored_prices(monkeypatch):
    """A post made before this fix (or a test post) has no buy_price/
    sell_target in its payload - there is nothing honest to grade, so no
    update is ever fabricated for it."""
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)

    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={"source_card_id": 999, "name": "Old Post"}, sent_at=now - timedelta(hours=2),
        )],
        bin_history={},
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_opportunity_updates

    updated = asyncio.run(publish_opportunity_updates(conn, server, fake_client))
    assert updated == 0
    assert fake_client.post_embed.await_count == 0


def test_publish_opportunity_updates_withholds_a_loss_until_the_window_elapses(monkeypatch):
    """Target hits are reported the moment they're observed, but a
    negative/flat result must wait for the full grading window - an early
    read of a still-open, merely-down position is not a loss yet."""
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)
    sent_at = now - timedelta(hours=1)  # 72h horizon window has not elapsed yet

    card_id = 999
    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={"source_card_id": card_id, "name": "Gregor Kobel", "buy_price": 5500.0, "sell_target": 9600.0},
            sent_at=sent_at,
        )],
        bin_history={
            card_id: [
                {"lowest_bin": 5400, "captured_at": sent_at + timedelta(minutes=5)},
                {"lowest_bin": 4500, "captured_at": sent_at + timedelta(minutes=30)},
            ],
        },
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_opportunity_updates

    updated = asyncio.run(publish_opportunity_updates(conn, server, fake_client))
    assert updated == 0
    assert fake_client.post_embed.await_count == 0


def test_publish_opportunity_updates_posts_target_not_reached_after_72h(monkeypatch):
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")
    import app.services.discord_trading_feed as feed_module

    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(feed_module, "_now", lambda: now)
    sent_at = now - timedelta(hours=73)  # 72h horizon window has fully elapsed

    card_id = 999
    conn = _FakeUpdatesConn(
        pending=[_pending_post(
            payload={"source_card_id": card_id, "name": "Gregor Kobel", "buy_price": 5500.0, "sell_target": 9600.0},
            sent_at=sent_at,
        )],
        bin_history={
            card_id: [
                {"lowest_bin": 5400, "captured_at": sent_at + timedelta(minutes=5)},
                {"lowest_bin": 4500, "captured_at": sent_at + timedelta(minutes=30)},
            ],
        },
    )
    fake_client = MagicMock()
    fake_client.post_embed = AsyncMock(return_value=(200, {"id": "msg1"}))
    server = ServerRecord(
        id=1, guild_id="g1", name="Test", access_level="paid_partner", feed_channel_id="chan1",
        proof_channel_id=None, status="active", expires_at=None,
    )

    from app.services.discord_trading_feed import publish_opportunity_updates

    updated = asyncio.run(publish_opportunity_updates(conn, server, fake_client))
    assert updated == 1
    assert fake_client.post_embed.await_count == 1
    embed = fake_client.post_embed.call_args.args[1]
    assert "TARGET NOT REACHED" in embed["title"]
    assert "72-hour" in embed["fields"][0]["value"]
    # A durable sent opportunity_update prevents the same expired call
    # being reconsidered on every worker pass.
    assert conn.record_calls == 1
