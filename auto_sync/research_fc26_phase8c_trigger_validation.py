"""
Phase 8C -- validation pass on the Phase 8B drawdown+deceleration trigger,
before any FC27 Investment Engine gets designed around it.

Research-only, read-only. Reuses research_fc26_phase8_investment_analysis
(data loading, cohort construction) and research_fc26_phase8b_bottom_
trigger_analysis (cohort daily reconstruction, signal evaluation,
no-lookahead primitives) -- nothing about cohort assignment or the
trailing-window/signal-grading logic is redefined here.

Run the same Coolify one-off way as every other research_fc26_*.py
script:

    python research_fc26_phase8c_trigger_validation.py

WHAT THIS ANSWERS (not repeating Phase 8/8B's broad analysis):
  1. Reconciles every "bottom"/"reversal" date across cohorts under one
     consistent definition, so FR+5/FR+7/FR+9/FR+10 stop looking like
     contradictions.
  2 & 3. Sweeps the drawdown threshold (35-60%) x deceleration duration
     (1-3 days) to see whether there's a STABLE region that works, not
     a single lucky number.
  4. Re-tests cohort-only vs card-only vs cohort-gate-plus-card-confirm
     selection using the actual winning drawdown+deceleration family
     (Phase 8B's card-vs-cohort test used an inferior signal).
  6. Tests whether the trigger identifies a multi-day REGIME or a single
     lucky date.
  7. Tests trailing-only exit signals against fixed 2/3/5-day exits.

Every signal below reads only rows[0..i] (today and earlier). A card or
cohort's REALIZED bottom is computed from the full series but used only
to grade signals after the fact -- never as an input to any signal's own
firing condition. See phase8b's module docstring for the same statement;
it applies identically here.
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
    FULL_RELEASE,
    load_data,
    build_reference_attrs,
    build_cohorts,
    compute_cell,
    roi_stats,
    price_on_exact,
)
from research_fc26_phase8b_bottom_trigger_analysis import (
    ANALYSIS_START,
    DETAIL_END,
    MIN_FIRE_COHORT_N,
    build_cohort_day_series,
    cohort_series_index,
    realized_bottom,
    is_new_low,
    evaluate_signal,
    max_adverse_excursion,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("phase8c")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

COMPARE_COHORTS = ["premium meta", "mid-tier meta", "desirable starter", "ordinary non-meta"]
DD_THRESHOLDS = [35, 40, 45, 50, 55, 60]
DECEL_DAYS = [1, 2, 3]
REGIME_HORIZON = 10  # days after first fire to probe for how long the window stays open


# ---------------------------------------------------------------------------
# Section 1 -- reconciled bottom/reversal terminology
# ---------------------------------------------------------------------------
def breadth_reversal_date(rows: list[dict], min_consec: int = 2) -> date | None:
    """First date breadth_spread > 0 for `min_consec` consecutive days --
    a sustained reversal, not one noisy up-day. NO LOOKAHEAD at decision
    time; used here purely descriptively (evaluation), same as bottom."""
    for i in range(len(rows) - min_consec + 1):
        if all(rows[i + k]["breadth_spread"] > 0 for k in range(min_consec)):
            return rows[i]["date"]
    return None


def best_hindsight_entry(cohort_ids, prices, rows, holding=3) -> tuple[date | None, float | None]:
    best_day, best_median = None, None
    for r in rows:
        if r["n"] < MIN_FIRE_COHORT_N:
            continue
        stats = compute_cell(cohort_ids, prices, r["date"], holding)
        if stats.get("n", 0) < MIN_FIRE_COHORT_N:
            continue
        if best_median is None or stats["median"] > best_median:
            best_median, best_day = stats["median"], r["date"]
    return best_day, best_median


# ---------------------------------------------------------------------------
# Drawdown + deceleration signal family, parametrized (no magic 40%)
# ---------------------------------------------------------------------------
def decel_for_days(rows, i, days) -> bool:
    """NO LOOKAHEAD. `days` consecutive days of deceleration = `days`
    pairwise comparisons (today's decline smaller than yesterday's, etc),
    i.e. days+1 return readings in a strictly improving chain."""
    if i < days:
        return False
    for k in range(days):
        a, b = rows[i - k]["ret1d"], rows[i - k - 1]["ret1d"]
        if not (isinstance(a, float) and isinstance(b, float) and a == a and b == b and a > b):
            return False
    return True


def make_signal(dd: float, days: int):
    def fn(rows, i):
        return rows[i]["drawdown_from_peak"] >= dd and decel_for_days(rows, i, days)
    return fn


def get_fire_dates(fn, rows, min_i=7) -> list[date]:
    return [rows[i]["date"] for i in range(min_i, len(rows))
            if rows[i]["n"] >= MIN_FIRE_COHORT_N and fn(rows, i)]


# ---------------------------------------------------------------------------
# Per-card day series (mirrors build_cohort_day_series's price/return/
# drawdown fields, but for one card -- used for the card-level tests)
# ---------------------------------------------------------------------------
def build_card_day_series(series: dict[date, int]) -> list[dict]:
    dates = sorted(d for d in series if ANALYSIS_START <= d <= DETAIL_END)
    rows = []
    peak = None
    for i, d in enumerate(dates):
        p = series[d]
        if p <= 0:
            continue
        peak = p if peak is None else max(peak, p)
        prev = series.get(dates[i - 1]) if i > 0 else None
        ret1d = (p - prev) / prev * 100 if prev else float("nan")
        rows.append({
            "date": d, "price": p, "ret1d": ret1d,
            "drawdown_from_peak": (peak - p) / peak * 100 if peak else 0.0,
        })
    return rows


# ---------------------------------------------------------------------------
# Section 2/3 -- threshold x duration sweep
# ---------------------------------------------------------------------------
def sweep_thresholds(cohort_ids, prices, rows, bottom_date):
    log.info("--- threshold x deceleration-duration sweep (premium meta) ---")
    log.info("%-5s %-5s %10s %6s %8s %8s %6s | %8s %8s %8s %8s %8s | %7s",
              "dd%", "days", "1st_fire", "relFR", "d_from_bt", "pre_bt#", "n",
              "roi1d", "roi2d", "roi3d", "roi5d", "roi7d", "mae14%")
    results = {}
    for dd in DD_THRESHOLDS:
        for days in DECEL_DAYS:
            fn = make_signal(dd, days)
            r = evaluate_signal(f"dd{dd}_decel{days}d", fn, rows, cohort_ids, prices, bottom_date)
            results[(dd, days)] = r
            if r is None:
                log.info("%-5d %-5d %10s %6s %8s %8s %6s | %8s %8s %8s %8s %8s | %7s",
                          dd, days, "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-")
                continue
            first = r["per_fire"][0]
            rel_fr = (r["first_fire"] - FULL_RELEASE).days
            n_first = first["roi_1d"].get("n", 0)
            log.info("%-5d %-5d %10s %+6d %8s %8d %6d | %8s %8s %8s %8s %8s | %7s",
                      dd, days, r["first_fire"].isoformat(), rel_fr,
                      r["days_from_bottom_first_fire"], r["pre_bottom_fires"], n_first,
                      first["roi_1d"].get("median"), first["roi_2d"].get("median"),
                      first["roi_3d"].get("median"), first["roi_5d"].get("median"),
                      first["roi_7d"].get("median"),
                      round(first["mae14"], 2) if first["mae14"] is not None else None)
    log.info("  full stats (win%%/p25/downside_p10/worst) for each threshold's FIRST fire, 3d hold:")
    log.info("%-5s %-5s %8s %8s %8s %8s %8s", "dd%", "days", "win%", "p25", "p10dn", "worst", "mean")
    for (dd, days), r in results.items():
        if r is None:
            continue
        f3 = r["per_fire"][0]["roi_3d"]
        log.info("%-5d %-5d %8s %8s %8s %8s %8s", dd, days,
                  f3.get("win_rate"), f3.get("p25"), f3.get("downside_p10"), f3.get("worst"), f3.get("mean"))
    return results


# ---------------------------------------------------------------------------
# Section 4 -- cohort-only vs card-only vs cohort-gate+card-confirm
# ---------------------------------------------------------------------------
def approach_a_cohort_only(cohort_ids, prices, fire_dates):
    by_h = {}
    for h in (1, 2, 3, 5, 7):
        rois = []
        for d in fire_dates:
            stats = compute_cell(cohort_ids, prices, d, h)
            if stats.get("n", 0) >= MIN_FIRE_COHORT_N:
                # weight by re-deriving individual ROIs isn't available from compute_cell's
                # summary alone, so approximate the pooled distribution via per-fire medians
                by_h.setdefault(h, []).append(stats)
    return {h: _pool_fire_stats(v) for h, v in by_h.items()}


def _pool_fire_stats(fire_stat_list):
    meds = [s["median"] for s in fire_stat_list if s.get("n", 0) > 0]
    wins = [s["win_rate"] for s in fire_stat_list if s.get("n", 0) > 0]
    ns = [s["n"] for s in fire_stat_list if s.get("n", 0) > 0]
    if not meds:
        return {"n_fires": 0}
    return {
        "n_fires": len(meds), "avg_n_per_fire": round(statistics.mean(ns), 1),
        "median_of_medians": round(statistics.median(meds), 2),
        "median_win_rate": round(statistics.median(wins), 1),
    }


def approach_b_card_only(premium_ids, prices, card_rows_by_cid, dd, days):
    fn = make_signal(dd, days)
    rois = {h: [] for h in (1, 2, 3, 5, 7)}
    n_instances = 0
    for cid, rows in card_rows_by_cid.items():
        series = prices[cid]
        for i in range(days, len(rows)):
            if fn(rows, i):
                n_instances += 1
                d = rows[i]["date"]
                for h in rois:
                    exit_ = price_on_exact(series, d + timedelta(days=h))
                    entry = rows[i]["price"]
                    if exit_ and entry:
                        rois[h].append((exit_ * 0.95 - entry) / entry)
    return n_instances, {h: roi_stats(v) for h, v in rois.items()}


def approach_c_hybrid(premium_ids, prices, cohort_rows, card_rows_by_cid, dd, days):
    cohort_index = cohort_series_index(cohort_rows)
    fn = make_signal(dd, days)
    regime_dates = get_fire_dates(fn, cohort_rows)

    confirmations = {
        "card_also_decelerating": lambda cr, cid_rows, i: (
            i >= 1 and cid_rows[i]["ret1d"] > cid_rows[i - 1]["ret1d"]
        ),
        "card_not_new_3d_low": lambda cr, cid_rows_full_series, cid, d: not is_new_low(cid_rows_full_series, d, 3),
        "card_dd_ge_cohort_dd": lambda cr, cid_rows, i: cid_rows[i]["drawdown_from_peak"] >= cr["drawdown_from_peak"],
        "card_flat_or_up": lambda cr, cid_rows, i: cid_rows[i]["ret1d"] >= -0.5,
        "card_outperforms_cohort_1d": lambda cr, cid_rows, i: cid_rows[i]["ret1d"] > cr["ret1d"],
    }

    out = {}
    for name in confirmations:
        rois = {h: [] for h in (1, 2, 3, 5, 7)}
        n_instances = 0
        for d in regime_dates:
            cr = cohort_index.get(d)
            if cr is None:
                continue
            for cid, rows in card_rows_by_cid.items():
                idx = next((i for i, r in enumerate(rows) if r["date"] == d), None)
                if idx is None:
                    continue
                series = prices[cid]
                if name == "card_not_new_3d_low":
                    ok = confirmations[name](cr, series, cid, d)
                else:
                    ok = confirmations[name](cr, rows, idx)
                if not ok:
                    continue
                n_instances += 1
                entry = rows[idx]["price"]
                for h in rois:
                    exit_ = price_on_exact(series, d + timedelta(days=h))
                    if exit_ and entry:
                        rois[h].append((exit_ * 0.95 - entry) / entry)
        out[name] = (n_instances, {h: roi_stats(v) for h, v in rois.items()})
    return out


# ---------------------------------------------------------------------------
# Section 6 -- regime duration
# ---------------------------------------------------------------------------
def probe_regime_duration(cohort_ids, prices, first_fire, horizon=REGIME_HORIZON):
    log.info("--- regime duration: fresh entry on each day after first fire, still profitable? ---")
    log.info("%-11s %6s %8s %8s %8s %8s", "entry_day", "d+N", "roi2d_md", "win2d", "roi3d_md", "win3d")
    still_open_days = 0
    for n in range(0, horizon + 1):
        d = first_fire + timedelta(days=n)
        s2 = compute_cell(cohort_ids, prices, d, 2)
        s3 = compute_cell(cohort_ids, prices, d, 3)
        if s2.get("n", 0) < MIN_FIRE_COHORT_N:
            break
        profitable = (s3.get("median") or -999) > 0 and (s3.get("win_rate") or 0) >= 50
        if profitable:
            still_open_days = n + 1
        log.info("%-11s %+6d %8s %8s %8s %8s%s", d.isoformat(), n,
                  s2.get("median"), s2.get("win_rate"), s3.get("median"), s3.get("win_rate"),
                  "  <- still profitable" if profitable else "")
    log.info("  window stayed attractive (median 3d ROI>0 AND win>=50%%) for approximately %d "
              "consecutive day(s) of fresh entries after first fire (daily-data resolution -- "
              "cannot distinguish an hours-long window from a full day within a single date).",
              still_open_days)
    return still_open_days


# ---------------------------------------------------------------------------
# Section 7 -- exit signal testing
# ---------------------------------------------------------------------------
def test_exit_signals(cohort_ids, prices, cohort_rows, entry_day, horizon=14):
    cohort_index = cohort_series_index(cohort_rows)
    entry_stats_by_h = {h: compute_cell(cohort_ids, prices, entry_day, h) for h in (2, 3, 5)}
    log.info("--- fixed-hold baselines from entry=%s ---", entry_day.isoformat())
    for h, s in entry_stats_by_h.items():
        log.info("  fixed %dd exit: median=%s%% win=%s%% n=%s", h, s.get("median"), s.get("win_rate"), s.get("n"))

    def cumret(d):
        r0 = cohort_index.get(entry_day)
        r = cohort_index.get(d)
        if not r0 or not r:
            return None
        return (r["median"] - r0["median"]) / r0["median"] * 100

    rules = {
        "breadth_turns_negative": lambda i, dates: cohort_index[dates[i]]["breadth_spread"] < 0,
        "pct_falling_overtakes_rising": lambda i, dates: (
            cohort_index[dates[i]]["pct_falling"] > cohort_index[dates[i]]["pct_rising"]
        ),
        "negative_day_after_any_gain": lambda i, dates: (
            i >= 1 and cohort_index[dates[i]]["ret1d"] < 0 and any(
                cohort_index[dates[j]]["ret1d"] > 0 for j in range(0, i)
            )
        ),
        "momentum_deteriorates_2d": lambda i, dates: (
            i >= 1 and cohort_index[dates[i]]["ret1d"] < cohort_index[dates[i - 1]]["ret1d"] < 0
        ),
        "profit_target_5pct": lambda i, dates: (cumret(dates[i]) or -999) >= 5.0,
    }

    all_dates = [entry_day + timedelta(days=n) for n in range(0, horizon + 1) if entry_day + timedelta(days=n) in cohort_index]
    log.info("--- dynamic exit rules from the same entry ---")
    for name, rule in rules.items():
        exit_day, exit_offset = None, None
        for i in range(1, len(all_dates)):
            if rule(i, all_dates):
                exit_day, exit_offset = all_dates[i], i
                break
        if exit_day is None:
            log.info("  %-30s never triggered within %dd -- would still be held", name, horizon)
            continue
        stats = compute_cell(cohort_ids, prices, entry_day, exit_offset)
        log.info("  %-30s exit=%s (entry+%dd) median=%s%% win=%s%% n=%s",
                  name, exit_day.isoformat(), exit_offset, stats.get("median"), stats.get("win_rate"), stats.get("n"))


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
            if label in COMPARE_COHORTS:
                cohort_pools[label].add(cid)
        cohort_pools["ALL gold common+rare"] = set(prices.keys())

        # ------------------------------------------------------------- 1
        log.info("=" * 100)
        log.info("SECTION 1 -- RECONCILED BOTTOM/REVERSAL TERMINOLOGY")
        log.info("=" * 100)
        rows_by_cohort = {}
        for name in ["ALL gold common+rare"] + COMPARE_COHORTS:
            ids = cohort_pools[name]
            if len(ids) < MIN_FIRE_COHORT_N:
                continue
            rows_by_cohort[name] = build_cohort_day_series(ids, prices)

        for name, rows in rows_by_cohort.items():
            bottom = realized_bottom(rows, ANALYSIS_START, DETAIL_END)
            reversal = breadth_reversal_date(rows)
            if bottom:
                log.info("  %-24s realized bottom: %s (FR%+d, %.1f%% below peak)", name,
                          bottom["date"].isoformat(), bottom["rel_fr"], bottom["drawdown_from_peak"])
            if reversal:
                log.info("  %-24s breadth reversal (2 consecutive positive-spread days): %s (FR%+d)",
                          name, reversal.isoformat(), (reversal - FULL_RELEASE).days)

        premium_ids = cohort_pools["premium meta"]
        premium_rows = rows_by_cohort.get("premium meta", [])
        premium_bottom = realized_bottom(premium_rows, ANALYSIS_START, DETAIL_END)
        best_day, best_median = best_hindsight_entry(premium_ids, prices, premium_rows, holding=3)
        log.info("  premium meta best-hindsight entry (max median 3d ROI across all entry days): "
                  "%s (FR%+d), median=%.2f%%",
                  best_day.isoformat() if best_day else "-",
                  (best_day - FULL_RELEASE).days if best_day else 0, best_median or 0)

        # ------------------------------------------------------------- 2/3
        log.info("=" * 100)
        log.info("SECTION 2/3 -- DRAWDOWN THRESHOLD x DECELERATION DURATION SWEEP")
        log.info("=" * 100)
        sweep_results = sweep_thresholds(premium_ids, prices, premium_rows,
                                          premium_bottom["date"] if premium_bottom else None)

        # Pick the smallest threshold (any duration) with ZERO pre-bottom false
        # fires as the "robust primary" signal for the rest of this script --
        # deliberately not hardcoding 40%/2 days.
        candidates = [(dd, days, r) for (dd, days), r in sweep_results.items()
                      if r is not None and r["pre_bottom_fires"] == 0]
        if candidates:
            candidates.sort(key=lambda t: (t[0], t[1]))
            best_dd, best_days, _ = candidates[0]
        else:
            best_dd, best_days = 40, 2
        log.info("  robust primary signal selected for sections 4/6/7: drawdown>=%d%% + decel %dd "
                  "(smallest threshold in the sweep with zero pre-bottom false fires)", best_dd, best_days)

        primary_fn = make_signal(best_dd, best_days)
        primary_fire_dates = get_fire_dates(primary_fn, premium_rows)
        if not primary_fire_dates:
            log.info("  no fires for the selected primary signal -- aborting sections 4/6/7")
            return 0
        primary_first_fire = primary_fire_dates[0]

        # ------------------------------------------------------------- 4
        log.info("=" * 100)
        log.info("SECTION 4 -- COHORT-ONLY vs CARD-ONLY vs COHORT-GATE+CARD-CONFIRM")
        log.info("primary signal: drawdown>=%d%% + decel %dd, all %d fire date(s): %s", best_dd, best_days,
                  len(primary_fire_dates), [d.isoformat() for d in primary_fire_dates])
        log.info("=" * 100)

        a_stats = approach_a_cohort_only(premium_ids, prices, primary_fire_dates)
        log.info("--- A: cohort-only (buy whole qualifying cohort on each fire date) ---")
        for h, s in a_stats.items():
            log.info("  hold=%dd n_fires=%s avg_n/fire=%s median_of_medians=%s%% median_win=%s%%",
                      h, s.get("n_fires"), s.get("avg_n_per_fire"), s.get("median_of_medians"), s.get("median_win_rate"))

        card_rows_by_cid = {cid: build_card_day_series(prices[cid]) for cid in premium_ids if cid in prices}
        n_b, b_stats = approach_b_card_only(premium_ids, prices, card_rows_by_cid, best_dd, best_days)
        log.info("--- B: card-only (each card independently satisfies the same drawdown+decel rule) ---")
        log.info("  %d (card, date) firing instances", n_b)
        for h, s in b_stats.items():
            log.info("  hold=%dd n=%s median=%s%% mean=%s%% win=%s%% p25=%s%% downside_p10=%s%% worst=%s%%",
                      h, s.get("n"), s.get("median"), s.get("mean"), s.get("win_rate"),
                      s.get("p25"), s.get("downside_p10"), s.get("worst"))

        c_stats = approach_c_hybrid(premium_ids, prices, premium_rows, card_rows_by_cid, best_dd, best_days)
        log.info("--- C: cohort gate + individual card confirmation (5 confirmation rules tested separately) ---")
        for name, (n_c, by_h) in c_stats.items():
            log.info("  confirmation=%-28s n_instances=%d", name, n_c)
            for h in (1, 2, 3, 5, 7):
                s = by_h[h]
                log.info("    hold=%dd n=%s median=%s%% win=%s%% downside_p10=%s%%",
                          h, s.get("n"), s.get("median"), s.get("win_rate"), s.get("downside_p10"))

        # ------------------------------------------------------------- 6
        log.info("=" * 100)
        log.info("SECTION 6 -- REGIME DURATION")
        log.info("=" * 100)
        probe_regime_duration(premium_ids, prices, primary_first_fire)

        # ------------------------------------------------------------- 7
        log.info("=" * 100)
        log.info("SECTION 7 -- EXIT SIGNAL TESTING")
        log.info("=" * 100)
        test_exit_signals(premium_ids, prices, premium_rows, primary_first_fire)

        log.info("=" * 100)
        log.info("PHASE 8C COMPLETE. No-lookahead statement identical to Phase 8B: every signal reads "
                  "only rows[0..i]; realized bottoms/reversals are evaluation-only and never feed a "
                  "signal's own firing condition. The 'robust primary' drawdown threshold above was "
                  "selected programmatically (smallest sweep value with zero pre-bottom false fires), "
                  "not assumed to be 40%%.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
