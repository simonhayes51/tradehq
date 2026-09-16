"""Small current-state corrections for legacy Discord helpers.

Completed Proof Ledger outcomes belong to the immutable recommendation/outcome
history. Live/open state and "what genuinely became a BUY since the last digest"
do not.

Two surgical adapters keep the original Discord functions and their surrounding
policy intact:
- Free overview: intercept only its historical open-count fetchval.
- Official digest: intercept only its historical candidate fetch and replace it
  with genuine intelligence transitions joined to still-valid current state.

Every other query, publication dedup rule, cooldown, batch minimum, embed build,
send and finalisation step delegates to the existing implementation untouched.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.snapshot_market_surfaces import DEFAULT_MAX_AGE_MINUTES

log = logging.getLogger("discord_current_state_overrides")

_CURRENT_OPEN_COUNT_SQL = """
SELECT COUNT(*)
FROM futgg_current_intelligence c
JOIN futgg_market_snapshot m ON m.source_card_id = c.source_card_id AND m.game_year = c.game_year
WHERE c.signal IN ('buy', 'strong_buy')
  AND c.status NOT IN ('expired', 'invalidated')
  AND (c.expires_at IS NULL OR c.expires_at > NOW())
  AND c.evaluated_at >= NOW() - ($1::int * interval '1 minute')
  AND m.is_tradeable IS DISTINCT FROM FALSE
  AND m.current_bin IS NOT NULL
  AND m.current_bin = c.current_bin
"""

_CURRENT_DIGEST_CANDIDATES_SQL = """
WITH last_digest AS (
    SELECT MAX(sent_at) AS sent_at
    FROM discord_publications
    WHERE kind='opportunity_digest' AND status='sent'
), first_qualifying AS (
    SELECT DISTINCT ON (transition.source_card_id)
           transition.id,
           transition.source_card_id,
           current.current_executable_buy,
           current.risk_level,
           current.signal,
           current.confidence_score,
           current.liquidity_score
    FROM futgg_intelligence_transitions transition
    JOIN futgg_current_intelligence current
      ON current.source_card_id = transition.source_card_id
     AND current.game_year = transition.game_year
    JOIN futgg_market_snapshot market
      ON market.source_card_id = transition.source_card_id
     AND market.game_year = transition.game_year
    CROSS JOIN last_digest
    WHERE transition.new_signal IN ('buy', 'strong_buy')
      AND (
          transition.previous_signal IS NULL
          OR transition.previous_signal NOT IN ('buy', 'strong_buy')
      )
      AND transition.changed_at > GREATEST(
          COALESCE(
              last_digest.sent_at,
              NOW() - ($1::integer * INTERVAL '1 minute')
          ),
          NOW() - INTERVAL '12 hours'
      )
      AND current.signal IN ('buy', 'strong_buy')
      AND current.status NOT IN ('expired', 'invalidated')
      AND (current.expires_at IS NULL OR current.expires_at > NOW())
      AND current.evaluated_at >= NOW() - ($3::integer * INTERVAL '1 minute')
      AND market.is_tradeable IS DISTINCT FROM FALSE
      AND market.current_bin IS NOT NULL
      AND market.current_bin = current.current_bin
    ORDER BY transition.source_card_id, transition.changed_at
)
SELECT first_qualifying.*
FROM first_qualifying
WHERE NOT EXISTS (
    SELECT 1
    FROM discord_publications published
    WHERE published.kind='opportunity_digest'
      AND published.payload @> jsonb_build_object(
          'source_card_ids', jsonb_build_array(first_qualifying.source_card_id::text)
      )
      AND published.sent_at > NOW() - ($2::integer * INTERVAL '1 hour')
)
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


class _CurrentDigestConn:
    """Delegate everything except publish_opportunity_digest's candidate read."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def fetch(self, query, *args):
        if "FROM futgg_recommendation_snapshots candidate" in str(query):
            if len(args) != 2:
                raise ValueError("unexpected opportunity-digest query arguments")
            return await self._conn.fetch(
                _CURRENT_DIGEST_CANDIDATES_SQL,
                args[0],
                args[1],
                DEFAULT_MAX_AGE_MINUTES,
            )
        return await self._conn.fetch(query, *args)


class _WrappedAcquire:
    def __init__(self, acquire_context):
        self._acquire_context = acquire_context

    async def __aenter__(self):
        conn = await self._acquire_context.__aenter__()
        return _CurrentDigestConn(conn)

    async def __aexit__(self, exc_type, exc, tb):
        return await self._acquire_context.__aexit__(exc_type, exc, tb)


class _CurrentDigestPool:
    """Pool facade so the existing publisher receives wrapped connections."""

    def __init__(self, pool):
        self._pool = pool

    def acquire(self):
        return _WrappedAcquire(self._pool.acquire())


def install_discord_current_state_overrides() -> None:
    from app.services import discord_integration as integration
    from app.services import discord_trading_feed as feed

    original_free_overview_stats = feed._free_overview_stats
    original_opportunity_digest = integration.publish_opportunity_digest

    async def current_free_overview_stats(conn):
        return await original_free_overview_stats(_CurrentOpenCountConn(conn))

    async def current_opportunity_digest(pool, client=None):
        return await original_opportunity_digest(_CurrentDigestPool(pool), client)

    feed._free_overview_stats = current_free_overview_stats
    integration.publish_opportunity_digest = current_opportunity_digest
    log.info("installed Discord current-intelligence live-state overrides")
