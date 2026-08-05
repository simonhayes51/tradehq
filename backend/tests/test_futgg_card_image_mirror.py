# tests/test_futgg_card_image_mirror.py
#
# Tests for app/services/futgg_card_image_mirror.py against a real
# throwaway Postgres database (same convention as
# test_priority_production_safety.py) - the claim/status-lifecycle SQL is
# the part worth testing against a real connection; the actual HTTP
# fetch and R2 upload are monkeypatched so these tests need no network
# access and no live S3-compatible endpoint.
#
# Needs a real Postgres reachable at TEST_ADMIN_DATABASE_URL (default
# postgresql://postgres:postgres@localhost:5432/postgres) - skipped
# outright if unreachable.
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from app.services import futgg_card_image_mirror as mod
from app.services.object_storage import ObjectStorageError

ADMIN_DSN = os.getenv(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)

_SCHEMA_SQL = """
CREATE TABLE futgg_players (
    source_card_id BIGINT PRIMARY KEY,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    card_image_url TEXT,
    card_image_stored_url TEXT,
    card_image_stored_key TEXT,
    card_image_stored_hash TEXT,
    card_image_stored_at TIMESTAMPTZ,
    card_image_stored_status TEXT,
    card_image_stored_error TEXT
);
"""


def _run(coro):
    return asyncio.run(coro)


async def _postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


async def _make_db() -> str:
    name = f"tradehq_mirror_test_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    return name


async def _drop_db(name: str) -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


def _dsn_for(name: str) -> str:
    base = ADMIN_DSN.rsplit("/", 1)[0]
    return f"{base}/{name}"


@pytest.fixture
def dsn():
    if not _run(_postgres_reachable()):
        pytest.skip(f"no Postgres reachable at {ADMIN_DSN}")
    name = _run(_make_db())
    d = _dsn_for(name)

    async def _bootstrap():
        conn = await asyncpg.connect(d)
        try:
            await conn.execute(_SCHEMA_SQL)
        finally:
            await conn.close()

    _run(_bootstrap())
    try:
        yield d
    finally:
        _run(_drop_db(name))


async def _pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=3)


class _FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "image/webp"):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def get(self, url):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_fetch(monkeypatch, status=200, body=b"fake-webp-bytes"):
    monkeypatch.setattr(
        mod.aiohttp, "ClientSession",
        lambda *a, **kw: _FakeSession(_FakeResponse(status, body)),
    )


def test_ensure_mirrored_card_image_uploads_and_records_stored_columns(dsn, monkeypatch):
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO futgg_players (source_card_id, card_image_url) VALUES (1, 'https://cdn/img.webp')"
                )
            _patch_fetch(monkeypatch)
            monkeypatch.setattr(mod, "upload_bytes", lambda key, data, ct: asyncio.sleep(0, result=f"https://r2.example.com/{key}"))

            result = await mod.ensure_mirrored_card_image(pool, 1)
            assert result["mirrored"] is True
            assert result["url"].startswith("https://r2.example.com/fc26/futgg-card-images/1/")

            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM futgg_players WHERE source_card_id = 1")
            assert row["card_image_stored_status"] == "ready"
            assert row["card_image_stored_url"] == result["url"]
            assert row["card_image_stored_hash"] is not None
            assert row["card_image_stored_error"] is None
        finally:
            await pool.close()

    _run(_t())


def test_ensure_mirrored_card_image_is_a_no_op_when_already_ready(dsn, monkeypatch):
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO futgg_players (source_card_id, card_image_url, card_image_stored_url, card_image_stored_status)
                    VALUES (1, 'https://cdn/img.webp', 'https://r2.example.com/already-there.webp', 'ready')
                    """
                )
            fetch_called = False

            def _boom(*a, **kw):
                nonlocal fetch_called
                fetch_called = True
                raise AssertionError("should not fetch when already ready")

            monkeypatch.setattr(mod.aiohttp, "ClientSession", _boom)

            result = await mod.ensure_mirrored_card_image(pool, 1)
            assert result == {"mirrored": False, "url": "https://r2.example.com/already-there.webp"}
            assert fetch_called is False
        finally:
            await pool.close()

    _run(_t())


def test_ensure_mirrored_card_image_marks_error_on_fetch_failure(dsn, monkeypatch):
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO futgg_players (source_card_id, card_image_url) VALUES (1, 'https://cdn/img.webp')"
                )
            _patch_fetch(monkeypatch, status=404, body=b"")

            result = await mod.ensure_mirrored_card_image(pool, 1)
            assert result["mirrored"] is False
            assert "error" in result

            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT card_image_stored_status, card_image_stored_error FROM futgg_players WHERE source_card_id = 1")
            assert row["card_image_stored_status"] == "error"
            assert row["card_image_stored_error"] is not None
        finally:
            await pool.close()

    _run(_t())


def test_ensure_mirrored_card_image_raises_for_card_with_no_image_url(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                await conn.execute("INSERT INTO futgg_players (source_card_id) VALUES (1)")
            with pytest.raises(mod.CardImageNotFoundError):
                await mod.ensure_mirrored_card_image(pool, 1)
        finally:
            await pool.close()

    _run(_t())


def test_mirror_pending_batch_claims_only_eligible_cards(dsn, monkeypatch):
    async def _t():
        pool = await _pool(dsn)
        try:
            async with pool.acquire() as conn:
                # 1: pending (never attempted)
                await conn.execute("INSERT INTO futgg_players (source_card_id, card_image_url) VALUES (1, 'https://cdn/1.webp')")
                # 2: already ready - must not be re-claimed
                await conn.execute(
                    "INSERT INTO futgg_players (source_card_id, card_image_url, card_image_stored_status) VALUES (2, 'https://cdn/2.webp', 'ready')"
                )
                # 3: no card_image_url at all - never eligible
                await conn.execute("INSERT INTO futgg_players (source_card_id) VALUES (3)")
                # 4: inactive - excluded
                await conn.execute(
                    "INSERT INTO futgg_players (source_card_id, card_image_url, is_active) VALUES (4, 'https://cdn/4.webp', FALSE)"
                )
                # 5: errored previously - eligible for retry
                await conn.execute(
                    "INSERT INTO futgg_players (source_card_id, card_image_url, card_image_stored_status) VALUES (5, 'https://cdn/5.webp', 'error')"
                )

            _patch_fetch(monkeypatch)
            monkeypatch.setattr(mod, "upload_bytes", lambda key, data, ct: asyncio.sleep(0, result=f"https://r2.example.com/{key}"))

            claimed = await mod.mirror_pending_batch(pool, batch_size=10)
            assert claimed == 2  # cards 1 and 5 only

            async with pool.acquire() as conn:
                statuses = {
                    r["source_card_id"]: r["card_image_stored_status"]
                    for r in await conn.fetch("SELECT source_card_id, card_image_stored_status FROM futgg_players ORDER BY source_card_id")
                }
            assert statuses[1] == "ready"
            assert statuses[2] == "ready"  # untouched
            assert statuses[3] is None     # untouched, no image_url
            assert statuses[4] is None     # untouched, inactive
            assert statuses[5] == "ready"  # retried and succeeded
        finally:
            await pool.close()

    _run(_t())


def test_mirror_pending_batch_returns_zero_when_nothing_pending(dsn):
    async def _t():
        pool = await _pool(dsn)
        try:
            claimed = await mod.mirror_pending_batch(pool, batch_size=10)
            assert claimed == 0
        finally:
            await pool.close()

    _run(_t())
