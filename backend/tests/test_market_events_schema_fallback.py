from __future__ import annotations

import asyncio

import asyncpg

from app.services import market_events


def run(coro):
    return asyncio.run(coro)


class _Conn:
    def __init__(self):
        self.calls = 0

    async def fetch(self, query, *args):
        self.calls += 1
        if self.calls == 1:
            raise asyncpg.exceptions.UndefinedColumnError("legacy card-art column missing")
        return [
            {
                "id": 1,
                "kind": "sbc",
                "source": "test",
                "external_id": "abc",
                "title": "Example SBC",
                "description": None,
                "starts_at": None,
                "ends_at": None,
                "fingerprint": "fp",
                "first_seen_at": None,
                "updated_at": None,
                "category": None,
                "total_cost_coins": None,
                "repeatable": None,
                "reward_card_name": None,
                "reward_card_rating": None,
                "reward_card_version": None,
                "reward_card_image_url": None,
                "reward_card_bg_image": None,
                "reward_card_cutout_image": None,
                "reward_card_cutout_type": None,
                "reward_card_card_name": None,
            }
        ]


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_event_list_falls_back_when_optional_enrichment_schema_is_missing():
    conn = _Conn()
    events = run(market_events.get_events(_Pool(conn), limit=30, offset=0))
    assert conn.calls == 2
    assert events[0]["title"] == "Example SBC"
    assert events[0]["reward_card_name"] is None
