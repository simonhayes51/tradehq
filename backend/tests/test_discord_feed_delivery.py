from __future__ import annotations

import asyncio

from app.routers.v2 import discord_feed_delivery as delivery


class _FakeDiscordClient:
    def __init__(self):
        self.calls = []

    async def _request(self, method, path, *, payload=None):
        self.calls.append((method, path, payload))
        return 200, {"id": "message-1"}


class _CI:
    recommended_sell_target = 15800
    expected_profit_after_tax = 2500.0
    risk_level = "low"
    liquidity_score = 0.82
    confidence_score = 0.77
    recommended_buy_max = 12500
    current_executable_buy = 12500


def _row():
    return {
        "source_card_id": 123,
        "name": "Example Player",
        "version": "Rare Gold",
    }


def test_platform_label_is_clear_in_trade_title_and_field():
    token = delivery._platform_ctx.set("pc")
    try:
        embed, _ = delivery._build_opportunity_with_platform(_row(), _CI())
    finally:
        delivery._platform_ctx.reset(token)

    assert embed["title"].startswith("PC TRADE —")
    platform = next(field for field in embed["fields"] if field["name"] == "PLATFORM")
    assert platform["value"] == "PC"


def test_console_label_is_clear_in_result_title_and_field():
    token = delivery._platform_ctx.set("ps")
    try:
        embed = delivery._build_update_with_platform(
            {"name": "Example Player"},
            {"outcome_status": "target_hit", "net_profit_after_tax": 2500},
        )
    finally:
        delivery._platform_ctx.reset(token)

    assert embed["title"].startswith("CONSOLE —")
    assert "TARGET HIT" in embed["title"]
    platform = next(field for field in embed["fields"] if field["name"] == "PLATFORM")
    assert platform["value"] == "Console"


def test_role_ping_allows_only_the_exact_configured_role():
    fake = _FakeDiscordClient()
    client = delivery._RoleMentionClient(fake, "123456789")

    asyncio.run(
        client.post_embed(
            "channel-1",
            {"title": "Trade"},
            nonce="n1",
            enforce_nonce=True,
        )
    )

    payload = fake.calls[0][2]
    assert payload["content"] == "<@&123456789>"
    assert payload["allowed_mentions"] == {"parse": [], "roles": ["123456789"]}


def test_invalid_role_id_cannot_turn_into_everyone_or_user_ping():
    fake = _FakeDiscordClient()
    client = delivery._RoleMentionClient(fake, "@everyone")

    asyncio.run(
        client.post_embed(
            "channel-1",
            {"title": "Trade"},
            nonce="n1",
            enforce_nonce=True,
        )
    )

    payload = fake.calls[0][2]
    assert "content" not in payload
    assert payload["allowed_mentions"] == {"parse": []}
