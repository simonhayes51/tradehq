"""
Phase 8D -- final research-only validation pass before FC27 Investment
Engine design. Corrects the one methodological gap Phase 8C flagged in
itself: its cohort-vs-card-selection comparison used an automatically
selected, suboptimal LATE signal (>=35% drawdown + 3 days deceleration,
firing FR+11 -- two days after the real premium/meta bottom). This
script re-runs that comparison pinned to the two signals Phase 8C's own
sweep showed were actually strong:

    Entry A: drawdown >= 50% + 1 day of deceleration  (fired FR+9, the
             realized bottom, 79.4% win rate in Phase 8C)
    Entry B: drawdown >= 40% + 2 days of deceleration (fired FR+10,
             60.8% win rate in Phase 8C)

Reuses research_fc26_phase8_investment_analysis (data/cohorts/ROI
stats), research_fc26_phase8b_bottom_trigger_analysis (cohort daily
reconstruction, no-lookahead primitives, realized-bottom grading,
capital-deployment simulation), and research_fc26_phase8c_trigger_
validation (the drawdown+deceleration signal family, per-card day
series) -- nothing about cohort assignment, signal grading, or the
no-lookahead discipline is redefined here.

Run the same Coolify one-off way as every other research_fc26_*.py
script:

    python research_fc26_phase8d_deployment_validation.py

Also formalizes and TESTS (not just proposes in prose) a dynamic
drawdown definition: "today's drawdown is in the Nth percentile of
every drawdown observed so far this cycle" -- computed with the same
no-lookahead discipline as every other signal here -- to check whether
a RELATIVE threshold reproduces the FR+9/FR+10 findings without
hardcoding an absolute FC26 percentage.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import date, timedelta

from db_connect import connect_with_retry
from research_fc26_phase8_investment_analysis import (
    load_data,
    build_reference_attrs,
    build_cohorts,
    compute_cell,
    roi_stats,
    price_on_exact,
)
from research_fc26_phase8b_bottom_trigger_analysis import (
    build_cohort_day_series,
    cohort_series_index,
    realized_bottom,
    is_new_low,
    evaluate_signal,
    simulate_deployment,
    max_adverse_excursion,
)
from research_fc26_phase8c_trigger_validation import (
    ANALYSIS_START,
    DETAIL_END,
    decel_for_days,
    make_signal,
    get_fire_dates,
    build_card_day_series,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("phase8d")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

ENTRY_SIGNALS = [
    ("A: dd>=50% + decel1d", make_signal(50, 1)),
    ("B: dd>=40% + decel2d", make_signal(40, 2)),
]
HOLDS = (2, 3)
BANKROLLS = (100_000, 500_000, 1_000_000)


def cum_return(value_by_date: dict[date, float], d: date, n: int) -> float | None:
    v1, v0 = value_by_date.get(d), value_by_date.get(d - timedelta(days=n))
    if v1 is None or v0 is None or v0 == 0:
        return None
    return (v1 - v0) / v0 * 100


def print_stats(label: str, stats: dict, indent: str = "  ") -> None:
    if stats.get("n", 0) == 0:
        log.info("%s%-40s n=0 (no qualifying cards)", indent, label)
        return
    log.info("%s%-40s n=%4d median=%7s%% mean=%7s%% win=%6s%% p25=%7s%% p10dn=%7s%% worst=%7s%%",
              indent, label, stats["n"], stats.get("median"), stats.get("mean"), stats.get("win_rate"),
              stats.get("p25"), stats.get("downside_p10"), stats.get("worst"))


# ---------------------------------------------------------------------------
# Approach 2 -- cohort gate + simple, independently-tested card confirmations
# ---------------------------------------------------------------------------
def build_confirmations(prices, cohort_idx, card_rows_by_cid, card_value_by_cid):
    def get(cid, d):
        rows = card_rows_by_cid.get(cid)
        if not rows:
            return None
        for r in rows:
            if r["date"] == d:
                return r
        return None

    return {
        "card_also_decelerating": lambda cid, d: (
            (r := get(cid, d)) is not None and (rp := get(cid, d - timedelta(days=1))) is not None
            and r["ret1d"] > rp["ret1d"]
        ),
        "card_no_new_3d_low": lambda cid, d: not is_new_low(prices[cid], d, 3),
        "card_outperforms_cohort_1d": lambda cid, d: (
            (r := get(cid, d)) is not None and cohort_idx.get(d) is not None
            and r["ret1d"] > cohort_idx[d]["ret1d"]
        ),
        "card_outperforms_cohort_2d": lambda cid, d: (
            (a := cum_return(card_value_by_cid[cid], d, 2)) is not None
            and (b := cum_return({dd: r["median"] for dd, r in cohort_idx.items()}, d, 2)) is not None
            and a > b
        ),
        "card_dd_above_cohort_dd": lambda cid, d: (
            (r := get(cid, d)) is not None and cohort_idx.get(d) is not None
            and r["drawdown_from_peak"] >= cohort_idx[d]["drawdown_from_peak"]
        ),
        "card_flat_or_up": lambda cid, d: (
            (r := get(cid, d)) is not None and r["ret1d"] >= -0.5
        ),
    }


def roi_for_cards(card_ids, prices, entry_day, holding) -> dict:
    rois = []
    for cid in card_ids:
        s = prices.get(cid)
        if not s:
            continue
        entry = price_on_exact(s, entry_day)
        exit_ = price_on_exact(s, entry_day + timedelta(days=holding))
        if entry and exit_ and entry > 0:
            rois.append((exit_ * 0.95 - entry) / entry)
    return roi_stats(rois)


# ---------------------------------------------------------------------------
# Approach 3 -- ranked selection (not a hard filter)
# ---------------------------------------------------------------------------
def rank_cards(premium_ids, cohort_idx, card_rows_by_cid, card_value_by_cid, d, feature):
    scored = []
    for cid in premium_ids:
        r = next((row for row in card_rows_by_cid.get(cid, []) if row["date"] == d), None)
        if r is None:
            continue
        if feature == "relative_strength_2d":
            cohort_val = {dd: row["median"] for dd, row in cohort_idx.items()}
            card_cum = cum_return(card_value_by_cid[cid], d, 2)
            cohort_cum = cum_return(cohort_val, d, 2)
            if card_cum is None or cohort_cum is None:
                continue
            score = card_cum - cohort_cum  # higher = outperforming cohort more
        elif feature == "drawdown_magnitude":
            score = r["drawdown_from_peak"]  # higher = fell further
        else:
            continue
        scored.append((cid, score))
    scored.sort(key=lambda t: -t[1])
    return [cid for cid, _ in scored]


# ---------------------------------------------------------------------------
# Dynamic/relative drawdown definition -- tested, not just proposed
# ---------------------------------------------------------------------------
def make_percentile_signal(pctile: float, decel_days: int):
    def fn(rows, i):
        so_far = [r["drawdown_from_peak"] for r in rows[: i + 1]]
        if len(so_far) < 5:
            return False
        current = rows[i]["drawdown_from_peak"]
        rank = sum(1 for x in so_far if x <= current) / len(so_far) * 100
        return rank >= pctile and decel_for_days(rows, i, decel_days)
    return fn


# ---------------------------------------------------------------------------
async def main() -> int:
    conn = await connect_with_retry(DATABASE_URL)
    try:
        log.info("Loading fc26_historical_daily_prices + futgg_players (gold common/rare only)...")
        prices, meta = await load_data(conn)
        ref = build_reference_attrs(prices, meta)
        cohorts = build_cohorts(ref)
        meta_tier = cohorts["meta_tier"]
        premium_ids = {cid for cid, label in meta_tier.items() if label == "premium meta"}
        log.info("premium meta cohort: %d cards", len(premium_ids))

        premium_rows = build_cohort_day_series(premium_ids, prices)
        cohort_idx = cohort_series_index(premium_rows)
        bottom = realized_bottom(premium_rows, ANALYSIS_START, DETAIL_END)
        log.info("realized bottom (evaluation-only): %s, %.1f%% below peak",
                  bottom["date"].isoformat() if bottom else "-", bottom["drawdown_from_peak"] if bottom else 0)

        card_rows_by_cid = {cid: build_card_day_series(prices[cid]) for cid in premium_ids if cid in prices}
        card_value_by_cid = {cid: {r["date"]: r["price"] for r in rows} for cid, rows in card_rows_by_cid.items()}
        confirmations = build_confirmations(prices, cohort_idx, card_rows_by_cid, card_value_by_cid)

        best_deployment = {}  # (entry_label,) -> {"cohort": stats, "best_alt": (name, stats, ids)}

        for entry_label, fn in ENTRY_SIGNALS:
            log.info("=" * 100)
            log.info("ENTRY SIGNAL: %s", entry_label)
            log.info("=" * 100)
            fires = get_fire_dates(fn, premium_rows)
            if not fires:
                log.info("  no fires for this signal -- skipping")
                continue
            d = fires[0]
            rel_fr_note = (bottom["date"] - d).days if bottom else None
            log.info("  first fire: %s (%s days from realized bottom)", d.isoformat(), rel_fr_note)

            # --- Approach 1: cohort-only ---
            log.info("--- Approach 1: cohort-only (buy whole eligible cohort) ---")
            cohort_stats_by_h = {}
            for h in HOLDS:
                stats = compute_cell(premium_ids, prices, d, h)
                cohort_stats_by_h[h] = stats
                print_stats(f"hold={h}d", stats)
                mae = max_adverse_excursion(cohort_idx, d, horizon=h)
                log.info("    max adverse excursion within %dd: %s%%", h, round(mae, 2) if mae is not None else None)

            # --- Approach 2: cohort gate + simple confirmations ---
            log.info("--- Approach 2: cohort gate + individual card confirmation (tested independently) ---")
            approach2_best = None
            for name, rule in confirmations.items():
                qualifying = [cid for cid in premium_ids if rule(cid, d)]
                log.info("  confirmation=%-28s qualifying_cards=%d/%d", name, len(qualifying), len(premium_ids))
                for h in HOLDS:
                    stats = roi_for_cards(qualifying, prices, d, h)
                    print_stats(f"    hold={h}d", stats, indent="")
                    if h == 3 and stats.get("n", 0) >= 10:
                        if approach2_best is None or (stats.get("median") or -999) > approach2_best[1]:
                            approach2_best = (name, stats.get("median"), qualifying)

            # --- Approach 3: ranked selection ---
            log.info("--- Approach 3: ranked selection (top 25%% / top 50%% / full cohort) ---")
            approach3_best = None
            for feature in ("relative_strength_2d", "drawdown_magnitude"):
                ranked = rank_cards(premium_ids, cohort_idx, card_rows_by_cid, card_value_by_cid, d, feature)
                if not ranked:
                    log.info("  feature=%-24s no rankable cards", feature)
                    continue
                log.info("  feature=%s (%d rankable cards)", feature, len(ranked))
                for frac, label in ((0.25, "top25%"), (0.5, "top50%"), (1.0, "full cohort")):
                    n_take = max(1, round(len(ranked) * frac))
                    subset = ranked[:n_take]
                    for h in HOLDS:
                        stats = roi_for_cards(subset, prices, d, h)
                        print_stats(f"    {label:<12} hold={h}d", stats, indent="")
                        if h == 3 and stats.get("n", 0) >= 10:
                            if approach3_best is None or (stats.get("median") or -999) > approach3_best[1]:
                                approach3_best = (f"{feature}/{label}", stats.get("median"), subset)

            # --- Capital deployment: cohort-only vs the best alternative found ---
            log.info("--- Capital deployment simulation (equal-weight, 5%% tax, hold=3d) ---")
            for bankroll in BANKROLLS:
                sim = simulate_deployment(f"{entry_label} cohort-only", premium_ids, prices, d, 3, bankroll)
                if sim:
                    log.info("  cohort-only        bankroll=%9d -> %9d (%+.2f%%) win=%s%% losing=%s%% worst_decile=%s%%",
                              bankroll, sim["ending_bankroll"], sim["after_tax_return_pct"],
                              sim["win_rate_pct"], sim["losing_pct"], sim["worst_decile_pct"])
            best_alt = None
            if approach2_best and (approach3_best is None or approach2_best[1] >= approach3_best[1]):
                best_alt = ("Approach 2: " + approach2_best[0], approach2_best[2])
            elif approach3_best:
                best_alt = ("Approach 3: " + approach3_best[0], approach3_best[2])
            if best_alt:
                name, ids = best_alt
                log.info("  best alternative selection found: %s (n=%d cards)", name, len(ids))
                for bankroll in BANKROLLS:
                    sim = simulate_deployment(f"{entry_label} {name}", set(ids), prices, d, 3, bankroll)
                    if sim:
                        log.info("  %-18s bankroll=%9d -> %9d (%+.2f%%) win=%s%% losing=%s%% worst_decile=%s%%",
                                  name[:18], bankroll, sim["ending_bankroll"], sim["after_tax_return_pct"],
                                  sim["win_rate_pct"], sim["losing_pct"], sim["worst_decile_pct"])
                cohort_median_3d = cohort_stats_by_h[3].get("median")
                if cohort_median_3d is not None and approach2_best is None and approach3_best is None:
                    log.info("  no alternative selection beat n>=10 threshold -- cohort-only stands unchallenged")
                elif cohort_median_3d is not None:
                    best_median = best_alt and (approach2_best[1] if best_alt[0].startswith("Approach 2") else approach3_best[1])
                    verdict = "ALTERNATIVE SELECTION WINS" if (best_median or -999) > cohort_median_3d else "COHORT-ONLY WINS"
                    log.info("  VERDICT for %s: %s (cohort median=%.2f%% vs best alt median=%.2f%%)",
                              entry_label, verdict, cohort_median_3d, best_median if best_median is not None else float("nan"))

        # ------------------------------------------------------------- dynamic drawdown
        log.info("=" * 100)
        log.info("DYNAMIC/RELATIVE DRAWDOWN DEFINITION -- tested against the same realized bottom")
        log.info("=" * 100)
        for pctile in (85, 90, 95, 98):
            for decel_days in (1, 2):
                fn = make_percentile_signal(pctile, decel_days)
                r = evaluate_signal(f"pctile{pctile}_decel{decel_days}d", fn, premium_rows, premium_ids, prices,
                                     bottom["date"] if bottom else None)
                if r is None:
                    log.info("  percentile>=%d decel=%dd: no fires", pctile, decel_days)
                    continue
                first = r["per_fire"][0]
                log.info("  percentile>=%3d decel=%dd  1st_fire=%s  d_from_bottom=%s  pre_bottom_false=%d  "
                          "roi3d_median=%s%% win3d=%s%%",
                          pctile, decel_days, r["first_fire"].isoformat(), r["days_from_bottom_first_fire"],
                          r["pre_bottom_fires"], first["roi_3d"].get("median"), first["roi_3d"].get("win_rate"))

        log.info("=" * 100)
        log.info("PHASE 8D COMPLETE. Same no-lookahead discipline as 8B/8C throughout: every signal and "
                  "confirmation rule reads only data at or before its own decision date; the realized bottom "
                  "is evaluation-only. Entry A and Entry B above were specified directly from Phase 8C's "
                  "sweep results, not re-auto-selected, correcting that run's late-signal methodological gap.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
