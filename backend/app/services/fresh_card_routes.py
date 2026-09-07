"""Install canonical fresh evaluation on the single-card player detail route.

The public route contract stays owned by futgg_market.py. This installer only
replaces its final callable so the route uses futgg_card_evaluation's one-card
I/O path, matching Companion, Discord and held-position refresh without
rewriting the large market router during the migration.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.services.futgg_card_evaluation import evaluate_fresh_card

log = logging.getLogger("fresh_card_routes")


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

        payload = {
            "source": "futgg",
            **market._item_with_intelligence(row, ci),
            "player": market._serialize_player(row),
            "intelligence": market._serialize_intelligence(ci),
        }
        if not viewer.has("opportunity_feed"):
            unlocked = await unlocked_pick_ids() if viewer.is_free else []
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
