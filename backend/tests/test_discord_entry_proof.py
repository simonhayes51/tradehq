from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import asyncio


class _Conn:
    def __init__(self, pending, history):
        self.pending = pending
        self.history = history
        self.record_calls = 0

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetch(self, query, *args):
        if "FROM discord_server_posts p" in query:
            return self.pending
        if "FROM futgg_bin_history" in query:
            return self.history
        return []

    async def fetchval(self, query, *args):
        if "INSERT INTO discord_server_posts" in query:
            self.record_calls += 1
            return self.record_calls
        return None

    async def execute(self, *args, **kwargs):
        return None


def test_published_live_price_proves_entry_even_without_new_history_row(monkeypatch):
    """Regression: Selma Bacha posted live at 14k with MAX BUY 17.75k.

    futgg_bin_history did not write another <=17.75k row during the 15-minute
    entry window, so the old worker falsely posted ENTRY NOT REACHED. The
    final pre-send observation stored in the post is itself proof of entry.
    """
    monkeypatch.setenv("DISCORD_TRADING_FEED_ENABLED", "1")

    import app.services.discord_trading_feed as feed

    now = datetime(2026, 9, 24, 20, 44, tzinfo=timezone.utc)
    sent_at = now - timedelta(minutes=30)
    monkeypatch.setattr(feed, "_now", lambda: now)

    payload = {
        "source_card_id": 123,
        "name": "Selma Bacha",
        "buy_price": 17_750,
        "observed_buy_price": 14_000,
        "sell_target": 20_250,
        "entry_expires_at": (sent_at + timedelta(minutes=15)).isoformat(),
        "price_checked_at": (sent_at - timedelta(minutes=1)).isoformat(),
    }
    conn = _Conn(
        pending=[{
            "post_id": 1,
            "payload": payload,
            "dedup_key": "selma",
            "sent_at": sent_at,
        }],
        # No second affordable observation inside the 15-minute entry window.
        # A later target observation should still grade from the proven
        # published entry rather than being rejected as NO_ENTRY.
        history=[{
            "lowest_bin": 20_500,
            "captured_at": sent_at + timedelta(minutes=20),
        }],
    )
    client = MagicMock()
    client.post_embed = AsyncMock(return_value=(200, {"id": "result"}))
    server = feed.ServerRecord(
        id=1,
        guild_id="g",
        name="Test",
        access_level="paid_partner",
        feed_channel_id="feed",
        proof_channel_id=None,
        status="active",
        expires_at=None,
    )

    updated = asyncio.run(feed.publish_opportunity_updates(conn, server, client))

    assert updated == 1
    embed = client.post_embed.call_args.args[1]
    assert "TARGET HIT" in embed["title"]
    assert "ENTRY NOT REACHED" not in embed["title"]
