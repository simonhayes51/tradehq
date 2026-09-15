# app/routers/v2/release_price_rises.py
"""
Per-card price movement from EARLY RELEASE to FULL RELEASE for a game
cycle (default: FC26 - see app/services/game_cycle.py).

Different question from market_event_study.py: that script reports a
COHORT median move around individual content-release events (promos,
TOTW). This router answers "which INDIVIDUAL cards rose the most between
two fixed calendar points" - the web-app/early-access launch and full
release - which is what a "who raised the most at launch" leaderboard
needs. It also breaks the same move down by position and by the
PACE_HIGH/PACE_LOW pace-tier split, purely descriptive (see WORDING
DISCIPLINE below).

Data source: fc26_historical_daily_prices (chart-reconstructed, see
auto_sync/research_fc26_historical_loader.py) joined to futgg_players for
display metadata. Baseline/target prices are the closest observed price
on or after the requested date, within a small forward-search window -
the series is daily and not uniformly dense (see that loader's own
docstring), so an exact-date match would silently drop most cards.

WORDING DISCIPLINE - same caution as market_event_study.py
------------------------------------------------------------
This measures ASSOCIATION over a calendar window, never causation. Many
things move a card's price between web-app launch and full release
besides "release timing" itself (packs opened, early-access-only
promotions, ordinary market settling) - report this as "X rose N%
between early and full release", never "full release caused X to rise".

Ungated (no entitlement check) - read-only descriptive data, same
precedent as futgg_market.py / investments.py's ungated surfaces.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_core_pool
from app.services.game_cycle import GAME_CYCLE

router = APIRouter(tags=["v2-release-price-rises"])
log = logging.getLogger("release_price_rises_router")

# How many days forward of the requested baseline/target date we'll
# search for the closest actually-observed price. The series is daily
# but sparse per card (see loader docstring) - beyond this window a
# "nearest price" stops meaningfully representing that date.
MATCH_WINDOW_DAYS = 5

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Pace-tier split used for the "did pace players rise more" trend
# breakdown. Matches the informal community threshold for a "fast" card,
# not a value derived from this dataset - kept as a named constant so the
# cutoff is visible and easy to move rather than a magic number in a query.
PACE_HIGH_THRESHOLD = 85

# FC26's confirmed launch calendar. Unlike GAME_CYCLE (app/services/
# game_cycle.py), which is deliberately env-var-driven because it also has
# to describe FC27's calendar before EA has announced it, FC26 already
# happened - its dates are a historical fact, not a per-deployment config
# value - so this endpoint hardcodes them rather than requiring every
# deployment to set EAFC_WEB_APP_LAUNCH_DATE/EAFC_FULL_RELEASE_DATE (and
# EAFC_GAME_YEAR=26) just to ask about a season that's already over.
# GAME_CYCLE's own env vars still win when they happen to be configured for
# game_year 26, and ?early_date=/?full_date= always wins over both.
FC26_WEB_APP_LAUNCH_DATE = date(2026, 9, 19)
FC26_FULL_RELEASE_DATE = date(2026, 9, 26)


def _resolve_date(explicit: Optional[str], fallback: Optional[date], label: str) -> date:
    if explicit:
        try:
            return date.fromisoformat(explicit)
        except ValueError:
            raise HTTPException(400, f"Invalid {label} date: {explicit!r} (expected YYYY-MM-DD)")
    if fallback is None:
        raise HTTPException(
            400,
            f"No {label} date configured (EAFC_WEB_APP_LAUNCH_DATE/EAFC_FULL_RELEASE_DATE) "
            f"and none supplied - pass ?{label}_date=YYYY-MM-DD explicitly.",
        )
    return fallback


async def _nearest_prices(conn, card_ids: List[int], target: date, window_days: int) -> Dict[int, Dict[str, Any]]:
    """For each card, the price on the observed_date closest to `target`
    within [target, target + window_days] (searching forward only - a
    price from BEFORE the requested date is not "the price at that
    point", it's stale data standing in for a gap)."""
    if not card_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (card_id) card_id, observed_date, price
        FROM fc26_historical_daily_prices
        WHERE card_id = ANY($1::bigint[])
          AND observed_date >= $2
          AND observed_date <= $3
        ORDER BY card_id, observed_date ASC
        """,
        card_ids, target, target + timedelta(days=window_days),
    )
    return {r["card_id"]: {"date": r["observed_date"], "price": r["price"]} for r in rows}


@router.get("/release-price-rises")
async def list_release_price_rises(
    game_year: int = Query(26, ge=20, le=99),
    early_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to the configured web-app-launch date"),
    full_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to the configured full-release date"),
    match_window_days: int = Query(MATCH_WINDOW_DAYS, ge=0, le=14),
    position: Optional[str] = Query(None, description="Filter to one primary_position, e.g. ST"),
    min_early_price: Optional[int] = Query(None, ge=0),
    sort_by: str = Query("pct_change", pattern="^(pct_change|abs_change|early_price|full_price|rating)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    """Ranked list of individual FC{game_year} cards by price change from
    early release to full release, plus a position/pace-tier breakdown of
    the same move for the "who/what archetype rose the most" question."""
    game_cycle_matches = GAME_CYCLE.game_year == game_year
    early_fallback = GAME_CYCLE.web_app_launch if game_cycle_matches else (FC26_WEB_APP_LAUNCH_DATE if game_year == 26 else None)
    full_fallback = GAME_CYCLE.full_release if game_cycle_matches else (FC26_FULL_RELEASE_DATE if game_year == 26 else None)
    early = _resolve_date(early_date, early_fallback, "early")
    full = _resolve_date(full_date, full_fallback, "full")
    if full <= early:
        raise HTTPException(400, f"full_date ({full}) must be after early_date ({early})")

    async with pool.acquire() as conn:
        card_where = ["p.game_year = $1"]
        args: List[Any] = [game_year]
        if position:
            args.append(position.upper())
            card_where.append(f"p.primary_position = ${len(args)}")

        players = await conn.fetch(
            f"""
            SELECT p.source_card_id, p.name, p.display_name, p.rating,
                   p.primary_position, p.rarity, p.pace,
                   p.player_image_url, p.card_design_image_url
            FROM futgg_players p
            WHERE {' AND '.join(card_where)}
              AND EXISTS (SELECT 1 FROM fc26_historical_daily_prices h WHERE h.card_id = p.source_card_id)
            """,
            *args,
        )
        card_ids = [r["source_card_id"] for r in players]

        early_prices = await _nearest_prices(conn, card_ids, early, match_window_days)
        full_prices = await _nearest_prices(conn, card_ids, full, match_window_days)

    rows: List[Dict[str, Any]] = []
    for p in players:
        cid = p["source_card_id"]
        e, f = early_prices.get(cid), full_prices.get(cid)
        if not e or not f:
            continue
        if min_early_price is not None and e["price"] < min_early_price:
            continue
        abs_change = f["price"] - e["price"]
        pct_change = (abs_change / e["price"] * 100.0) if e["price"] else None
        rows.append({
            "card_id": cid,
            "name": p["display_name"] or p["name"],
            "rating": p["rating"],
            "position": p["primary_position"],
            "rarity": p["rarity"],
            "pace": p["pace"],
            "image_url": p["card_design_image_url"] or p["player_image_url"],
            "early_price": e["price"],
            "early_price_date": e["date"].isoformat(),
            "full_price": f["price"],
            "full_price_date": f["date"].isoformat(),
            "abs_change": abs_change,
            "pct_change": round(pct_change, 1) if pct_change is not None else None,
        })

    reverse = order == "desc"
    rows.sort(key=lambda r: (r[sort_by] is None, r[sort_by] or 0), reverse=reverse)

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]

    trends = _build_trends(rows)

    return {
        "game_year": game_year,
        "early_date": early.isoformat(),
        "full_date": full.isoformat(),
        "match_window_days": match_window_days,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": page_rows,
        "trends": trends,
    }


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _build_trends(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Descriptive breakdowns only - see module docstring's WORDING
    DISCIPLINE note. Median (not mean) pct_change per group, so a handful
    of extreme risers don't stand in for the whole group's typical move."""
    by_position: Dict[str, List[float]] = {}
    for r in rows:
        if r["pct_change"] is None or not r["position"]:
            continue
        by_position.setdefault(r["position"], []).append(r["pct_change"])
    position_trend = [
        {"position": pos, "median_pct_change": round(_median(vals), 1), "card_count": len(vals)}
        for pos, vals in sorted(by_position.items(), key=lambda kv: _median(kv[1]) or 0, reverse=True)
    ]

    pace_groups: Dict[str, List[float]] = {"high_pace": [], "low_pace": []}
    for r in rows:
        if r["pct_change"] is None or r["pace"] is None:
            continue
        key = "high_pace" if r["pace"] >= PACE_HIGH_THRESHOLD else "low_pace"
        pace_groups[key].append(r["pct_change"])
    pace_trend = [
        {
            "group": key,
            "label": f"Pace {PACE_HIGH_THRESHOLD}+" if key == "high_pace" else f"Pace under {PACE_HIGH_THRESHOLD}",
            "median_pct_change": round(_median(vals), 1) if vals else None,
            "card_count": len(vals),
        }
        for key, vals in pace_groups.items()
    ]

    return {"by_position": position_trend, "by_pace_tier": pace_trend}
