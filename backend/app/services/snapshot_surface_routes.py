"""Install snapshot-backed handlers for the expensive market list routes.

FastAPI builds dependency/query parsing once when the routers are included. We
keep that exact public contract and only replace the final route callable after
app import, so auth, validation, response paths and frontend parameters stay
unchanged while the implementation stops re-running evaluate_card() over the
whole candidate universe inside HTTP requests.

This is intentionally isolated as an incident-safe transition layer. Once the
snapshot-backed path has proven itself in production, these handlers can be
moved into futgg_market.py normally and the installer removed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.services.snapshot_market_surfaces import load_market_surface_pairs

log = logging.getLogger("snapshot_surface_routes")


def _pair_filter(
    pairs,
    *,
    risk: Optional[str] = None,
    min_profit: Optional[float] = None,
    min_roi: Optional[float] = None,
    min_confidence: Optional[float] = None,
    min_liquidity: Optional[float] = None,
    signals: Optional[set] = None,
    require_confirmed_sales: bool = False,
    max_evidence_age_minutes: Optional[int] = None,
):
    out = []
    for row, ci in pairs:
        if signals is not None and ci.signal not in signals:
            continue
        if require_confirmed_sales and (
            ci.valuation_source != "completed_sales" or ci.evidence_quality != "confirmed"
        ):
            continue
        if max_evidence_age_minutes is not None and (
            ci.evidence_age_minutes is None or ci.evidence_age_minutes > max_evidence_age_minutes
        ):
            continue
        if risk is not None and ci.risk_level != risk:
            continue
        if min_profit is not None and (
            ci.expected_profit_after_tax is None or float(ci.expected_profit_after_tax) < min_profit
        ):
            continue
        if min_roi is not None and (ci.expected_roi is None or float(ci.expected_roi) < min_roi):
            continue
        if min_confidence is not None and (
            ci.confidence_score is None or ci.confidence_score < min_confidence
        ):
            continue
        if min_liquidity is not None and (
            ci.liquidity_score is None or ci.liquidity_score < min_liquidity
        ):
            continue
        out.append((row, ci))
    return out


def _restrict_to_rows(pairs, rows):
    allowed = {int(row["source_card_id"]) for row in rows}
    return [(row, ci) for row, ci in pairs if int(row["source_card_id"]) in allowed]


def _filter_meaningful_opportunities(pairs, floor_fn):
    """Keep headline opportunities economically meaningful, not just high-ROI.

    The engine can legitimately produce BUY on a 350-coin card when the
    percentage edge is huge, but a +268-coin result should not occupy the main
    Opportunities feed beside five-figure trades. Reuse the existing calibrated
    price-relative meaningful-profit floor rather than inventing a second set of
    thresholds here.
    """
    return [
        (row, ci)
        for row, ci in pairs
        if ci.expected_profit_after_tax is not None
        and float(ci.expected_profit_after_tax) >= floor_fn(row.get("current_bin"))
    ]


def _quality_opportunity_sort_key(pair):
    """Rank executable opportunities by coin value before percentage optics."""
    row, ci = pair
    return (
        0 if ci.signal == "strong_buy" else 1,
        -(float(ci.expected_profit_after_tax) if ci.expected_profit_after_tax is not None else 0.0),
        -(float(ci.confidence_score) if ci.confidence_score is not None else 0.0),
        -(float(ci.expected_roi) if ci.expected_roi is not None else 0.0),
        int(row["source_card_id"]),
    )


def install_snapshot_surface_routes(application) -> None:
    from app.routers.v2 import futgg_market as market
    from app.routers.v2 import dashboard as dashboard_module
    from app.services.free_picks import resolve_pick_ids
    from app.services.strategy_definitions import classify_watch_candidate, get_strategy, why_this_fits

    async def snapshot_free_pick_candidates(provider):
        pairs = await load_market_surface_pairs(provider)
        scored = _pair_filter(
            pairs,
            signals={"buy", "strong_buy"},
            min_liquidity=market.LIVE_OPPORTUNITY_MIN_LIQUIDITY_SCORE,
            require_confirmed_sales=True,
            max_evidence_age_minutes=market.MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES,
        )
        scored = _filter_meaningful_opportunities(scored, market._meaningful_profit_floor)
        scored.sort(key=_quality_opportunity_sort_key)
        return [market._item_with_intelligence(row, ci) for row, ci in scored]

    # Home's Free-pick resolver imported this symbol directly, so patch both
    # module namespaces. This does not alter the daily persistence/allowance
    # logic, only how the candidate pool is built.
    market.free_pick_candidates = snapshot_free_pick_candidates
    dashboard_module.free_pick_candidates = snapshot_free_pick_candidates

    async def opportunities(**kw):
        provider = kw["provider"]
        viewer = kw["viewer"]
        rating_min, rating_max = market._resolve_rating_bounds(
            kw.get("rating"), kw.get("rating_min"), kw.get("rating_max")
        )
        max_price_age_minutes = (
            kw.get("max_price_age_minutes")
            if kw.get("max_price_age_minutes") is not None
            else kw.get("max_price_age")
        )
        filters = market._filters_from_query(
            None, rating_min, rating_max, kw.get("position"), kw.get("rarity"),
            kw.get("club"), kw.get("league"), kw.get("nation"),
            kw.get("max_price"), kw.get("min_price"), max_price_age_minutes,
        )
        pairs = await load_market_surface_pairs(provider)
        rows = market._apply_filters_in_python([row for row, _ in pairs], filters)
        pairs = _restrict_to_rows(pairs, rows)
        scored = _pair_filter(
            pairs,
            risk=kw.get("risk"),
            min_profit=kw.get("min_profit"),
            min_roi=kw.get("min_roi"),
            min_confidence=kw.get("min_confidence"),
            min_liquidity=market._live_opportunity_min_liquidity(kw.get("min_liquidity")),
            signals={"buy", "strong_buy"},
            require_confirmed_sales=True,
            max_evidence_age_minutes=market.MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES,
        )
        scored = _filter_meaningful_opportunities(scored, market._meaningful_profit_floor)
        scored.sort(key=_quality_opportunity_sort_key)
        page = kw.get("page", 1)
        page_size = kw.get("page_size", market.DEFAULT_PAGE_SIZE)
        total = len(scored)
        window = scored[(page - 1) * page_size: page * page_size]
        items = [market._item_with_intelligence(row, ci) for row, ci in window]

        unlocked: List[int] = []
        if viewer.is_free:
            free_candidates = await snapshot_free_pick_candidates(provider)
            unlocked = await resolve_pick_ids(lambda: free_candidates)
        payload = market.serialise_opportunities(items, viewer, unlocked_ids=unlocked)
        payload.update({"page": page, "page_size": page_size, "total": total})
        if not viewer.has("opportunity_feed"):
            payload["free_picks"] = market.free_pick_meta(
                unlocked=len([i for i in items if not i.get("locked")]) if viewer.is_free else 0,
            )
        return payload

    async def opportunities_preview(**kw):
        provider = kw["provider"]
        pairs = await load_market_surface_pairs(provider)
        scored = _pair_filter(
            pairs,
            signals={"buy", "strong_buy"},
            min_liquidity=market.LIVE_OPPORTUNITY_MIN_LIQUIDITY_SCORE,
            require_confirmed_sales=True,
            max_evidence_age_minutes=market.MAX_OPPORTUNITY_EVIDENCE_AGE_MINUTES,
        )
        scored = _filter_meaningful_opportunities(scored, market._meaningful_profit_floor)
        scored.sort(key=_quality_opportunity_sort_key)
        if not scored:
            return {"item": None}
        row, ci = scored[0]
        return {"item": market._redact_for_public_preview(market._item_with_intelligence(row, ci))}

    async def trade_finder(**kw):
        provider = kw["provider"]
        sort_by = kw.get("sort_by", "best_opportunity")
        if kw.get("sort") is not None:
            sort_by = market._SORT_ALIASES.get(kw["sort"], kw["sort"])
        if sort_by not in market._TRADE_FINDER_SORTS:
            raise HTTPException(400, f"sort_by must be one of {sorted(market._TRADE_FINDER_SORTS)}")

        risk_tolerance = kw.get("risk_tolerance") if kw.get("risk_tolerance") is not None else kw.get("risk")
        rating_min, rating_max = market._resolve_rating_bounds(
            kw.get("rating"), kw.get("rating_min"), kw.get("rating_max")
        )
        max_price_age_minutes = (
            kw.get("max_price_age_minutes")
            if kw.get("max_price_age_minutes") is not None
            else kw.get("max_price_age")
        )
        filters = market._filters_from_query(
            None, rating_min, rating_max, kw.get("position"), kw.get("rarity"),
            None, None, None, kw.get("budget"), None, max_price_age_minutes,
        )
        pairs = await load_market_surface_pairs(provider)
        rows = market._apply_filters_in_python([row for row, _ in pairs], filters)
        pairs = _restrict_to_rows(pairs, rows)
        scored = _pair_filter(
            pairs,
            min_profit=kw.get("min_profit"), min_roi=kw.get("min_roi"),
            min_confidence=kw.get("min_confidence"), min_liquidity=kw.get("min_liquidity"),
            signals={"buy", "strong_buy"} if kw.get("require_executable") else {"buy", "strong_buy", "watch"},
        )

        risk_order = {"low": 0, "medium": 1, "high": 2, "avoid": 3}
        max_risk_rank = risk_order.get(risk_tolerance, 2) if risk_tolerance else None
        if max_risk_rank is not None:
            scored = [(row, ci) for row, ci in scored if risk_order.get(ci.risk_level, 3) <= max_risk_rank]
        if kw.get("meaningful_profit"):
            scored = [
                (row, ci) for row, ci in scored
                if ci.expected_profit_after_tax is not None
                and float(ci.expected_profit_after_tax) >= market._meaningful_profit_floor(row.get("current_bin"))
            ]

        if sort_by == "newest":
            scored.sort(key=lambda pair: (
                -(pair[0].get("price_updated_at") or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                int(pair[0]["source_card_id"]),
            ))
        else:
            scored.sort(key=market._TRADE_FINDER_SORTS[sort_by])

        page = kw.get("page", 1)
        page_size = kw.get("page_size", market.DEFAULT_PAGE_SIZE)
        total = len(scored)
        window = scored[(page - 1) * page_size: page * page_size]
        items = [market._item_with_intelligence(row, ci) for row, ci in window]
        return {"items": items, "page": page, "page_size": page_size, "total": total, "sort_by": sort_by}

    async def strategy_finder(**kw):
        provider = kw["provider"]
        definition = get_strategy(kw["strategy"])
        if definition is None:
            raise HTTPException(
                404,
                f"Unknown strategy '{kw['strategy']}'. Valid: {sorted(market.STRATEGY_DEFINITIONS)}",
            )

        filters = market._filters_from_query(
            None, None, None, None, None, None, None, None,
            definition.budget, None, None,
        )
        pairs = await load_market_surface_pairs(provider)
        rows = market._apply_filters_in_python([row for row, _ in pairs], filters)
        pairs = _restrict_to_rows(pairs, rows)

        risk_order = {"low": 0, "medium": 1, "high": 2, "avoid": 3}
        max_risk_rank = risk_order.get(definition.risk_tolerance, 2) if definition.risk_tolerance else None

        scored = _pair_filter(
            pairs,
            min_profit=definition.min_profit, min_roi=definition.min_roi,
            min_confidence=definition.min_confidence, min_liquidity=definition.min_liquidity,
            signals={"buy", "strong_buy"},
        )
        if max_risk_rank is not None:
            scored = [(row, ci) for row, ci in scored if risk_order.get(ci.risk_level, 3) <= max_risk_rank]
        if definition.meaningful_profit:
            scored = [
                (row, ci) for row, ci in scored
                if ci.expected_profit_after_tax is not None
                and float(ci.expected_profit_after_tax) >= market._meaningful_profit_floor(row.get("current_bin"))
            ]
        scored.sort(key=market._TRADE_FINDER_SORTS[definition.sort_by])

        page = kw.get("page", 1)
        page_size = kw.get("page_size", market.DEFAULT_PAGE_SIZE)
        total = len(scored)
        window = scored[(page - 1) * page_size: page * page_size]
        items = []
        for row, ci in window:
            snipe = market.build_snipe_filter(row, ci)
            item = market._item_with_intelligence(row, ci)
            item["live_entry_state"] = market._live_entry_state(row, ci, snipe)
            item["why_this_fits"] = why_this_fits(definition, row, ci)
            items.append(item)

        watch_scored = _pair_filter(
            pairs,
            min_confidence=definition.min_confidence,
            min_liquidity=definition.min_liquidity,
            signals={"watch"},
        )
        if max_risk_rank is not None:
            watch_scored = [(row, ci) for row, ci in watch_scored if risk_order.get(ci.risk_level, 3) <= max_risk_rank]
        watch_items = []
        for row, ci in watch_scored:
            classification = classify_watch_candidate(row, ci)
            if classification is None:
                continue
            item = market._item_with_intelligence(row, ci)
            item["live_entry_state"] = "watch"
            item.update(classification)
            watch_items.append(item)
        watch_items.sort(key=lambda item: item["above_entry_pct"])
        watch_items = watch_items[:6]

        return {
            "strategy": definition.key,
            "label": definition.label,
            "description": definition.description,
            "items": items,
            "watch_items": watch_items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    replacements = {
        "/api/v2/opportunities": opportunities,
        "/api/v2/opportunities/preview": opportunities_preview,
        "/api/v2/trade-finder": trade_finder,
        "/api/v2/trade-finder/strategy/{strategy}": strategy_finder,
    }
    installed = []
    for route in application.app.routes:
        if not isinstance(route, APIRoute):
            continue
        handler = replacements.get(route.path)
        if handler is None:
            continue
        # FastAPI executes dependant.call. Keep endpoint aligned too for
        # introspection/debug tooling.
        route.endpoint = handler
        route.dependant.call = handler
        installed.append(route.path)

    missing = set(replacements) - set(installed)
    if missing:
        log.warning("snapshot market route installer missed routes: %s", sorted(missing))
    log.info("snapshot-backed market routes installed: %s", sorted(installed))
