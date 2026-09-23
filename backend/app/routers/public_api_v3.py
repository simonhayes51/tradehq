"""EAFC.Live Public Data API v3.

FC27-first external API over the live FUT.GG-backed market pipeline.
Unlike v1/v2, which preserve the older fut_players contract, v3 reads
futgg_market_snapshot / futgg_sales_history and keeps game_year explicit
so FC26 history and FC27 live data cannot collide.

All routes are read-only and API-key authenticated. Rate limits and monthly
quotas are enforced centrally by app.auth.api_keys.require_api_key.
"""
from __future__ import annotations

from datetime import date
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.api_keys import require_api_key
from app.db import get_db

router = APIRouter(prefix="/api/public/v3", tags=["public-api-v3"])


def _iso(value):
    return value.isoformat() if value is not None else None


def _player_payload(row: Any) -> dict:
    return {
        "cardId": int(row["source_card_id"]),
        "gameYear": int(row["game_year"]),
        "name": row["display_name"] or row["name"],
        "fullName": row["full_name"] or row["name"],
        "rating": row["rating"],
        "position": row["primary_position"],
        "rarity": row["rarity"],
        "club": row["club"],
        "league": row["league"],
        "nation": row["nation"],
        "tradeable": row["is_tradeable"],
        "imageUrl": row["card_image_stored_url"] or row["card_image_url"] or row["player_image_url"],
        "currentBin": row["current_bin"],
        "priceRange": {
            "low": row["price_range_low"],
            "high": row["price_range_high"],
        },
        "priceCapturedAt": _iso(row["bin_captured_at"]),
        "priceCheckedAt": _iso(row["bin_checked_at"]),
        "latestSale": row["latest_sale_price"],
        "latestSaleAt": _iso(row["latest_sale_at"]),
        "salesMedian": round(row["sales_median"]) if row["sales_median"] is not None else None,
        "salesCount": int(row["sales_count"] or 0),
        "snapshotComputedAt": _iso(row["snapshot_computed_at"]),
    }


@router.get("/players/search")
async def search_players(
    q: str = Query("", max_length=100, description="player name or card id"),
    game_year: int = Query(27, ge=26, le=99),
    limit: int = Query(25, ge=1, le=100),
    key=Depends(require_api_key),
    conn=Depends(get_db),
):
    q = (q or "").strip()
    params: List[Any] = [game_year]
    where = ["game_year = $1", "is_active", "is_tradeable IS DISTINCT FROM FALSE"]
    if q:
        params.append(f"%{q}%")
        n = len(params)
        where.append(
            f"(source_card_id::text LIKE ${n} OR name ILIKE ${n} "
            f"OR display_name ILIKE ${n} OR full_name ILIKE ${n})"
        )
    params.append(limit)
    rows = await conn.fetch(
        f"""
        SELECT source_card_id, game_year, name, display_name, full_name,
               rating, primary_position, rarity, club, league, nation,
               is_tradeable, card_image_stored_url, card_image_url, player_image_url,
               current_bin, price_range_low, price_range_high,
               bin_captured_at, bin_checked_at, latest_sale_price, latest_sale_at,
               sales_median, sales_count, snapshot_computed_at
        FROM futgg_market_snapshot
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE WHEN current_bin IS NOT NULL THEN 0 ELSE 1 END,
            rating DESC NULLS LAST,
            source_card_id
        LIMIT ${len(params)}
        """,
        *params,
    )
    return {"gameYear": game_year, "players": [_player_payload(r) for r in rows]}


@router.get("/players/{card_id}")
async def player_detail(
    card_id: int,
    game_year: int = Query(27, ge=26, le=99),
    key=Depends(require_api_key),
    conn=Depends(get_db),
):
    row = await conn.fetchrow(
        """
        SELECT source_card_id, game_year, name, display_name, full_name,
               rating, primary_position, rarity, club, league, nation,
               is_tradeable, card_image_stored_url, card_image_url, player_image_url,
               current_bin, price_range_low, price_range_high,
               bin_captured_at, bin_checked_at, latest_sale_price, latest_sale_at,
               sales_median, sales_count, snapshot_computed_at
        FROM futgg_market_snapshot
        WHERE game_year = $1 AND source_card_id = $2
        """,
        game_year,
        card_id,
    )
    if not row:
        raise HTTPException(404, "Card not found for that game year")
    return _player_payload(row)


@router.get("/players/{card_id}/price")
async def player_price(
    card_id: int,
    game_year: int = Query(27, ge=26, le=99),
    key=Depends(require_api_key),
    conn=Depends(get_db),
):
    """Small response for bots that only need the current console price."""
    row = await conn.fetchrow(
        """
        SELECT source_card_id, game_year, current_bin, price_range_low,
               price_range_high, bin_captured_at, bin_checked_at,
               latest_sale_price, latest_sale_at
        FROM futgg_market_snapshot
        WHERE game_year = $1 AND source_card_id = $2
        """,
        game_year,
        card_id,
    )
    if not row:
        raise HTTPException(404, "Card not found for that game year")
    return {
        "cardId": int(row["source_card_id"]),
        "gameYear": int(row["game_year"]),
        "platform": "ps5",
        "currentBin": row["current_bin"],
        "priceRange": {"low": row["price_range_low"], "high": row["price_range_high"]},
        "priceCapturedAt": _iso(row["bin_captured_at"]),
        "priceCheckedAt": _iso(row["bin_checked_at"]),
        "latestSale": row["latest_sale_price"],
        "latestSaleAt": _iso(row["latest_sale_at"]),
    }


@router.get("/players/{card_id}/sales")
async def player_sales(
    card_id: int,
    game_year: int = Query(27, ge=26, le=99),
    limit: int = Query(20, ge=1, le=100),
    key=Depends(require_api_key),
    conn=Depends(get_db),
):
    rows = await conn.fetch(
        """
        SELECT sold_price, approximate_sold_at
        FROM futgg_sales_history
        WHERE game_year = $1
          AND source_card_id = $2
          AND platform = 'ps5'
        ORDER BY approximate_sold_at DESC
        LIMIT $3
        """,
        game_year,
        card_id,
        limit,
    )
    return {
        "cardId": card_id,
        "gameYear": game_year,
        "platform": "ps5",
        "sales": [
            {"soldPrice": r["sold_price"], "soldAt": _iso(r["approximate_sold_at"])}
            for r in rows
        ],
    }


@router.get("/usage")
async def usage(key=Depends(require_api_key), conn=Depends(get_db)):
    month_start = date.today().replace(day=1)
    try:
        rows = await conn.fetch(
            """
            SELECT day, requests
            FROM api_key_usage
            WHERE api_key_id = $1 AND day >= $2
            ORDER BY day
            """,
            key["id"],
            month_start,
        )
        daily = [{"day": r["day"].isoformat(), "requests": int(r["requests"])} for r in rows]
    except Exception:
        daily = []
    return {
        "keyPrefix": key.get("key_prefix"),
        "tier": key.get("tier"),
        "usage": key.get("usage"),
        "daily": daily,
    }
