"""Install canonical fresh evaluation on the single-card player detail route.

The public route contract stays owned by futgg_market.py. This installer only
replaces its final callable so the route uses futgg_card_evaluation's one-card
I/O path, matching Companion, Discord and held-position refresh without
rewriting the large market router during the migration.
"""
from __future__ import annotations

import logging
from typing import Iterable

from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.services.futgg_card_evaluation import evaluate_fresh_card
from app.services.futgg_exit_quality import exit_quality_for_card

log = logging.getLogger("fresh_card_routes")


def exit_quality_is_unlocked(viewer, card_id: int, unlocked_ids: Iterable[int] = ()) -> bool:
    """Whether this viewer may receive empirical exit evidence.

    Exit evidence is part of the executable trade plan, not ordinary player
    search/fair-value data. Elite/full opportunity-feed access gets it for every
    card. A Free viewer gets it only for one of the server-persisted daily picks
    they already legitimately unlocked. Anonymous and other locked viewers never
    receive the field at all, so there is nothing for the frontend to merely hide.
    """
    if viewer.has("opportunity_feed"):
        return True
    if not getattr(viewer, "is_free", False):
        return False
    unlocked = {int(value) for value in unlocked_ids}
    return int(card_id) in unlocked


def install_fresh_player_detail_route(application) -> None:
    from app.routers.v2 import futgg_market as market
    from app.services.free_picks import unlocked_pick_ids

    async def player_detail(**kw):
        card_id = int(kw["card_id"])
        provider = kw["provider"]
        viewer = kw["viewer"]

        result = await evaluate_fresh_card(
            provider,
            card_id,
            requested_by="player_detail",
        )
        if result is None:
            raise HTTPException(404, "Card not found in the FUT.GG-backed market layer")

        row = result.row
        ci = result.intelligence

        if market._is_stale_for_tier(ci.price_age_minutes, row.get("price_tier")):
            try:
                await provider.bump_price_priority(card_id)
            except Exception:
                log.warning("bump_price_priority failed for card_id=%s", card_id, exc_info=True)

        unlocked = []
        if not viewer.has("opportunity_feed") and viewer.is_free:
            unlocked = await unlocked_pick_ids()
        exit_quality_unlocked = exit_quality_is_unlocked(viewer, card_id, unlocked)

        payload = {
            "source": "futgg",
            **market._item_with_intelligence(row, ci),
            "player": market._serialize_player(row),
            "intelligence": market._serialize_intelligence(ci),
        }

        if exit_quality_unlocked:
            try:
                payload["exit_quality"] = await exit_quality_for_card(
                    provider._pool,
                    row,
                    ci,
                )
            except Exception:
                # Historical evidence is an enrichment, never a reason to turn
                # a valid live card page into a 500.
                log.warning("exit quality lookup failed for card_id=%s", card_id, exc_info=True)
                payload["exit_quality"] = {
                    "status": "unavailable",
                    "message": "Historical exit evidence is temporarily unavailable.",
                }

        if not viewer.has("opportunity_feed"):
            payload = market.serialise_card_detail(payload, viewer, unlocked_ids=unlocked)
        return payload

    installed = False
    for route in application.app.routes:
        if not isinstance(route, APIRoute) or route.path != "/api/v2/players/{card_id}":
            continue
        route.endpoint = player_detail
        route.dependant.call = player_detail
        installed = True
        break

    if installed:
        log.info("canonical fresh player-detail route installed")
    else:
        log.warning("canonical fresh player-detail installer missed /api/v2/players/{card_id}")
