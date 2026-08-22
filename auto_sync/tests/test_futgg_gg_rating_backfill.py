import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import backfill_futgg_gg_rating as backfill


def test_populated_page_without_ratings_does_not_checkpoint(monkeypatch):
    page_url = "https://www.fut.gg/players/1-player/26-1001/"
    checkpointed = False

    class FakeConn:
        async def fetchval(self, query, *args):
            if "pg_try_advisory_lock" in query:
                return True
            return None

        async def execute(self, query, *args):
            nonlocal checkpointed
            if "INSERT INTO futgg_gg_rating_backfill_state" in query:
                checkpointed = True

        async def close(self):
            pass

    class FakePage:
        pass

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def close(self):
            pass

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return FakeContext()

        async def close(self):
            pass

    class FakeChromium:
        async def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        async def start(self):
            return self

        async def stop(self):
            pass

    async def fake_paginate(page, template, max_pages, label, **kwargs):
        await kwargs["page_callback"](
            1,
            {page_url: backfill.sync.ListingCardMetadata()},
        )
        return {}

    conn = FakeConn()
    monkeypatch.setattr(backfill, "connect_with_retry", lambda dsn: _async_value(conn))
    monkeypatch.setattr(backfill, "async_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(backfill.sync, "ensure_schema", lambda conn: _async_value(None))
    monkeypatch.setattr(backfill.sync, "_paginate_listing", fake_paginate)

    with pytest.raises(RuntimeError, match="refusing to advance"):
        asyncio.run(backfill.run_backfill(game_year=26, start_page=1, max_pages=1))

    assert checkpointed is False


async def _async_value(value):
    return value
