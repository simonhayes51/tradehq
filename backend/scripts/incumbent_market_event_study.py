# backend/scripts/incumbent_market_event_study.py
"""
The corrected event study: does the EXISTING/INCUMBENT base-gold market
(fc26_historical_daily_prices' ~2600 Gold Common/Rare cards) move around
FC26 content releases (content_release_events)? Promo cards are the
calendar, never the subject - see app/services/incumbent_market_study.py's
module docstring for the full framing and no-lookahead rules.

    python -m scripts.incumbent_market_event_study --game-year 26

Writes a machine-readable JSON report to --out (default
incumbent_market_event_study_fc26.json in the current directory) and
prints a readable research report to stdout. Read-only - never writes to
the database, never touches the Opportunity or Investment Engine.

WORDING DISCIPLINE: association, never causation. RESOLUTION LIMIT: FC26
prices are daily, so day 0 may reflect either side of an evening drop.
Both apply throughout - see market_event_study.py's docstring for the
full statement, not repeated here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("incumbent_market_event_study")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-year", type=int, default=26)
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--out", default="incumbent_market_event_study_fc26.json")
    parser.add_argument(
        "--min-median-price", type=float, default=0.0,
        help="Exclude incumbent cards whose OWN median stored price is below this. "
             "See scripts/diagnose_price_quantization.py: cards under ~1,000-5,000 coins are "
             "heavily chart-pixel-quantized (up to 79%% of adjacent days show zero change), which "
             "swamps the pooled median with a resolution artifact rather than real flatness. "
             "Default 0 = no filter (matches the original run). Try 5000 for a clean-instrument re-run.",
    )
    return parser.parse_args()


async def _load(conn, game_year: int):
    events = await conn.fetch(
        """
        SELECT id, event_type, event_key, title, rarity, squad, released_at,
               card_count, event_scale
        FROM content_release_events
        WHERE game_year = $1
        ORDER BY released_at
        """,
        game_year,
    )
    price_rows = await conn.fetch(
        """
        SELECT h.card_id, h.observed_date, h.price
        FROM fc26_historical_daily_prices h
        JOIN futgg_players p ON p.source_card_id = h.card_id
        WHERE p.game_year = $1 AND p.rating >= 75 AND p.rarity IN ('Common', 'Rare')
        """,
        game_year,
    )
    card_ids = sorted({r["card_id"] for r in price_rows})
    static_rows = await conn.fetch(
        """
        SELECT source_card_id, rating, rarity, primary_position, pace
        FROM futgg_players
        WHERE source_card_id = ANY($1::bigint[])
        """,
        card_ids,
    )
    return events, price_rows, static_rows


async def main() -> int:
    args = _parse_args()
    dsn = args.dsn or os.getenv("PLAYER_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        log.error("need --dsn or PLAYER_DATABASE_URL/DATABASE_URL")
        return 1

    import asyncpg
    from app.services.investment_engine.config import INVESTMENT_ENGINE_CONFIG
    from app.services.investment_engine.cohorts import POSITION_GROUPS
    from app.services.incumbent_market_study import (
        BASELINE_LEGS, ENTRY_EXIT_LEGS, aggregate_event_level, assign_asof_launch_cohorts,
        assign_price_band, flag_independent_events, legs_for_event, pace_band as pace_band_of,
        per_label_legs, rating_band as rating_band_of, season_regime, weekday_bucket,
    )

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            events, price_rows, static_rows = await _load(conn, args.game_year)
    finally:
        await pool.close()

    if not events:
        log.warning("No content_release_events for game_year=%d", args.game_year)
        return 0
    if not price_rows:
        log.warning("No incumbent base-gold price rows for game_year=%d", args.game_year)
        return 0

    series_by_card: Dict[int, Dict[date, float]] = defaultdict(dict)
    for r in price_rows:
        series_by_card[r["card_id"]][r["observed_date"]] = float(r["price"])
    all_card_ids = list(series_by_card.keys())
    log.info("incumbent universe (unfiltered): %d base-gold cards, %d priced rows", len(all_card_ids), len(price_rows))

    if args.min_median_price > 0:
        before = len(all_card_ids)
        all_card_ids = [
            cid for cid in all_card_ids
            if statistics.median(series_by_card[cid].values()) >= args.min_median_price
        ]
        card_id_set = set(all_card_ids)
        series_by_card = defaultdict(dict, {cid: s for cid, s in series_by_card.items() if cid in card_id_set})
        log.info(
            "--min-median-price=%.0f applied: %d/%d cards kept (excludes the chart-pixel-quantized "
            "cheap tail - see scripts/diagnose_price_quantization.py)",
            args.min_median_price, len(all_card_ids), before,
        )
    log.info("incumbent universe used: %d base-gold cards", len(all_card_ids))

    rating_of: Dict[int, Optional[int]] = {}
    rarity_of: Dict[int, Optional[str]] = {}
    position_of: Dict[int, Optional[str]] = {}
    pace_of: Dict[int, Optional[int]] = {}
    for r in static_rows:
        cid = r["source_card_id"]
        rating_of[cid] = r["rating"]
        rarity_of[cid] = r["rarity"]
        position_of[cid] = r["primary_position"]
        pace_of[cid] = r["pace"]

    rating_band_by_card = {cid: rating_band_of(rating_of.get(cid)) for cid in all_card_ids}
    pace_band_by_card = {cid: pace_band_of(pace_of.get(cid)) for cid in all_card_ids}
    position_group_by_card = {
        cid: POSITION_GROUPS.get(position_of.get(cid) or "", "OTHER") for cid in all_card_ids
    }
    rarity_by_card = {cid: (rarity_of.get(cid) or "unknown") for cid in all_card_ids}

    independence = flag_independent_events([{"id": e["id"], "released_at": e["released_at"]} for e in events])

    per_event: List[dict] = []
    for ev in events:
        release_day = ev["released_at"].date()
        overall = legs_for_event(BASELINE_LEGS, series_by_card, all_card_ids, release_day)
        entry_exit = legs_for_event(ENTRY_EXIT_LEGS, series_by_card, all_card_ids, release_day)

        baseline_day = release_day + timedelta(days=-1)
        launch_labels = assign_asof_launch_cohorts(
            all_card_ids, series_by_card, position_of, baseline_day, INVESTMENT_ENGINE_CONFIG,
        )
        price_band_labels = assign_price_band(all_card_ids, series_by_card, baseline_day)

        by_launch_cohort = per_label_legs(BASELINE_LEGS, series_by_card, launch_labels, release_day)
        by_price_band = per_label_legs(BASELINE_LEGS, series_by_card, price_band_labels, release_day)
        by_rarity = per_label_legs(BASELINE_LEGS, series_by_card, rarity_by_card, release_day)
        by_rating_band = per_label_legs(BASELINE_LEGS, series_by_card, rating_band_by_card, release_day)
        by_pace_band = per_label_legs(BASELINE_LEGS, series_by_card, pace_band_by_card, release_day)
        by_position_group = per_label_legs(BASELINE_LEGS, series_by_card, position_group_by_card, release_day)

        per_event.append({
            "id": ev["id"], "title": ev["title"], "event_type": ev["event_type"],
            "released_at": ev["released_at"], "release_day": release_day,
            "card_count": ev["card_count"], "event_scale": ev["event_scale"],
            "weekday": weekday_bucket(release_day), "regime": season_regime(release_day),
            "independent": independence[ev["id"]],
            "overall": overall, "entry_exit": entry_exit,
            "by_launch_cohort": by_launch_cohort, "by_price_band": by_price_band,
            "by_rarity": by_rarity, "by_rating_band": by_rating_band,
            "by_pace_band": by_pace_band, "by_position_group": by_position_group,
        })

    _print_report(per_event, args.game_year, len(all_card_ids))
    _write_json(per_event, args)
    return 0


def _filtered(per_event: List[dict], **preds) -> List[dict]:
    out = []
    for ev in per_event:
        ok = True
        for key, val in preds.items():
            if callable(val):
                ok = ok and val(ev[key])
            else:
                ok = ok and ev[key] == val
        if ok:
            out.append(ev)
    return out


def _fmt_agg_row(label: str, agg: dict, legs) -> str:
    cells = []
    for leg_name, _, _ in legs:
        row = agg.get(leg_name, {})
        mom = row.get("median_of_medians")
        n = row.get("n_events", 0)
        cells.append("n/a" if mom is None else f"{mom:+.1f}%n{n}")
    return f"  {label:<38} " + " ".join(f"{c:>10}" for c in cells)


def _print_report(per_event: List[dict], game_year: int, universe_size: int) -> None:
    from app.services.incumbent_market_study import BASELINE_LEGS, aggregate_event_level

    print()
    print("=" * 100)
    print(f"FC{game_year} INCUMBENT-GOLD-MARKET EVENT STUDY (research only, association not causation)")
    print(f"incumbent universe: {universe_size} base Gold Common/Rare cards | {len(per_event)} calendar events")
    print("=" * 100)

    header = " ".join(f"{leg:>10}" for leg, _, _ in BASELINE_LEGS)
    print(f"\n{'':30} {header}")

    print("\n-- OVERALL, per event (all incumbent cards pooled per event, card-level-first) --")
    for ev in per_event:
        cells = []
        for leg_name, _, _ in BASELINE_LEGS:
            s = ev["overall"][leg_name]
            cells.append(f"{s.median:+9.1f}%" if s.is_reportable else f"{'n/a':>10}")
        indep = "" if ev["independent"] else " [clustered]"
        print(f"  {ev['release_day']} {ev['title']:<26.26}[{ev['weekday'][:3]}]{indep:<12} " + " ".join(cells))

    weekdays = sorted({ev["weekday"] for ev in per_event})
    print("\n-- HYPOTHESIS A/F: sell-off before content, by release weekday (event-level aggregate) --")
    for wd in weekdays:
        subset = _filtered(per_event, weekday=wd)
        agg = aggregate_event_level([ev["overall"] for ev in subset], BASELINE_LEGS)
        print(_fmt_agg_row(f"{wd} (n_events={len(subset)})", agg, BASELINE_LEGS))

    print("\n-- Same, INDEPENDENT EVENTS ONLY (clustered multi-stage promos excluded, hypothesis H) --")
    for wd in weekdays:
        subset = _filtered(per_event, weekday=wd, independent=True)
        if not subset:
            continue
        agg = aggregate_event_level([ev["overall"] for ev in subset], BASELINE_LEGS)
        print(_fmt_agg_row(f"{wd} independent (n={len(subset)})", agg, BASELINE_LEGS))

    print("\n-- HYPOTHESIS D: premium/meta vs ordinary/starter/fodder, ALL events, AS-OF launch cohort --")
    for cohort in ("premium_meta", "mid_tier_meta", "desirable_starter", "ordinary", "likely_fodder"):
        cohort_legs = [ev["by_launch_cohort"].get(cohort, {}) for ev in per_event if cohort in ev["by_launch_cohort"]]
        if not cohort_legs:
            print(f"  {cohort:<28} no event had a card in this AS-OF cohort")
            continue
        agg = aggregate_event_level(cohort_legs, BASELINE_LEGS)
        print(_fmt_agg_row(cohort, agg, BASELINE_LEGS))

    print("\n-- Gold Rare vs Gold Common, ALL events --")
    for rarity in ("Rare", "Common"):
        rarity_legs = [ev["by_rarity"].get(rarity, {}) for ev in per_event if rarity in ev["by_rarity"]]
        if rarity_legs:
            agg = aggregate_event_level(rarity_legs, BASELINE_LEGS)
            print(_fmt_agg_row(rarity, agg, BASELINE_LEGS))

    print("\n-- Price band (AS-OF T-1 quintile), ALL events --")
    for band in ("Q1_cheapest", "Q2", "Q3", "Q4", "Q5_priciest"):
        band_legs = [ev["by_price_band"].get(band, {}) for ev in per_event if band in ev["by_price_band"]]
        if band_legs:
            agg = aggregate_event_level(band_legs, BASELINE_LEGS)
            print(_fmt_agg_row(band, agg, BASELINE_LEGS))

    print("\n-- Rating band (static), ALL events --")
    for band in ("75-77", "78-80", "81-83", "84-86", "87+"):
        band_legs = [ev["by_rating_band"].get(band, {}) for ev in per_event if band in ev["by_rating_band"]]
        if band_legs:
            agg = aggregate_event_level(band_legs, BASELINE_LEGS)
            print(_fmt_agg_row(band, agg, BASELINE_LEGS))

    print("\n-- Pace band (static), ALL events --")
    for band in ("<60", "60-69", "70-79", "80-89", "90+"):
        band_legs = [ev["by_pace_band"].get(band, {}) for ev in per_event if band in ev["by_pace_band"]]
        if band_legs:
            agg = aggregate_event_level(band_legs, BASELINE_LEGS)
            print(_fmt_agg_row(band, agg, BASELINE_LEGS))

    print("\n-- Position group (static), ALL events --")
    groups = sorted({g for ev in per_event for g in ev["by_position_group"].keys()})
    for grp in groups:
        grp_legs = [ev["by_position_group"].get(grp, {}) for ev in per_event if grp in ev["by_position_group"]]
        if grp_legs:
            agg = aggregate_event_level(grp_legs, BASELINE_LEGS)
            print(_fmt_agg_row(grp, agg, BASELINE_LEGS))

    print("\n-- HYPOTHESIS E: event size (event_scale), Friday events only --")
    friday_events = [ev for ev in per_event if ev["weekday"] == "Friday"]
    for scale in ("mini", "isolated", "broad"):
        subset = [ev for ev in friday_events if ev["event_scale"] == scale]
        if subset:
            agg = aggregate_event_level([ev["overall"] for ev in subset], BASELINE_LEGS)
            print(_fmt_agg_row(f"{scale} (n_events={len(subset)})", agg, BASELINE_LEGS))

    print("\n-- HYPOTHESIS G: season/regime, Friday events only --")
    for regime_label in dict.fromkeys(ev["regime"] for ev in per_event):
        subset = [ev for ev in friday_events if ev["regime"] == regime_label]
        if subset:
            agg = aggregate_event_level([ev["overall"] for ev in subset], BASELINE_LEGS)
            print(_fmt_agg_row(f"{regime_label} (n={len(subset)})", agg, BASELINE_LEGS))

    print("\n-- HYPOTHESIS C: entry timing matrix, Friday events, tax-adjusted median-of-event-medians --")
    from app.services.incumbent_market_study import ENTRY_EXIT_LEGS
    header2 = " ".join(f"{leg:>12}" for leg, _, _ in ENTRY_EXIT_LEGS)
    print(f"  {'':28} {header2}")
    agg = aggregate_event_level([ev["entry_exit"] for ev in friday_events], ENTRY_EXIT_LEGS)
    cells = []
    for leg_name, _, _ in ENTRY_EXIT_LEGS:
        row = agg.get(leg_name, {})
        mom = row.get("median_of_medians")
        cells.append(f"{(mom - 5.0):+11.1f}%" if mom is not None else f"{'n/a':>12}")
    print(f"  {'Friday events, after tax':<28} " + " ".join(cells))

    print()
    print("Read every number above as association, never causation - see script docstring.")
    print("Legend: '+X.X%nN' = median-of-N-event-medians. 'n/a' = fewer than 2 reportable events.")


def _write_json(per_event: List[dict], args: argparse.Namespace) -> None:
    def _default(obj):
        if hasattr(obj, "as_dict"):
            return obj.as_dict()
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        raise TypeError(str(type(obj)))

    payload = {"game_year": args.game_year, "events": per_event}
    with open(args.out, "w") as f:
        json.dump(payload, f, default=_default, indent=2)
    log.info("wrote machine-readable results to %s", args.out)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
