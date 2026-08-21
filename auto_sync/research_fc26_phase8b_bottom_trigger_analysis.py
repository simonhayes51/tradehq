"""
Phase 8B -- bottom & entry-trigger study for the premium/meta FC26 gold
cohort (and comparison cohorts).

Research-only, read-only. Reuses research_fc26_phase8_investment_analysis
for data loading, cohort construction (position group, within-position
launch-price-percentile "meta tier"), and ROI stats -- nothing about how
a card is assigned to a cohort is redefined here.

Run the same Coolify one-off way as every other research_fc26_*.py
script: temporarily override PlayerSync's start command to

    python research_fc26_phase8b_bottom_trigger_analysis.py

QUESTION THIS ANSWERS (not "when was the bottom" -- we already know that
from Phase 8): what OBSERVABLE, TRAILING-ONLY market condition would have
told a trader in real time, without knowing the future, that the
post-Full-Release crash in premium/meta golds was ending?

METHOD
------
For each cohort, build one row per calendar day (median price, breadth,
new-lows, recoveries, drawdown) using only that day and earlier days --
exactly what would have been computable live. Every signal in SIGNALS
below is a function of that trailing history only.

The cohort's REALIZED bottom date is computed too, but ONLY to grade
signals after the fact (how early/late did each firing land relative to
it, what was the perfect-hindsight benchmark) -- it never enters any
signal's own decision logic. That separation is the whole point of this
script; see the "NO LOOKAHEAD" markers below.
"""
from __future__ import annotations

import asyncio
import logging
import os
import statistics
from collections import defaultdict
from datetime import date, timedelta

