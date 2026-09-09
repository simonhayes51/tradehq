from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.services import discord_free_server_feed as free_feed
from app.services.discord_commands.feed import FEED_COMMAND, PLAN_CHOICES


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.fetchrow_calls = []

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return self.row


def _candidate_row():
    return {
        "source_card_id": 123,
        "name": "Example Player",
        "rating": 88,
        "rarity": "Gold Rare",
        "card_image_stored_url": "https://example.test/card.png",
        "card_image_url": None,
        "signal": "strong_buy",
        "current_executable_buy": Decimal("100000"),
        "recommended_sell_target": Decimal("115000"),
        "expected_profit_after_tax": Decimal("9250"),
        "expected_roi": Decimal("0.0925"),
        "confidence_score": Decimal("0.82"),
        "liquidity_score": Decimal("0.75"),
        "evidence_age_minutes": 20,
        "evaluated_at": datetime(2026, 9, 9, 20, 55, tzinfo=timezone.utc),
    }


def test_free_plan_is_visible_and_setup_defaults_can_be_channel_only():
    assert any(choice["value"] == "free" for choice in PLAN_CHOICES)
    setup = next(option for option in FEED_COMMAND["options"] if option["name"] == "setup")
    options = setup["options"]
    assert options[0]["name"] == "channel"
    assert options[0]["required"] is True
    assert next(o for o in options if o["name"] == "plan")["required"] is False
    assert next(o for o in options if o["name"] == "platform")["required"] is False
    assert next(o for o in options if o["name"] == "budget_mix")["required"] is False


def test_trade_of_day_embed_is_branded_and_explicitly_shared():
    embed = free_feed._trade_embed(_candidate_row())
    assert embed["title"] == "EAFC.Live Trade of the Day"
    assert "1 shared trade/day" in embed["footer"]["text"]
    rendered_fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert rendered_fields["BUY NOW"] == "**100,000** coins"
    assert rendered_fields["SELL TARGET"] == "**115,000** coins"
    assert rendered_fields["EVIDENCE"] == "Confirmed completed sales"


def test_candidate_selector_requires_confirmed_sales_and_quality_floor():
    conn = FakeConn(_candidate_row())
    result = run(free_feed._select_candidate(conn))
    assert result["source_card_id"] == 123
    sql, args = conn.fetchrow_calls[0]
    compact = " ".join(sql.split())
    assert "c.valuation_source='completed_sales'" in compact
    assert "c.evidence_quality='confirmed'" in compact
    assert "COALESCE(c.sales_sample_size,0) >= 3" in compact
    assert "COALESCE(c.liquidity_score,0) >= 0.30" in compact
    assert "GREATEST( 300, c.current_executable_buy * 0.015 )" in compact
    assert args == (
        free_feed.FREE_CURRENT_INTELLIGENCE_MAX_AGE_MINUTES,
        free_feed.FREE_MAX_EVIDENCE_AGE_MINUTES,
    )


def test_broadcast_opens_only_after_daily_london_boundary(monkeypatch):
    monkeypatch.setenv("DISCORD_PROOF_REPORT_TIMEZONE", "Europe/London")
    monkeypatch.setenv("DISCORD_PROOF_REPORT_HOUR", "21")
    monkeypatch.setenv("DISCORD_PROOF_REPORT_MINUTE", "0")

    # September is BST: 19:59 UTC = 20:59 London, 20:00 UTC = 21:00 London.
    before = datetime(2026, 9, 9, 19, 59, tzinfo=timezone.utc)
    at_boundary = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)
    assert free_feed._broadcast_date(before) is None
    assert free_feed._broadcast_date(at_boundary).isoformat() == "2026-09-09"
