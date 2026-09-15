# app/routers/v2/release_price_rises.py
"""
Per-card price movement from EARLY RELEASE to FULL RELEASE for a game
cycle (default: FC26 - see app/services/game_cycle.py).

Different question from market_event_study.py: that script reports a
COHORT median move around individual content-release events (promos,
TOTW). This router answers "which INDIVIDUAL cards rose the most between
two fixed calendar points" - the web-app/early-access launch and full
release - which is what a launch-movers leaderboard needs. It also turns
that historical movement into descriptive launch-playbook signals across
all useful data we have: position, rating band, price band, pace tier,
rarity, nation and league.

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

DATA QUALITY - chart-reconstructed prices can be wrong
--------------------------------------------------------
fc26_historical_daily_prices is reconstructed by pixel-measuring FUT.GG's
price-history SVG chart (see the loader's own docstring), not read from a
raw API - a bad Y-axis calibration produces a price that is off by a
large factor, which then shows up here as a spectacular but fake % move.
fc26_historical_extraction_runs records that same script's own confidence
in each card's extraction (status, calibration_max_pct_err, tick_warnings
- HIGH_CALIBRATION_ERR_PCT = 20.0 is that loader's own flag threshold).
By default this endpoint EXCLUDES any card whose extraction run didn't
succeed cleanly (status != 'success', calibration error above that same
20% threshold, or any tick_warnings) - pass ?include_uncertain=true to
see them anyway, e.g. for auditing the loader itself.
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

MATCH_WINDOW_DAYS = 5
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
PACE_HIGH_THRESHOLD = 85
MIN_TREND_GROUP_SIZE = 3
TOP_TREND_GROUPS = 8
TOP_KEY_SIGNALS = 5
HIGH_CALIBRATION_ERR_PCT = 20.0

FC26_WEB_APP_LAUNCH_DATE = date(2025, 9, 17)
FC26_FULL_RELEASE_DATE = date(2025, 9, 24)

PRICE_BANDS = [
    {"key": "sub_1k", "label": "Under 1k", "min": 0, "max": 999},
    {"key": "1k_5k", "label": "1k-5k", "min": 1000, "max": 4999},
    {"key": "5k_25k", "label": "5k-25k", "min": 5000, "max": 24999},
    {"key": "25k_plus", "label": "25k+", "min": 25000, "max": None},
]

RATING_BANDS = [
    {"key": "bronze_silver", "label": "Under 75", "min": 0, "max": 74},
    {"key": "75_79", "label": "75-79", "min": 75, "max": 79},
    {"key": "80_84", "label": "80-84", "min": 80, "max": 84},
    {"key": "85_plus", "label": "85+", "min": 85, "max": None},
]

PACE_BANDS = [
    {"key": "under_70", "label": "Pace under 70", "min": 0, "max": 69},
    {"key": "70_79", "label": "Pace 70-79", "min": 70, "max": 79},
    {"key": "80_84", "label": "Pace 80-84", "min": 80, "max": 84},
    {"key": "85_plus", "label": "Pace 85+", "min": 85, "max": None},
]


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
    within [target, target + window_days] (searching forward only)."""
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
    q: Optional[str] = Query(None, min_length=1, max_length=80, description="Case-insensitive player-name search"),
    min_rating: Optional[int] = Query(None, ge=0, le=99),
    min_early_price: Optional[int] = Query(None, ge=0, description="Drop cards priced below this at the early date - filters out cheap fodder whose tiny base inflates % change"),
    max_early_price: Optional[int] = Query(None, ge=0),
    include_uncertain: bool = Query(False, description="Include cards whose chart extraction was flagged low-confidence"),
    sort_by: str = Query("pct_change", pattern="^(pct_change|abs_change|net_profit_after_tax|net_roi_after_tax|early_price|full_price|rating)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    pool=Depends(get_core_pool),
) -> Dict[str, Any]:
    """Rank individual launch movers and describe the launch patterns.

    q/min_rating are server-side deliberately: filtering a client-side top
    100 makes a "90+" or searched result look exhaustive when it is only a
    narrowed slice of a different leaderboard.
    """
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
        if min_rating is not None:
            args.append(min_rating)
            card_where.append(f"p.rating >= ${len(args)}")
        if q:
            args.append(f"%{q.strip().lower()}%")
            card_where.append(
                f"(LOWER(COALESCE(p.display_name, p.name, '')) LIKE ${len(args)} "
                f"OR LOWER(COALESCE(p.name, '')) LIKE ${len(args)})"
            )
        if not include_uncertain:
            card_where.append(
                "(r.card_id IS NULL OR (r.status = 'success' "
                f"AND (r.calibration_max_pct_err IS NULL OR r.calibration_max_pct_err <= {HIGH_CALIBRATION_ERR_PCT}) "
                "AND cardinality(r.tick_warnings) = 0))"
            )

        players = await conn.fetch(
            f"""
            SELECT p.source_card_id, p.name, p.display_name, p.rating,
                   p.primary_position, p.rarity, p.pace,
                   p.nation, p.nation_image_url, p.league, p.league_image_url,
                   p.player_image_url, p.card_design_image_url,
                   p.card_image_url, p.card_image_stored_url
            FROM futgg_players p
            LEFT JOIN fc26_historical_extraction_runs r ON r.card_id = p.source_card_id
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
        if max_early_price is not None and e["price"] > max_early_price:
            continue
        abs_change = f["price"] - e["price"]
        pct_change = (abs_change / e["price"] * 100.0) if e["price"] else None
        net_profit = (float(f["price"]) * 0.95) - float(e["price"])
        net_roi = (net_profit / float(e["price"]) * 100.0) if e["price"] else None
        rows.append({
            "card_id": cid,
            "name": p["display_name"] or p["name"],
            "rating": p["rating"],
            "rating_band": _rating_band(p["rating"]),
            "position": p["primary_position"],
            "rarity": p["rarity"],
            "pace": p["pace"],
            "pace_band": _pace_band(p["pace"]),
            "nation": p["nation"],
            "nation_image_url": p["nation_image_url"],
            "league": p["league"],
            "league_image_url": p["league_image_url"],
            "image_url": p["card_image_stored_url"] or p["card_image_url"] or p["card_design_image_url"] or p["player_image_url"],
            "early_price": e["price"],
            "early_price_date": e["date"].isoformat(),
            "full_price": f["price"],
            "full_price_date": f["date"].isoformat(),
            "abs_change": abs_change,
            "pct_change": round(pct_change, 1) if pct_change is not None else None,
            "net_profit_after_tax": round(net_profit),
            "net_roi_after_tax": round(net_roi, 1) if net_roi is not None else None,
            "price_band": _price_band(e["price"]),
        })

    rows.sort(key=lambda r: _sort_key(r, sort_by, order))

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    trends = _build_trends(rows)

    return {
        "game_year": game_year,
        "early_date": early.isoformat(),
        "full_date": full.isoformat(),
        "match_window_days": match_window_days,
        "include_uncertain": include_uncertain,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": page_rows,
        "trends": trends,
        "playbook": _build_playbook(rows, trends),
    }


def _sort_key(row: Dict[str, Any], field: str, order: str) -> tuple:
    value = row.get(field)
    # Missing values always sort last, regardless of asc/desc.
    if value is None:
        return (1, 0)
    direction = -1 if order == "desc" else 1
    return (0, direction * value)


def _band_for_value(value: Optional[int], bands: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    for band in bands:
        if value >= band["min"] and (band["max"] is None or value <= band["max"]):
            return {"key": band["key"], "label": band["label"]}
    return None


def _price_band(price: int) -> Dict[str, Any]:
    return _band_for_value(price, PRICE_BANDS) or {"key": "unknown", "label": "Unknown"}


def _rating_band(rating: Optional[int]) -> Optional[Dict[str, Any]]:
    return _band_for_value(rating, RATING_BANDS)


def _pace_band(pace: Optional[int]) -> Optional[Dict[str, Any]]:
    return _band_for_value(pace, PACE_BANDS)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _group_by(rows: List[Dict[str, Any]], key_field: str, image_field: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = r.get(key_field)
        if isinstance(key, dict):
            key = key.get("label")
        if r["pct_change"] is None or not key:
            continue
        g = groups.setdefault(key, {"values": [], "profits": [], "roi": [], "image_url": None})
        g["values"].append(r["pct_change"])
        if r.get("net_profit_after_tax") is not None:
            g["profits"].append(r["net_profit_after_tax"])
        if r.get("net_roi_after_tax") is not None:
            g["roi"].append(r["net_roi_after_tax"])
        if image_field and not g["image_url"] and r.get(image_field):
            g["image_url"] = r[image_field]
    return groups


def _top_groups(groups: Dict[str, Dict[str, Any]], label_field: str, limit: int = TOP_TREND_GROUPS) -> List[Dict[str, Any]]:
    eligible = {k: g for k, g in groups.items() if len(g["values"]) >= MIN_TREND_GROUP_SIZE}
    ranked = sorted(eligible.items(), key=lambda kv: _median(kv[1]["values"]) or 0, reverse=True)
    return [
        {
            label_field: key,
            "median_pct_change": round(_median(g["values"]), 1),
            "median_profit_after_tax": round(_median(g["profits"])) if g["profits"] else None,
            "median_roi_after_tax": round(_median(g["roi"]), 1) if g["roi"] else None,
            "card_count": len(g["values"]),
            "image_url": g["image_url"],
        }
        for key, g in ranked[:limit]
    ]


def _signal_from_group(dimension: str, label: str, group: Dict[str, Any], overall_pct: float, overall_profit: Optional[float]) -> Dict[str, Any]:
    median_pct = group.get("median_pct_change")
    median_profit = group.get("median_profit_after_tax")
    pct_lift = round((median_pct or 0) - overall_pct, 1)
    profit_lift = round(median_profit - overall_profit) if median_profit is not None and overall_profit is not None else None
    return {
        "dimension": dimension,
        "label": label,
        "median_pct_change": median_pct,
        "median_profit_after_tax": median_profit,
        "median_roi_after_tax": group.get("median_roi_after_tax"),
        "card_count": group.get("card_count", 0),
        "lift_vs_all_pct_points": pct_lift,
        "profit_lift_vs_all": profit_lift,
    }


def _key_signals(rows: List[Dict[str, Any]], grouped: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    pct_values = [r["pct_change"] for r in rows if r.get("pct_change") is not None]
    profit_values = [r["net_profit_after_tax"] for r in rows if r.get("net_profit_after_tax") is not None]
    overall_pct = _median(pct_values) or 0
    overall_profit = _median(profit_values)

    dimensions = [
        ("Position", "by_position", "position"),
        ("Price band", "by_price_band", "price_band"),
        ("Rating band", "by_rating_band", "rating_band"),
        ("Pace band", "by_pace_band", "pace_band"),
        ("Rarity", "by_rarity", "rarity"),
        ("League", "by_league", "league"),
        ("Nation", "by_nation", "nation"),
    ]
    signals: List[Dict[str, Any]] = []
    for dimension, trend_key, label_key in dimensions:
        for group in grouped.get(trend_key, []):
            if group.get("card_count", 0) < MIN_TREND_GROUP_SIZE:
                continue
            signals.append(_signal_from_group(dimension, group[label_key], group, overall_pct, overall_profit))

    signals.sort(
        key=lambda s: (
            s["lift_vs_all_pct_points"],
            s.get("median_profit_after_tax") if s.get("median_profit_after_tax") is not None else -10**12,
            s["card_count"],
        ),
        reverse=True,
    )
    return signals[:TOP_KEY_SIGNALS]


def _build_trends(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_position: Dict[str, List[float]] = {}
    by_position_profit: Dict[str, List[float]] = {}
    by_position_roi: Dict[str, List[float]] = {}
    for r in rows:
        if r["pct_change"] is None or not r["position"]:
            continue
        by_position.setdefault(r["position"], []).append(r["pct_change"])
        if r.get("net_profit_after_tax") is not None:
            by_position_profit.setdefault(r["position"], []).append(r["net_profit_after_tax"])
        if r.get("net_roi_after_tax") is not None:
            by_position_roi.setdefault(r["position"], []).append(r["net_roi_after_tax"])
    position_trend = [
        {
            "position": pos,
            "median_pct_change": round(_median(vals), 1),
            "median_profit_after_tax": round(_median(by_position_profit.get(pos, []))) if by_position_profit.get(pos) else None,
            "median_roi_after_tax": round(_median(by_position_roi.get(pos, [])), 1) if by_position_roi.get(pos) else None,
            "card_count": len(vals),
        }
        for pos, vals in sorted(by_position.items(), key=lambda kv: _median(kv[1]) or 0, reverse=True)
    ]

    price_trend = _top_groups(_group_by(rows, "price_band"), "price_band", limit=len(PRICE_BANDS))
    band_order = {band["label"]: i for i, band in enumerate(PRICE_BANDS)}
    price_trend.sort(key=lambda r: band_order.get(r["price_band"], 99))

    rating_trend = _top_groups(_group_by(rows, "rating_band"), "rating_band", limit=len(RATING_BANDS))
    rating_order = {band["label"]: i for i, band in enumerate(RATING_BANDS)}
    rating_trend.sort(key=lambda r: rating_order.get(r["rating_band"], 99))

    pace_trend = _top_groups(_group_by(rows, "pace_band"), "pace_band", limit=len(PACE_BANDS))
    pace_order = {band["label"]: i for i, band in enumerate(PACE_BANDS)}
    pace_trend.sort(key=lambda r: pace_order.get(r["pace_band"], 99))

    grouped = {
        "by_position": position_trend,
        "by_price_band": price_trend,
        "by_rating_band": rating_trend,
        "by_pace_band": pace_trend,
        "by_nation": _top_groups(_group_by(rows, "nation", "nation_image_url"), "nation"),
        "by_league": _top_groups(_group_by(rows, "league", "league_image_url"), "league"),
        "by_rarity": _top_groups(_group_by(rows, "rarity"), "rarity"),
    }
    grouped["key_signals"] = _key_signals(rows, grouped)
    # Keep the old two-bucket shape for older clients while the UI moves to the
    # ranked, data-led key_signals array.
    grouped["by_pace_tier"] = [
        {
            "group": "high_pace",
            "label": f"Pace {PACE_HIGH_THRESHOLD}+",
            "median_pct_change": next((r["median_pct_change"] for r in pace_trend if r["pace_band"] == f"Pace {PACE_HIGH_THRESHOLD}+"), None),
            "median_profit_after_tax": next((r["median_profit_after_tax"] for r in pace_trend if r["pace_band"] == f"Pace {PACE_HIGH_THRESHOLD}+"), None),
            "card_count": next((r["card_count"] for r in pace_trend if r["pace_band"] == f"Pace {PACE_HIGH_THRESHOLD}+"), 0),
        },
        {
            "group": "low_pace",
            "label": f"Pace under {PACE_HIGH_THRESHOLD}",
            "median_pct_change": None,
            "median_profit_after_tax": None,
            "card_count": sum(r["card_count"] for r in pace_trend if r["pace_band"] != f"Pace {PACE_HIGH_THRESHOLD}+"),
        },
    ]
    return grouped


def _top_trend(rows: List[Dict[str, Any]], label_key: str) -> Optional[Dict[str, Any]]:
    return rows[0] if rows else None


def _build_playbook(rows: List[Dict[str, Any]], trends: Dict[str, Any]) -> Dict[str, Any]:
    if not rows:
        return {
            "headline": "No launch pattern yet",
            "summary": "Widen the date range or lower the filters to build a launch read.",
            "cards": [],
        }

    top_card = max(rows, key=lambda r: r.get("pct_change") or 0)
    top_profit = max(rows, key=lambda r: r.get("net_profit_after_tax") or 0)
    key_signals = trends.get("key_signals", [])
    best_position = _top_trend(trends.get("by_position", []), "position")
    best_rarity = _top_trend(trends.get("by_rarity", []), "rarity")
    price_bands = [r for r in trends.get("by_price_band", []) if r.get("card_count", 0) >= MIN_TREND_GROUP_SIZE]
    best_band = max(price_bands, key=lambda r: r.get("median_pct_change") or 0) if price_bands else None

    cards = [
        {
            "label": "Biggest riser",
            "value": f"+{top_card['pct_change']}%" if (top_card.get("pct_change") or 0) >= 0 else f"{top_card['pct_change']}%",
            "copy": f"{top_card['name']} moved from {top_card['early_price']:,} to {top_card['full_price']:,} coins.",
            "card_id": top_card["card_id"],
        },
        {
            "label": "Best coin return",
            "value": f"+{top_profit['net_profit_after_tax']:,}",
            "copy": f"After EA tax, {top_profit['name']} produced the biggest coin gain in this view.",
            "card_id": top_profit["card_id"],
        },
    ]
    for signal in key_signals[:3]:
        cards.append({
            "label": signal["dimension"],
            "value": signal["label"],
            "copy": (
                f"{signal['label']} beat the overall median by "
                f"{signal['lift_vs_all_pct_points']} points across {signal['card_count']} cards."
            ),
        })
    if not key_signals and best_position:
        cards.append({
            "label": "Position angle",
            "value": best_position["position"],
            "copy": f"{best_position['position']} had a {best_position['median_pct_change']}% median rise across {best_position['card_count']} cards.",
        })
    if not key_signals and best_band:
        cards.append({
            "label": "Budget band",
            "value": best_band["price_band"],
            "copy": f"This band had a {best_band['median_pct_change']}% median rise across {best_band['card_count']} cards.",
        })
    if not key_signals and best_rarity:
        cards.append({
            "label": "Card type",
            "value": best_rarity["rarity"],
            "copy": f"{best_rarity['rarity']} cards led the rarity breakdown on median rise.",
        })

    profile = " + ".join(signal["label"] for signal in key_signals[:3]) or "the strongest historical risers"

    return {
        "headline": f"Best FC27 watch profile: {profile}",
        "summary": "The signal is ranked from the actual FC26 data by median rise versus the overall market. Treat it as a shortlist, then verify current FC27 prices before buying.",
        "cards": cards[:6],
    }
