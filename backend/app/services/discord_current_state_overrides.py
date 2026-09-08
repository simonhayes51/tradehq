"""Small current-state corrections for legacy Discord helpers.

Completed Proof Ledger outcomes belong to the immutable recommendation/outcome
history. "How many opportunities are open right now" does not. The existing
_free_overview_stats() mixes both concerns in one function, so replacing the
whole function would duplicate sensitive proof logic merely to change one query.

Instead this module wraps only that function's connection and intercepts its
historical live-count query. Every other fetch/fetchval call delegates untouched.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.snapshot_market_surfaces import DEFAULT_MAX_AGE_MINUTES

log = logging.getLogger("discord_current_state_overrides")

_CURRENT_OPEN_COUNT_SQL = """
SELECT COUNT(*)
FROM futgg_current_intelligence c
JOIN futgg_market_snapshot m ON m.source_card_id = c.source_card_id
WHERE c.signal IN ('buy', 'strong_buy')
  AND c.status NOT IN ('expired', 'invalidated')
  AND (c.expires_at IS NULL OR c.expires_at > NOW())
  AND c.evaluated_at >= NOW() - ($1::int * interval '1 minute')
  AND m.is_tradeable IS DISTINCT FROM FALSE
  AND m.current_bin IS NOT NULL
  AND m.current_bin = c.current_bin
"""


class _CurrentOpenCountConn:
    """Delegate everything except the obsolete historical open-count query."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def fetchval(self, query, *args):
        if "futgg_recommendation_snapshots" in str(query):
            return await self._conn.fetchval(
                _CURRENT_OPEN_COUNT_SQL,
                DEFAULT_MAX_AGE_MINUTES,
            )
        return await self._conn.fetchval(query, *args)



def install_discord_current_state_overrides() -> None:
    from app.services import discord_trading_feed as feed

    original_free_overview_stats = feed._free_overview_stats

    async def current_free_overview_stats(conn):
        return await original_free_overview_stats(_CurrentOpenCountConn(conn))

    feed._free_overview_stats = current_free_overview_stats
    log.info("installed Discord current-intelligence open-count override")
