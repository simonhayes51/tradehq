from __future__ import annotations

import os
from typing import Optional

import asyncpg

_GAME_POOL: Optional[asyncpg.Pool] = None
_CARD_POOL: Optional[asyncpg.Pool] = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.execute("SET search_path TO public")


def _pool_sizes() -> tuple[int, int]:
    min_size = int(os.getenv("FC_CARDS_POOL_MIN", os.getenv("POOL_MIN", "1")))
    max_size = int(os.getenv("FC_CARDS_POOL_MAX", os.getenv("POOL_MAX", "10")))
    if min_size < 1:
        min_size = 1
    if max_size < min_size:
        max_size = min_size
    return min_size, max_size


async def _create_pool(dsn: str) -> asyncpg.Pool:
    min_size, max_size = _pool_sizes()
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        init=_init_conn,
    )


def _require_database_url() -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Missing DATABASE_URL")
    return dsn


async def get_game_pool() -> asyncpg.Pool:
    global _GAME_POOL
    if _GAME_POOL is None:
        dsn = os.getenv("FC_CARDS_DATABASE_URL") or _require_database_url()
        _GAME_POOL = await _create_pool(dsn)
    return _GAME_POOL


async def get_card_pool() -> asyncpg.Pool:
    global _CARD_POOL
    if _CARD_POOL is None:
        dsn = os.getenv("FC_CARDS_CARD_DATABASE_URL") or _require_database_url()
        _CARD_POOL = await _create_pool(dsn)
    return _CARD_POOL

