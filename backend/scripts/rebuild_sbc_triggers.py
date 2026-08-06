#!/usr/bin/env python
# scripts/rebuild_sbc_triggers.py
#
# One-shot rebuild of SBC-driven priority_triggers under the revised
# relevance model (see app/services/sbc_card_matching.py). The normal
# recompute loop already converges to the same end state on its own -
# a stale trigger the new logic no longer re-confirms grace-expires
# within PRIORITY_TRIGGER_CONFIRMATION_GRACE_HOURS (default 72h,
# migration 046) - this script just does it immediately instead of
# waiting out the grace period, for the one-time cutover from the old
# ungated matching logic.
#
# What it does, in order:
#   1. Runs sync_requirement_filters() + sync_sbc_resource_demand() +
#      sync_sbc_identity_triggers() once - exactly what a normal
#      recompute pass already does. Every genuinely-still-valid identity
#      trigger gets re-confirmed (last_confirmed_at bumped); resource
#      demand rows get populated/refreshed.
#   2. Finds every currently-active trigger_type='sbc_match' row that
#      step 1 did NOT just re-confirm (last_confirmed_at older than this
#      run's own start time) - these can only be rows the OLD, ungated
#      matching logic created, since the new logic would have
#      re-confirmed anything still legitimately matched.
#   3. In --apply mode, sets expires_at = now() on exactly those rows -
#      preserves the row and its full audit trail (created_at,
#      last_confirmed_at, activated_at) rather than deleting it;
#      "expired" is a normal, already-understood trigger state, not a
#      destructive one. In preview mode (the default), only reports what
#      WOULD be expired - no writes.
#
# Never touches trigger_type != 'sbc_match' rows (manual_override,
# sales_spike, price_spike, new_release, user_position) - untouched by
# construction, since the query is scoped to trigger_type='sbc_match'.
#
# Idempotent: running it twice in a row is a no-op the second time -
# step 1's re-confirmation is already a no-op for unchanged data, and
# step 2 finds nothing left to expire.
#
# "Preview" (the default, no --apply) still RUNS step 1 - the same
# parse/resource-demand/identity-trigger writes a normal recompute pass
# already makes continuously in production, not something this script
# introduces - it only skips step 3 (expiring stale rows). This is a
# deliberate choice, not an oversight: step 1's writes are idempotent
# and already happening regardless of this script; the one genuinely
# cutover-specific action is the expiry, which --apply gates.
#
# Usage:
#   python -m scripts.rebuild_sbc_triggers              # preview (step 3 skipped)
#   python -m scripts.rebuild_sbc_triggers --apply       # actually expire stale rows
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import asyncpg
from dotenv import load_dotenv

from app.services.sbc_card_matching import (
    sync_requirement_filters, sync_sbc_identity_triggers, sync_sbc_resource_demand,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rebuild_sbc_triggers")

_STALE_SBC_MATCH_QUERY = """
    SELECT id, card_id, source_event_id, last_confirmed_at
    FROM priority_triggers
    WHERE trigger_type = 'sbc_match'
      AND (expires_at IS NULL OR expires_at > now())
      AND last_confirmed_at < $1
    ORDER BY card_id
"""

_EXPIRE_SQL = """
    UPDATE priority_triggers
    SET expires_at = now()
    WHERE id = ANY($1::bigint[])
"""


async def rebuild(pool: asyncpg.Pool, *, apply: bool) -> dict:
    run_started_at = datetime.now(timezone.utc)

    changed_challenges = await sync_requirement_filters(pool)
    resource_rows = await sync_sbc_resource_demand(pool)
    identity_created = await sync_sbc_identity_triggers(pool)

    async with pool.acquire() as conn:
        stale_rows = await conn.fetch(_STALE_SBC_MATCH_QUERY, run_started_at)
        stale_ids = [int(r["id"]) for r in stale_rows]
        stale_card_count = len({int(r["card_id"]) for r in stale_rows})

        if apply and stale_ids:
            await conn.execute(_EXPIRE_SQL, stale_ids)

        remaining_active = await conn.fetchval(
            "SELECT count(*) FROM priority_triggers WHERE trigger_type = 'sbc_match' AND (expires_at IS NULL OR expires_at > now())"
        )

    return {
        "apply": apply,
        "changed_challenges_reparsed": changed_challenges,
        "resource_demand_rows_upserted": resource_rows,
        "identity_triggers_created_or_confirmed": identity_created,
        "stale_sbc_match_rows_found": len(stale_ids),
        "stale_sbc_match_cards_found": stale_card_count,
        "stale_sbc_match_rows_expired": len(stale_ids) if apply else 0,
        "active_sbc_match_rows_after": int(remaining_active) if apply else None,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rebuild SBC priority_triggers under the revised relevance model")
    p.add_argument("--apply", action="store_true", help="actually expire stale rows (default: preview only, no writes)")
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL is required")
        return 1

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
    try:
        summary = await rebuild(pool, apply=args.apply)
    finally:
        await pool.close()

    mode = (
        "APPLY (stale sbc_match rows expired)" if args.apply
        else "PREVIEW - reparses/re-syncs normally (the same writes a regular recompute "
             "pass already makes), but does NOT expire any stale rows; pass --apply to do that"
    )
    logger.info("Mode: %s", mode)
    for key, value in summary.items():
        logger.info("  %s: %s", key, value)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