from db_connect import connect_with_retry
from research_fc26_phase8_investment_analysis import (
    EA_TAX,
    WEB_APP_LAUNCH,
    FULL_RELEASE,
    load_data,
    build_reference_attrs,
    build_cohorts,
    roi_stats,
    price_on_exact,
    compute_cell,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("phase8b")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

ANALYSIS_START = WEB_APP_LAUNCH  # FR-9 -- need the whole EA/decline period to catch false triggers
ANALYSIS_END = FULL_RELEASE + timedelta(days=21)
DETAIL_END = FULL_RELEASE + timedelta(days=14)
MIN_FIRE_COHORT_N = 20  # a cohort day with fewer priced cards than this is too thin to act on

COHORT_ORDER = ["premium meta", "mid-tier meta", "desirable starter", "ordinary non-meta"]


# ---------------------------------------------------------------------------
# Trailing-window primitives (used identically at cohort-median level and
# at individual-card level -- see is_new_low / is_recovery below)
# ---------------------------------------------------------------------------
def trailing_prices(series: dict[date, float], d: date, window: int, include_today: bool) -> list[float]:
    hi = d if include_today else d - timedelta(days=1)
    lo = d - timedelta(days=window - 1) if include_today else d - timedelta(days=window)
    return [series[dd] for dd in series if lo <= dd <= hi]


def is_new_low(series: dict[date, float], d: date, window: int) -> bool:
    """NO LOOKAHEAD: uses only prices on or before d."""
    if d not in series:
        return False
    window_prices = trailing_prices(series, d, window, include_today=True)
    return len(window_prices) >= 2 and series[d] <= min(window_prices)


def trailing_low_before(series: dict[date, float], d: date, window: int) -> float | None:
    """NO LOOKAHEAD: strictly prior `window` days, excludes d itself."""
    prev = trailing_prices(series, d, window, include_today=False)
    return min(prev) if prev else None


def is_recovery(series: dict[date, float], d: date, window: int, pct_above: float) -> bool:
    """NO LOOKAHEAD: is d's price >= pct_above% over the trailing (pre-d) low."""
    if d not in series:
        return False
    lo = trailing_low_before(series, d, window)
    return lo is not None and lo > 0 and series[d] >= lo * (1 + pct_above / 100.0)


# ---------------------------------------------------------------------------
# Cohort daily reconstruction
# ---------------------------------------------------------------------------
def build_cohort_day_series(cohort_ids: set[int], prices: dict) -> list[dict]:
    """One dict per calendar day, ANALYSIS_START..ANALYSIS_END. NO LOOKAHEAD
    within any single day's fields -- every field uses only that day and
    earlier days from the underlying per-card series."""
    cohort_series = {cid: prices[cid] for cid in cohort_ids if cid in prices}
    all_dates = sorted({d for s in cohort_series.values() for d in s if ANALYSIS_START <= d <= ANALYSIS_END})
    if not all_dates:
        return []

    median_series: dict[date, float] = {}
    rows: list[dict] = []
    running_peak = None
    launch_price = None
    prev_day_prices: dict[int, float] = {}

    for d in all_dates:
        day_prices = {cid: s[d] for cid, s in cohort_series.items() if d in s and s[d] > 0}
        n = len(day_prices)
        if n == 0:
            continue
        median = statistics.median(day_prices.values())
        median_series[d] = median
        if launch_price is None:
            launch_price = median
        running_peak = median if running_peak is None else max(running_peak, median)

        rising = falling = flat = 0
        for cid, p in day_prices.items():
            prev = prev_day_prices.get(cid)
            if prev is not None and prev > 0:
                r = (p - prev) / prev
                if r > 0.01:
                    rising += 1
                elif r < -0.01:
                    falling += 1
                else:
                    flat += 1
        moved = rising + falling + flat
        pct_rising = rising / moved * 100 if moved else float("nan")
        pct_falling = falling / moved * 100 if moved else float("nan")
        pct_flat = flat / moved * 100 if moved else float("nan")

        new_low_3d = sum(1 for s in cohort_series.values() if is_new_low(s, d, 3))
        new_low_5d = sum(1 for s in cohort_series.values() if is_new_low(s, d, 5))
        recover_5pct_5d = sum(1 for s in cohort_series.values() if is_recovery(s, d, 5, 5.0))

        prev_median = median_series.get(all_dates[all_dates.index(d) - 1]) if d != all_dates[0] else None
        ret1d = (median - prev_median) / prev_median * 100 if prev_median else float("nan")

        rows.append({
            "date": d, "rel_fr": (d - FULL_RELEASE).days, "n": n, "median": median,
            "ret1d": ret1d,
            "drawdown_from_peak": (running_peak - median) / running_peak * 100 if running_peak else 0.0,
            "drawdown_from_launch": (launch_price - median) / launch_price * 100 if launch_price else 0.0,
            "pct_rising": pct_rising, "pct_falling": pct_falling, "pct_flat": pct_flat,
            "breadth_spread": pct_rising - pct_falling if moved else float("nan"),
            "new_low_3d": new_low_3d, "new_low_5d": new_low_5d, "recover_5pct_5d": recover_5pct_5d,
        })
        prev_day_prices = day_prices

    # dist_above_Nd_low needs the cohort median series built above (2-pass)
    for row in rows:
        d = row["date"]
        for w in (3, 5, 7):
            lo = trailing_low_before(median_series, d, w)
            row[f"dist_above_{w}d_low"] = ((row["median"] - lo) / lo * 100) if lo else float("nan")

    return rows


def cohort_series_index(rows: list[dict]) -> dict[date, dict]:
    return {r["date"]: r for r in rows}


def realized_bottom(rows: list[dict], search_start: date, search_end: date) -> dict | None:
    """Evaluation-only: the actual lowest median-price day in a window.
    Never used inside a signal's own decision logic -- only to grade signals
    after the fact (days_from_bottom, perfect-hindsight benchmark)."""
    candidates = [r for r in rows if search_start <= r["date"] <= search_end]
    return min(candidates, key=lambda r: r["median"]) if candidates else None


# ---------------------------------------------------------------------------
# Signal library -- every signal(rows, i) reads rows[0..i] only
# ---------------------------------------------------------------------------
def build_signals() -> dict:
    sig = {
        "breadth_positive": lambda rows, i: rows[i]["breadth_spread"] > 0,
        "breadth_gt5pp": lambda rows, i: rows[i]["breadth_spread"] > 5,
        "breadth_gt10pp": lambda rows, i: rows[i]["breadth_spread"] > 10,
        "breadth_improve_2d": lambda rows, i: (
            i >= 2 and rows[i]["breadth_spread"] > rows[i - 1]["breadth_spread"] > rows[i - 2]["breadth_spread"]
        ),
        "pct_falling_drop_sharp_10pp": lambda rows, i: (
            i >= 1 and (rows[i - 1]["pct_falling"] - rows[i]["pct_falling"]) >= 10
        ),
        "fewer_new_3d_lows": lambda rows, i: i >= 1 and rows[i]["new_low_3d"] < rows[i - 1]["new_low_3d"],
        "decline_decel_2d": lambda rows, i: (
            i >= 2 and rows[i]["ret1d"] > rows[i - 1]["ret1d"] > rows[i - 2]["ret1d"]
        ),
        "flat_day": lambda rows, i: abs(rows[i]["ret1d"]) < 1.0,
        "positive_day": lambda rows, i: rows[i]["ret1d"] > 0,
        "two_nonneg_days": lambda rows, i: i >= 1 and rows[i]["ret1d"] >= -0.5 and rows[i - 1]["ret1d"] >= -0.5,
        "two_positive_days": lambda rows, i: i >= 1 and rows[i]["ret1d"] > 0 and rows[i - 1]["ret1d"] > 0,
    }
    for w in (3, 5, 7):
        for t in (2.0, 3.0, 5.0, 7.5, 10.0):
            name = f"recover_{w}d_low_{t}pct"
            sig[name] = (lambda rows, i, w=w, t=t: rows[i][f"dist_above_{w}d_low"] >= t)

    # 2-3 condition combos (kept small deliberately -- see module docstring
    # + Section 9 of the request: broad thresholds, not a bespoke 12-clause rule)
    for dd in (15, 20, 25, 30, 35, 40):
        sig[f"dd{dd}_breadth_pos"] = (
            lambda rows, i, dd=dd: rows[i]["drawdown_from_peak"] >= dd and rows[i]["breadth_spread"] > 0
        )
        sig[f"dd{dd}_decel2d"] = (
            lambda rows, i, dd=dd: (
                rows[i]["drawdown_from_peak"] >= dd
                and i >= 2 and rows[i]["ret1d"] > rows[i - 1]["ret1d"] > rows[i - 2]["ret1d"]
            )
        )
        sig[f"dd{dd}_recover5pct_5d"] = (
            lambda rows, i, dd=dd: rows[i]["drawdown_from_peak"] >= dd and rows[i]["dist_above_5d_low"] >= 5.0
        )
        sig[f"dd{dd}_breadth10pp"] = (
            lambda rows, i, dd=dd: rows[i]["drawdown_from_peak"] >= dd and rows[i]["breadth_spread"] > 10
        )
    return sig


# ---------------------------------------------------------------------------
# Signal evaluation
# ---------------------------------------------------------------------------
def max_adverse_excursion(rows_by_date: dict, entry_day: date, horizon: int = 14) -> float | None:
    entry_row = rows_by_date.get(entry_day)
    if not entry_row:
        return None
    entry_price = entry_row["median"]
    forward = [rows_by_date[entry_day + timedelta(days=h)]["median"]
               for h in range(1, horizon + 1) if entry_day + timedelta(days=h) in rows_by_date]
    if not forward or entry_price <= 0:
        return None
    worst = min(forward)
    return max(0.0, (entry_price - worst) / entry_price * 100)


def evaluate_signal(name, fn, rows, cohort_ids, prices, bottom_date, min_i=7):
    rows_by_date = cohort_series_index(rows)
    fires = []
    for i in range(min_i, len(rows)):
        if rows[i]["n"] < MIN_FIRE_COHORT_N:
            continue
        try:
            fired = fn(rows, i)
        except (KeyError, TypeError):
            fired = False
        if fired:
            d = rows[i]["date"]
            fires.append(d)
    if not fires:
        return None

    first_fire = fires[0]
    days_from_bottom_first = (first_fire - bottom_date).days if bottom_date else None
    pre_bottom_fires = sum(1 for d in fires if bottom_date and d < bottom_date)

    per_fire = []
    for d in fires:
        row = {"date": d, "days_from_bottom": (d - bottom_date).days if bottom_date else None}
        for h in (1, 2, 3, 5, 7):
            row[f"roi_{h}d"] = compute_cell(cohort_ids, prices, d, h)
        row["mae14"] = max_adverse_excursion(rows_by_date, d)
        per_fire.append(row)

    def agg(h):
        medians = [f[f"roi_{h}d"]["median"] for f in per_fire if f[f"roi_{h}d"].get("n", 0) > 0]
        wins = [f[f"roi_{h}d"]["win_rate"] for f in per_fire if f[f"roi_{h}d"].get("n", 0) > 0]
        return {
            "n_fires_with_data": len(medians),
            "median_of_medians": round(statistics.median(medians), 2) if medians else None,
            "median_win_rate": round(statistics.median(wins), 1) if wins else None,
        }

    return {
        "name": name, "n_fires": len(fires), "first_fire": first_fire,
        "days_from_bottom_first_fire": days_from_bottom_first,
        "pre_bottom_fires": pre_bottom_fires, "pre_bottom_frac": round(pre_bottom_fires / len(fires) * 100, 1),
        "avg_mae14": round(statistics.mean([f["mae14"] for f in per_fire if f["mae14"] is not None]), 2)
                     if any(f["mae14"] is not None for f in per_fire) else None,
        "by_holding": {h: agg(h) for h in (1, 2, 3, 5, 7)},
        "per_fire": per_fire,
    }


# ---------------------------------------------------------------------------
# Card-level vs cohort-level comparison
# ---------------------------------------------------------------------------
def card_level_vs_cohort_level(cohort_ids, prices, dd_threshold, recover_pct, recover_window):
    """For the same underlying idea (drawdown >= dd_threshold, then recovery
    >= recover_pct off the trailing recover_window-day low), compare firing
    it per-card (each card judged on its OWN series) against firing it at
    the cohort level (whole cohort bought together when the cohort median
    satisfies the same condition)."""
    card_level_rois = {1: [], 2: [], 3: [], 5: [], 7: []}
    for cid in cohort_ids:
        s = prices.get(cid)
        if not s or len(s) < 10:
            continue
        dates = sorted(s)
        peak = None
        for d in dates:
            peak = s[d] if peak is None else max(peak, s[d])
            if peak <= 0:
                continue
            dd = (peak - s[d]) / peak * 100
            if dd >= dd_threshold and is_recovery(s, d, recover_window, recover_pct):
                for h in (1, 2, 3, 5, 7):
                    exit_ = price_on_exact(s, d + timedelta(days=h))
                    if exit_:
                        card_level_rois[h].append((exit_ * (1 - EA_TAX) - s[d]) / s[d])
    return {h: roi_stats(v) for h, v in card_level_rois.items()}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_daily_table(cohort_name, rows, detailed):
    log.info("--- %s -- daily reconstruction (%s) ---", cohort_name,
              "full detail" if detailed else "condensed")
    if detailed:
        log.info("%-11s %5s %6s %7s %7s %6s %6s %6s %7s %5s %5s %5s",
                  "date", "relFR", "median", "dd_pk%", "dd_lnc%", "%up", "%dn", "brdth", "ret1d%",
                  "nl3d", "nl5d", "rec5%")
        for r in rows:
            if r["date"] > DETAIL_END:
                continue
            log.info("%-11s %+5d %6.0f %7.1f %7.1f %6.1f %6.1f %6.1f %7.2f %5d %5d %5d",
                      r["date"].isoformat(), r["rel_fr"], r["median"], r["drawdown_from_peak"],
                      r["drawdown_from_launch"], r["pct_rising"], r["pct_falling"], r["breadth_spread"],
                      r["ret1d"], r["new_low_3d"], r["new_low_5d"], r["recover_5pct_5d"])
    else:
        log.info("%-11s %5s %6s %7s %6s", "date", "relFR", "median", "dd_pk%", "brdth")
        for r in rows:
            if r["date"] > DETAIL_END:
                continue
            log.info("%-11s %+5d %6.0f %7.1f %6.1f", r["date"].isoformat(), r["rel_fr"], r["median"],
                      r["drawdown_from_peak"], r["breadth_spread"])


def print_signal_results(cohort_name, results, detailed, top_n=15):
    scored = []
    for r in results:
        if r is None:
            continue
        r7 = r["by_holding"][7]
        if r7["median_of_medians"] is None:
            continue
        # prefer signals with fewer pre-bottom (false) fires and a positive 7d median
        score = r7["median_of_medians"] - r["pre_bottom_frac"] * 0.3
        scored.append((score, r))
    scored.sort(key=lambda t: -t[0])

    log.info("--- %s -- signal scoreboard (ranked by 7d median ROI adjusted for false-fire rate) ---",
              cohort_name)
    log.info("%-28s %6s %10s %8s %8s %8s %8s %8s %8s",
              "signal", "fires", "1st_fire", "d_from_bt", "pre_bt%", "mae14%", "roi7d_md", "win7d", "roi3d_md")
    for score, r in scored[:top_n]:
        log.info("%-28s %6d %10s %+8s %8.1f %8s %8s %8s %8s",
                  r["name"], r["n_fires"], r["first_fire"].isoformat(),
                  r["days_from_bottom_first_fire"], r["pre_bottom_frac"],
                  r["avg_mae14"], r["by_holding"][7]["median_of_medians"],
                  r["by_holding"][7]["median_win_rate"], r["by_holding"][3]["median_of_medians"])

    if detailed and scored:
        best_name, best = scored[0][1]["name"], scored[0][1]
        log.info("--- %s -- per-fire detail for top signal (%s) ---", cohort_name, best_name)
        log.info("%-11s %10s %8s %8s %8s %8s", "fire_date", "d_from_bot", "roi3d_md", "win3d", "roi7d_md", "win7d")
        for f in best["per_fire"]:
            log.info("%-11s %+10s %8s %8s %8s %8s",
                      f["date"].isoformat(), f["days_from_bottom"],
                      f["roi_3d"].get("median"), f["roi_3d"].get("win_rate"),
                      f["roi_7d"].get("median"), f["roi_7d"].get("win_rate"))


def simulate_deployment(cohort_name, cohort_ids, prices, entry_day, holding, bankroll=100_000):
    entries = []
    for cid in cohort_ids:
        s = prices.get(cid)
        if not s:
            continue
        entry = price_on_exact(s, entry_day)
        if entry and entry > 0:
            entries.append((cid, entry))
    if not entries:
        return None
    stake_per_card = bankroll / len(entries)
    total_end = 0.0
    rois = []
    for cid, entry in entries:
        s = prices[cid]
        exit_ = price_on_exact(s, entry_day + timedelta(days=holding))
        if exit_ is None:
            total_end += stake_per_card  # unrealized/no exit data -- treat as held flat, not lost
            continue
        qty = stake_per_card / entry
        proceeds = qty * exit_ * (1 - EA_TAX)
        total_end += proceeds
        rois.append((exit_ * (1 - EA_TAX) - entry) / entry)
    stats = roi_stats(rois)
    losing_pct = round(sum(1 for r in rois if r < 0) / len(rois) * 100, 1) if rois else None
    return {
        "cohort": cohort_name, "entry_day": entry_day, "holding": holding,
        "n_cards": len(entries), "starting_bankroll": bankroll,
        "ending_bankroll": round(total_end), "after_tax_return_pct": round((total_end / bankroll - 1) * 100, 2),
        "median_card_roi_pct": stats.get("median"), "win_rate_pct": stats.get("win_rate"),
        "losing_pct": losing_pct, "worst_decile_pct": stats.get("downside_p10"),
    }


def sensitivity_check(cohort_ids, prices, rows, bottom_date, base_dd, base_recover_pct, window=5):
    signals = build_signals()
    log.info("--- sensitivity: dd%%%s x nearby thresholds (dd+/-5pp, recover+/-2.5pp) ---", base_dd)
    log.info("%-10s %-10s %8s %10s %8s %8s", "dd_thresh", "rec_thresh", "fires", "1st_fire", "pre_bt%", "roi7d_md")
    for dd in (base_dd - 5, base_dd, base_dd + 5):
        for rp in (max(2.5, base_recover_pct - 2.5), base_recover_pct, base_recover_pct + 2.5):
            def fn(rows, i, dd=dd, rp=rp, window=window):
                return rows[i]["drawdown_from_peak"] >= dd and rows[i][f"dist_above_{window}d_low"] >= rp
            r = evaluate_signal(f"dd{dd}_rec{rp}", fn, rows, cohort_ids, prices, bottom_date)
            if r is None:
                log.info("%-10.0f %-10.1f %8s %10s %8s %8s", dd, rp, 0, "-", "-", "-")
                continue
            log.info("%-10.0f %-10.1f %8d %10s %8.1f %8s", dd, rp, r["n_fires"],
                      r["first_fire"].isoformat(), r["pre_bottom_frac"], r["by_holding"][7]["median_of_medians"])


# ---------------------------------------------------------------------------
async def main() -> int:
    conn = await connect_with_retry(DATABASE_URL)
    try:
        log.info("Loading fc26_historical_daily_prices + futgg_players (gold common/rare only)...")
        prices, meta = await load_data(conn)
        ref = build_reference_attrs(prices, meta)
        cohorts = build_cohorts(ref)
        meta_tier = cohorts["meta_tier"]

        cohort_pools: dict[str, set[int]] = defaultdict(set)
        for cid, label in meta_tier.items():
            if label in COHORT_ORDER:
                cohort_pools[label].add(cid)

        signals = build_signals()

        for idx, cohort_name in enumerate(COHORT_ORDER):
            ids = cohort_pools.get(cohort_name, set())
            log.info("=" * 100)
            log.info("COHORT: %s (n=%d cards)%s", cohort_name, len(ids),
                      "  [PRIMARY]" if cohort_name == "premium meta" else
                      ("  [CONTROL]" if cohort_name == "ordinary non-meta" else ""))
            log.info("=" * 100)
            if len(ids) < MIN_FIRE_COHORT_N:
                log.info("  skipped -- fewer than %d cards", MIN_FIRE_COHORT_N)
                continue

            rows = build_cohort_day_series(ids, prices)
            if not rows:
                log.info("  skipped -- no priced days in range")
                continue

            detailed = cohort_name == "premium meta"
            print_daily_table(cohort_name, rows, detailed)

            bottom = realized_bottom(rows, ANALYSIS_START, DETAIL_END)
            if bottom:
                log.info("  realized bottom (evaluation-only, never used by any signal): %s "
                          "(FR%+d) median=%.0f, %.1f%% below running peak",
                          bottom["date"].isoformat(), bottom["rel_fr"], bottom["median"], bottom["drawdown_from_peak"])
                perfect = {h: compute_cell(ids, prices, bottom["date"], h) for h in (2, 3, 5, 7, 10, 14)}
                log.info("  perfect-hindsight benchmark (buy exactly at realized bottom):")
                for h, st in perfect.items():
                    log.info("    hold=%2dd median=%s%% win=%s%% n=%s", h, st.get("median"), st.get("win_rate"), st.get("n"))

            results = [evaluate_signal(name, fn, rows, ids, prices, bottom["date"] if bottom else None)
                       for name, fn in signals.items()]
            print_signal_results(cohort_name, results, detailed)

            if detailed and bottom:
                sensitivity_check(ids, prices, rows, bottom["date"], base_dd=30, base_recover_pct=5.0)

                log.info("--- card-level vs cohort-level: drawdown>=30%% + recover>=5%% off trailing 5d low ---")
                card_level = card_level_vs_cohort_level(ids, prices, 30, 5.0, 5)
                for h in (1, 2, 3, 5, 7):
                    log.info("  card-level  hold=%dd median=%s%% win=%s%% n=%s", h,
                              card_level[h].get("median"), card_level[h].get("win_rate"), card_level[h].get("n"))
                cohort_level_sig = evaluate_signal("dd30_recover5pct_5d", signals["dd30_recover5pct_5d"],
                                                     rows, ids, prices, bottom["date"])
                if cohort_level_sig:
                    for h in (1, 2, 3, 5, 7):
                        log.info("  cohort-level hold=%dd median=%s%% win=%s%%", h,
                                  cohort_level_sig["by_holding"][h]["median_of_medians"],
                                  cohort_level_sig["by_holding"][h]["median_win_rate"])

                log.info("--- capital simulation: 100,000 coin bankroll, entering on top-3 signals' first fire ---")
                scored_names = sorted(
                    [r for r in results if r],
                    key=lambda r: -(r["by_holding"][7]["median_of_medians"] or -999) + r["pre_bottom_frac"] * 0.3,
                )[:3]
                for r in scored_names:
                    for h in (2, 3, 5, 7):
                        sim = simulate_deployment(cohort_name, ids, prices, r["first_fire"], h)
                        if sim:
                            log.info("  signal=%-24s entry=%s hold=%dd n_cards=%3d end_bankroll=%s "
                                      "(%+.2f%%) median_card_roi=%s%% win=%s%% losing=%s%% worst_decile=%s%%",
                                      r["name"], sim["entry_day"], h, sim["n_cards"], sim["ending_bankroll"],
                                      sim["after_tax_return_pct"], sim["median_card_roi_pct"], sim["win_rate_pct"],
                                      sim["losing_pct"], sim["worst_decile_pct"])
                log.info("  (250k/500k/1m bankrolls are pure linear scaling of the above under equal-weighting "
                          "and are omitted -- we have no historical BIN-depth data to model fill capacity "
                          "differently at larger size, and fabricating one would be worse than omitting it.)")

        log.info("=" * 100)
        log.info("PHASE 8B COMPLETE. No-lookahead statement: every signal above reads only rows[0..i] "
                  "(today and earlier) at evaluation time i. The realized bottom date is computed from the "
                  "full series and used ONLY to grade signals after the fact (days_from_bottom, "
                  "pre_bottom_frac, perfect-hindsight benchmark) -- it is never an input to any signal's "
                  "own firing condition.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
