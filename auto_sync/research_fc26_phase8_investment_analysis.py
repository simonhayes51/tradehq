"""
Phase 8 -- FC26 launch investment-behaviour analysis.

Research-only, read-only. Reads fc26_historical_daily_prices (Phase 5)
joined to futgg_players (canonical metadata, never duplicated) and prints
an aggregated launch-investment report to stdout/logs. Writes nothing to
any table.

Run the same Coolify one-off way as every other research_fc26_*.py
script (see research/fc26_launch_market/tools/README.md): temporarily
override PlayerSync's start command to

    python research_fc26_phase8_investment_analysis.py

trigger a manual run, and copy the logs back. Revert the start command
afterward, same as every prior phase.

SCOPE (first Phase 8 cut, per explicit brief): FC26 launch investment
behaviour -- when/where a trader holding coins in the first days/weeks
could have bought and later sold for after-tax profit. NOT primarily a
snipe/mispricing study.

FC26 LAUNCH TIMELINE (sourced, not guessed -- see WebSearch citations in
the commit message that introduced this script):
    Web App launch            2025-09-17
    Companion App refresh     2025-09-18
    Early Access (Ultimate)   2025-09-19
    Full Release              2025-09-26
This lines up with the data: the earliest reconstructed price date across
the whole dataset is 2025-09-15..09-17 (chart's own first labeled tick),
i.e. the data starts right at Web App launch, not earlier -- fut.gg has
no FC26 price history before its own Web App went live.

NO FUTURE LEAKAGE (see report footer for the full statement): every
cohort a card belongs to (price band, rating band, position, "meta tier")
is assigned from data available AT OR BEFORE that card's first observed
price date, never from later price performance. Every entry/exit return
uses only the entry day's price and the (already-known, backward-looking)
cohort label; nothing about a setup's later cohort-level performance
feeds back into which cohort a card was placed in.
"""
from __future__ import annotations

import asyncio
import logging
import os
import statistics
from collections import defaultdict
from datetime import date, timedelta

from db_connect import connect_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("phase8")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

EA_TAX = 0.05
MIN_COHORT_N = 30  # below this, a cohort's stats are too thin to trust/act on

# ---------------------------------------------------------------------------
# 1. FC26 launch timeline
# ---------------------------------------------------------------------------
WEB_APP_LAUNCH = date(2025, 9, 17)
COMPANION_APP_REFRESH = date(2025, 9, 18)
EARLY_ACCESS = date(2025, 9, 19)
FULL_RELEASE = date(2025, 9, 26)
FIRST_WEEKEND = date(2025, 9, 27)  # Saturday immediately after Full Release

TIMELINE = [
    ("data start (first reconstructable chart date)", None),  # filled in from real data at runtime
    ("web_app_launch", WEB_APP_LAUNCH),
    ("companion_app_refresh", COMPANION_APP_REFRESH),
    ("early_access", EARLY_ACCESS),
    ("full_release", FULL_RELEASE),
    ("first_weekend_after_full_release", FIRST_WEEKEND),
    ("full_release_plus_3", FULL_RELEASE + timedelta(days=3)),
    ("full_release_plus_7", FULL_RELEASE + timedelta(days=7)),
    ("full_release_plus_14", FULL_RELEASE + timedelta(days=14)),
]

ENTRY_DAYS = [WEB_APP_LAUNCH + timedelta(days=i) for i in range(0, 42)]  # ~6 launch weeks
HOLDING_PERIODS = [1, 2, 3, 5, 7, 10, 14]
FR_OFFSETS = [-7, -5, -3, -2, -1, 0, 1, 2, 3, 5, 7]

POSITION_GROUPS = {
    "GK": "GK",
    "CB": "CB", "SW": "CB",
    "LB": "Fullback", "RB": "Fullback", "LWB": "Fullback", "RWB": "Fullback",
    "CDM": "Central Midfield", "CM": "Central Midfield", "CAM": "Central Midfield",
    "LM": "Wide Attacker", "RM": "Wide Attacker", "LW": "Wide Attacker", "RW": "Wide Attacker",
    "CF": "Striker", "ST": "Striker",
}


def position_group(pos: str | None) -> str:
    return POSITION_GROUPS.get((pos or "").strip().upper(), "Other")


# ---------------------------------------------------------------------------
# Stats helpers (pure Python -- no numpy/pandas in this image on purpose;
# adding a heavy dependency to requirements.txt would also affect the
# production PlayerSync image, which shares this file)
# ---------------------------------------------------------------------------
def pct(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def roi_stats(rois: list[float]) -> dict:
    if not rois:
        return {"n": 0}
    s = sorted(rois)
    wins = sum(1 for r in rois if r > 0)
    return {
        "n": len(rois),
        "median": round(statistics.median(s) * 100, 2),
        "mean": round((sum(s) / len(s)) * 100, 2),
        "win_rate": round(wins / len(s) * 100, 1),
        "p25": round(pct(s, 0.25) * 100, 2),
        "p75": round(pct(s, 0.75) * 100, 2),
        "downside_p10": round(pct(s, 0.10) * 100, 2),
        "worst": round(s[0] * 100, 2),
    }


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
async def load_data(conn):
    price_rows = await conn.fetch(
        """
        SELECT card_id, observed_date, price
        FROM fc26_historical_daily_prices
        ORDER BY card_id, observed_date
        """
    )
    meta_rows = await conn.fetch(
        """
        SELECT source_card_id, name, rating, primary_position, rarity,
               price_tier, league, nation, height_cm, weight_kg, foot,
               skill_moves, weak_foot, accelerate_type, body_type,
               pace, shooting, passing, dribbling, defending, physicality
        FROM futgg_players
        WHERE price_tier IN ('gold_common', 'gold_rare')
        """
    )
    prices: dict[int, dict[date, int]] = defaultdict(dict)
    for r in price_rows:
        prices[r["card_id"]][r["observed_date"]] = r["price"]
    meta = {r["source_card_id"]: dict(r) for r in meta_rows if r["source_card_id"] in prices}
    # Drop price series for cards we have no metadata for (shouldn't happen --
    # fc26_historical_daily_prices FK's to futgg_players.source_card_id --
    # but keep the two collections consistent defensively.)
    prices = {cid: series for cid, series in prices.items() if cid in meta}
    return prices, meta


def price_on_exact(series: dict[date, int], d: date) -> int | None:
    return series.get(d)


def entry_exit_roi(series: dict[date, int], entry_day: date, holding: int) -> float | None:
    entry = price_on_exact(series, entry_day)
    if entry is None or entry <= 0:
        return None
    exit_ = price_on_exact(series, entry_day + timedelta(days=holding))
    if exit_ is None:
        return None
    return (exit_ * (1 - EA_TAX) - entry) / entry


# ---------------------------------------------------------------------------
# Section 1 -- timeline
# ---------------------------------------------------------------------------
def section1_timeline(prices: dict) -> None:
    all_dates = sorted({d for series in prices.values() for d in series})
    data_start, data_end = all_dates[0], all_dates[-1]
    log.info("=" * 100)
    log.info("SECTION 1 -- FC26 LAUNCH TIMELINE")
    log.info("=" * 100)
    log.info("  data start (earliest reconstructed price, any card): %s", data_start)
    log.info("  data end (most recent reconstructed price, any card): %s", data_end)
    for label, d in TIMELINE[1:]:
        rel_data_start = (d - data_start).days
        rel_full_release = (d - FULL_RELEASE).days
        log.info("  %-38s %s  (day %+d from data start, day %+d from Full Release)",
                  label, d, rel_data_start, rel_full_release)


# ---------------------------------------------------------------------------
# Section 2 -- whole gold market, day by day
# ---------------------------------------------------------------------------
def section2_market_overview(prices: dict, meta: dict) -> None:
    log.info("=" * 100)
    log.info("SECTION 2 -- WHOLE GOLD MARKET, LAUNCH WINDOW")
    log.info("=" * 100)
    rare_ids = {cid for cid, m in meta.items() if m["price_tier"] == "gold_rare"}
    common_ids = {cid for cid, m in meta.items() if m["price_tier"] == "gold_common"}

    header = (f"{'date':10} {'rel_FR':>7} {'all_med':>9} {'rare_med':>9} {'common_med':>10} "
              f"{'ret_med%':>9} {'%up':>6} {'%dn':>6} {'%flat':>6} {'p10':>8} {'p90':>8} {'stdev%':>8} {'n':>6}")
    log.info(header)

    prev_prices: dict[int, int] = {}
    end_day = FULL_RELEASE + timedelta(days=21)
    d = WEB_APP_LAUNCH
    while d <= end_day:
        day_prices = {}
        for cid, series in prices.items():
            p = price_on_exact(series, d)
            if p is not None and p > 0:
                day_prices[cid] = p
        if not day_prices:
            d += timedelta(days=1)
            continue

        all_vals = sorted(day_prices.values())
        rare_vals = sorted(v for cid, v in day_prices.items() if cid in rare_ids)
        common_vals = sorted(v for cid, v in day_prices.items() if cid in common_ids)

        returns = []
        rising = falling = flat = 0
        for cid, p in day_prices.items():
            prev = prev_prices.get(cid)
            if prev is not None and prev > 0:
                r = (p - prev) / prev
                returns.append(r)
                if r > 0.01:
                    rising += 1
                elif r < -0.01:
                    falling += 1
                else:
                    flat += 1

        ret_med = statistics.median(returns) * 100 if returns else float("nan")
        total_moved = rising + falling + flat
        pct_up = rising / total_moved * 100 if total_moved else float("nan")
        pct_dn = falling / total_moved * 100 if total_moved else float("nan")
        pct_flat = flat / total_moved * 100 if total_moved else float("nan")
        stdev = statistics.pstdev(returns) * 100 if len(returns) > 1 else float("nan")

        log.info(
            "%-10s %+7d %9.0f %9.0f %10.0f %9.2f %6.1f %6.1f %6.1f %8.0f %8.0f %8.2f %6d",
            d.isoformat(), (d - FULL_RELEASE).days,
            statistics.median(all_vals),
            statistics.median(rare_vals) if rare_vals else float("nan"),
            statistics.median(common_vals) if common_vals else float("nan"),
            ret_med, pct_up, pct_dn, pct_flat,
            pct(all_vals, 0.10), pct(all_vals, 0.90), stdev, len(day_prices),
        )
        prev_prices = day_prices
        d += timedelta(days=1)

    log.info("  (ret_med = median day-over-day price change across cards with both days' "
              "prices, as a percent; up/down/flat use a +-1% dead-band; look for the run of "
              "large negative ret_med / high pct-down = the launch sell-off, and where ret_med "
              "turns positive again with pct-up rising = the rebound. A second dip after that = "
              "secondary sell-off.)")


# ---------------------------------------------------------------------------
# Section 3 -- entry-day x holding-period investment-return matrix
# ---------------------------------------------------------------------------
def compute_cell(card_ids, prices, entry_day, holding) -> dict:
    rois = []
    for cid in card_ids:
        series = prices.get(cid)
        if not series:
            continue
        r = entry_exit_roi(series, entry_day, holding)
        if r is not None:
            rois.append(r)
    return roi_stats(rois)


def section3_return_matrix(prices: dict, meta: dict) -> dict:
    log.info("=" * 100)
    log.info("SECTION 3 -- ENTRY-DAY x HOLDING-PERIOD INVESTMENT-RETURN MATRIX (all gold common/rare)")
    log.info("5% EA tax applied on exit. ROI shown as a percent.")
    log.info("=" * 100)

    all_ids = list(prices.keys())
    matrix: dict[tuple[date, int], dict] = {}
    for entry_day in ENTRY_DAYS:
        for holding in HOLDING_PERIODS:
            matrix[(entry_day, holding)] = compute_cell(all_ids, prices, entry_day, holding)

    for metric, fmt in [("median", "median after-tax ROI %"), ("win_rate", "win rate %"), ("n", "sample size")]:
        log.info("--- %s ---", fmt)
        log.info("%-12s %-8s " + " ".join(f"h={h:<7}" for h in HOLDING_PERIODS),
                  "entry_day", "rel_FR")
        for entry_day in ENTRY_DAYS:
            row = [matrix[(entry_day, h)].get(metric, float("nan")) for h in HOLDING_PERIODS]
            log.info("%-12s %+8d " + " ".join(f"{v!s:<9}" for v in row),
                      entry_day.isoformat(), (entry_day - FULL_RELEASE).days)

    return matrix


# ---------------------------------------------------------------------------
# Cohort construction (all reference info is entry-time-available; see
# module docstring's no-leakage statement)
# ---------------------------------------------------------------------------
def build_reference_attrs(prices: dict, meta: dict) -> dict[int, dict]:
    """One row per card: its first observed price/date + static metadata.
    This is the ONLY information used to assign a card to any cohort --
    never a later price or later performance."""
    ref: dict[int, dict] = {}
    for cid, series in prices.items():
        if not series:
            continue
        first_date = min(series)
        first_price = series[first_date]
        m = meta[cid]
        ref[cid] = {
            "first_date": first_date,
            "first_price": first_price,
            "rating": m["rating"],
            "position_group": position_group(m["primary_position"]),
            "price_tier": m["price_tier"],
            "league": m["league"],
            "nation": m["nation"],
            "pace": m["pace"], "defending": m["defending"], "physicality": m["physicality"],
            "height_cm": m["height_cm"], "body_type": m["body_type"],
            "accelerate_type": m["accelerate_type"], "skill_moves": m["skill_moves"],
            "weak_foot": m["weak_foot"],
        }
    return ref


def build_cohorts(ref: dict[int, dict]) -> dict[str, dict[int, str]]:
    """Returns {dimension_name: {card_id: bucket_label}}."""
    cohorts: dict[str, dict[int, str]] = {}

    # --- price bands, data-driven quantile cutoffs off first_price ---
    prices_sorted = sorted(v["first_price"] for v in ref.values())
    edges = {p: pct(prices_sorted, p) for p in (0.10, 0.25, 0.50, 0.75, 0.90)}
    log.info("  price-band cutoffs (data-driven, from launch-time first observed price): "
             "p10=%.0f p25=%.0f p50=%.0f p75=%.0f p90=%.0f", *[edges[p] for p in (0.10, 0.25, 0.50, 0.75, 0.90)])

    def price_band(v):
        if v < edges[0.10]:
            return f"<{edges[0.10]:.0f} (discard/very cheap)"
        if v < edges[0.25]:
            return f"{edges[0.10]:.0f}-{edges[0.25]:.0f}"
        if v < edges[0.50]:
            return f"{edges[0.25]:.0f}-{edges[0.50]:.0f}"
        if v < edges[0.75]:
            return f"{edges[0.50]:.0f}-{edges[0.75]:.0f}"
        if v < edges[0.90]:
            return f"{edges[0.75]:.0f}-{edges[0.90]:.0f}"
        return f">{edges[0.90]:.0f} (premium)"

    cohorts["price_band"] = {cid: price_band(v["first_price"]) for cid, v in ref.items()}

    # --- rating bands, data-driven quartiles ---
    ratings_sorted = sorted(v["rating"] for v in ref.values() if v["rating"] is not None)
    r_edges = {p: pct(ratings_sorted, p) for p in (0.25, 0.50, 0.75)}
    log.info("  rating-band cutoffs (quartiles): p25=%.0f p50=%.0f p75=%.0f",
              r_edges[0.25], r_edges[0.50], r_edges[0.75])

    def rating_band(v):
        if v is None:
            return "unknown"
        if v < r_edges[0.25]:
            return f"<{r_edges[0.25]:.0f}"
        if v < r_edges[0.50]:
            return f"{r_edges[0.25]:.0f}-{r_edges[0.50]:.0f}"
        if v < r_edges[0.75]:
            return f"{r_edges[0.50]:.0f}-{r_edges[0.75]:.0f}"
        return f">={r_edges[0.75]:.0f}"

    cohorts["rating_band"] = {cid: rating_band(v["rating"]) for cid, v in ref.items()}

    # --- position group ---
    cohorts["position_group"] = {cid: v["position_group"] for cid, v in ref.items()}

    # --- price tier (gold common vs gold rare) ---
    cohorts["price_tier"] = {cid: v["price_tier"] for cid, v in ref.items()}

    # --- meta tier: within-position-group price percentile at launch ---
    by_pos: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for cid, v in ref.items():
        by_pos[v["position_group"]].append((cid, v["first_price"]))
    meta_tier: dict[int, str] = {}
    for pos, items in by_pos.items():
        items_sorted = sorted(items, key=lambda t: t[1])
        n = len(items_sorted)
        for rank, (cid, _price) in enumerate(items_sorted):
            pctile = rank / max(n - 1, 1)
            if pctile >= 0.90:
                meta_tier[cid] = "premium meta"
            elif pctile >= 0.70:
                meta_tier[cid] = "mid-tier meta"
            elif pctile >= 0.45:
                meta_tier[cid] = "desirable starter"
            elif pctile >= 0.20:
                meta_tier[cid] = "ordinary non-meta"
            else:
                meta_tier[cid] = "likely fodder"
    cohorts["meta_tier"] = meta_tier

    # --- numeric attribute buckets, within-position-group terciles ---
    for attr in ("pace", "defending", "physicality", "height_cm"):
        by_pos_attr: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for cid, v in ref.items():
            if v[attr] is not None:
                by_pos_attr[v["position_group"]].append((cid, v[attr]))
        bucket: dict[int, str] = {}
        for pos, items in by_pos_attr.items():
            items_sorted = sorted(items, key=lambda t: t[1])
            n = len(items_sorted)
            for rank, (cid, _val) in enumerate(items_sorted):
                pctile = rank / max(n - 1, 1)
                bucket[cid] = "high" if pctile >= 0.667 else ("low" if pctile < 0.333 else "mid")
        cohorts[f"{attr}_tercile_within_position"] = bucket

    # --- categorical attributes, as-is (min-n filtering happens at report time) ---
    for attr in ("body_type", "accelerate_type", "league", "nation"):
        cohorts[attr] = {cid: (v[attr] or "unknown") for cid, v in ref.items()}
    for attr in ("skill_moves", "weak_foot"):
        cohorts[attr] = {cid: (str(v[attr]) if v[attr] is not None else "unknown") for cid, v in ref.items()}

    return cohorts


def cohort_groups(cohort_map: dict[int, str], min_n: int = MIN_COHORT_N) -> dict[str, set[int]]:
    groups: dict[str, set[int]] = defaultdict(set)
    for cid, label in cohort_map.items():
        groups[label].add(cid)
    return {label: ids for label, ids in groups.items() if len(ids) >= min_n}


# ---------------------------------------------------------------------------
# Section 4 -- cohort investment-return comparison
# ---------------------------------------------------------------------------
def section4_cohort_returns(prices: dict, cohorts: dict) -> None:
    log.info("=" * 100)
    log.info("SECTION 4 -- COHORT INVESTMENT-RETURN COMPARISON")
    log.info("Headline test per dimension: entry=web_app_launch+1 and entry=full_release, holding=7d after-tax.")
    log.info("=" * 100)

    headline_entries = [
        ("day1", WEB_APP_LAUNCH + timedelta(days=1)),
        ("full_release", FULL_RELEASE),
    ]
    for dim in ("price_band", "rating_band", "position_group", "meta_tier",
                "pace_tercile_within_position", "defending_tercile_within_position",
                "physicality_tercile_within_position", "height_cm_tercile_within_position",
                "body_type", "accelerate_type", "skill_moves", "weak_foot", "league", "nation"):
        groups = cohort_groups(cohorts[dim])
        if len(groups) < 2:
            log.info("  [%s] skipped -- fewer than 2 buckets with n>=%d", dim, MIN_COHORT_N)
            continue
        log.info("--- %s (buckets with n>=%d) ---", dim, MIN_COHORT_N)
        for label, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            parts = []
            for entry_label, entry_day in headline_entries:
                stats = compute_cell(ids, prices, entry_day, 7)
                parts.append(f"{entry_label}: n={stats.get('n', 0)} median={stats.get('median', 'nan')}% "
                              f"win={stats.get('win_rate', 'nan')}%")
            log.info("  %-40s %s", label, "  |  ".join(parts))


# ---------------------------------------------------------------------------
# Section 6 -- Full Release event study
# ---------------------------------------------------------------------------
def section6_full_release_event_study(prices: dict, cohorts: dict) -> None:
    log.info("=" * 100)
    log.info("SECTION 6 -- FULL RELEASE EVENT STUDY (median cohort price index, Full Release day = 100)")
    log.info("=" * 100)

    dims_to_report = ["price_tier", "meta_tier", "price_band", "position_group"]
    all_groups: dict[str, set[int]] = {"ALL gold common+rare": set(prices.keys())}
    for dim in dims_to_report:
        for label, ids in cohort_groups(cohorts[dim]).items():
            all_groups[f"{dim}:{label}"] = ids

    header = "%-45s " + " ".join(f"{('FR' + (f'{o:+d}' if o else '')):>8}" for o in FR_OFFSETS)
    log.info(header, "cohort")

    for label, ids in all_groups.items():
        base_prices = [prices[cid][FULL_RELEASE] for cid in ids if FULL_RELEASE in prices.get(cid, {})]
        if len(base_prices) < MIN_COHORT_N:
            continue
        base_median = statistics.median(base_prices)
        row = []
        for offset in FR_OFFSETS:
            d = FULL_RELEASE + timedelta(days=offset)
            vals = [prices[cid][d] for cid in ids if d in prices.get(cid, {})]
            if len(vals) < MIN_COHORT_N or base_median <= 0:
                row.append(float("nan"))
            else:
                row.append(round(statistics.median(vals) / base_median * 100, 1))
        log.info("%-45s " + " ".join(f"{v:>8}" for v in row), f"{label} (n={len(base_prices)})")


# ---------------------------------------------------------------------------
# Section 7 -- best historical investment setups
# ---------------------------------------------------------------------------
def section7_best_setups(prices: dict, cohorts: dict) -> None:
    log.info("=" * 100)
    log.info("SECTION 7 -- CANDIDATE INVESTMENT SETUPS (scanned, not hand-picked)")
    log.info("Scanning entry offsets FR-7..FR+10, holding [3,5,7,10,14], across cohorts. "
              "Filtered to n>=%d.", MIN_COHORT_N)
    log.info("=" * 100)

    dims_to_scan = ["price_tier", "meta_tier", "price_band", "position_group"]
    cohort_pool: dict[str, set[int]] = {"ALL": set(prices.keys())}
    for dim in dims_to_scan:
        for label, ids in cohort_groups(cohorts[dim]).items():
            cohort_pool[f"{dim}:{label}"] = ids

    candidates = []
    for cohort_label, ids in cohort_pool.items():
        for offset in range(-7, 11):
            entry_day = FULL_RELEASE + timedelta(days=offset)
            for holding in (3, 5, 7, 10, 14):
                stats = compute_cell(ids, prices, entry_day, holding)
                if stats.get("n", 0) < MIN_COHORT_N:
                    continue
                mean_median_ratio = (stats["mean"] / stats["median"]) if stats["median"] not in (0, None) else None
                robust = (
                    mean_median_ratio is not None
                    and 0.4 <= mean_median_ratio <= 2.5
                )
                candidates.append({
                    "cohort": cohort_label,
                    "entry_offset_from_FR": offset,
                    "entry_day": entry_day,
                    "holding": holding,
                    **stats,
                    "robust": robust,
                })

    by_median = sorted(candidates, key=lambda c: -c["median"])[:20]
    log.info("--- top 20 by median after-tax ROI ---")
    for c in by_median:
        log.info("  cohort=%-30s entry=%s (FR%+d) hold=%2dd n=%4d median=%6.1f%% mean=%6.1f%% "
                  "win=%5.1f%% p25=%6.1f%% downside_p10=%6.1f%% robust=%s",
                  c["cohort"], c["entry_day"], c["entry_offset_from_FR"], c["holding"], c["n"],
                  c["median"], c["mean"], c["win_rate"], c["p25"], c["downside_p10"], c["robust"])

    decent = [c for c in candidates if c["win_rate"] >= 60 and c["median"] > 0]
    by_win = sorted(decent, key=lambda c: -c["win_rate"])[:20]
    log.info("--- top 20 by win rate (win_rate>=60%, median ROI>0) ---")
    for c in by_win:
        log.info("  cohort=%-30s entry=%s (FR%+d) hold=%2dd n=%4d median=%6.1f%% "
                  "win=%5.1f%% downside_p10=%6.1f%% robust=%s",
                  c["cohort"], c["entry_day"], c["entry_offset_from_FR"], c["holding"], c["n"],
                  c["median"], c["win_rate"], c["downside_p10"], c["robust"])


# ---------------------------------------------------------------------------
# Section 9 -- falling knives
# ---------------------------------------------------------------------------
def section9_falling_knife(prices: dict) -> None:
    log.info("=" * 100)
    log.info("SECTION 9 -- FALLING KNIVES (secondary analysis)")
    log.info("=" * 100)

    inspect_end = FULL_RELEASE + timedelta(days=21)
    depth_buckets = [0.10, 0.20, 0.30, 0.40, 0.50]
    # depth -> list of forward after-tax returns at +5/+7/+14
    forward_by_depth: dict[float, dict[int, list[float]]] = {b: {5: [], 7: [], 14: []} for b in depth_buckets}

    signal_forward: dict[str, dict[int, list[float]]] = {
        "flat_or_up_day": {5: [], 7: [], 14: []},
        "first_up_day": {5: [], 7: [], 14: []},
        "two_consec_nonneg_days": {5: [], 7: [], 14: []},
        "at_or_above_trailing_3d_low": {5: [], 7: [], 14: []},
        "baseline_still_declining": {5: [], 7: [], 14: []},  # comparison group
    }

    for cid, series in prices.items():
        dates = sorted(d for d in series if WEB_APP_LAUNCH <= d <= inspect_end)
        if len(dates) < 5:
            continue
        running_peak = None
        depth_seen: set[float] = set()
        for i, d in enumerate(dates):
            p = series[d]
            if running_peak is None or p > running_peak:
                running_peak = p
            drawdown = (running_peak - p) / running_peak if running_peak else 0.0
            in_decline = drawdown >= 0.15

            for b in depth_buckets:
                if drawdown >= b and b not in depth_seen:
                    depth_seen.add(b)
                    for h in (5, 7, 14):
                        exit_d = d + timedelta(days=h)
                        if exit_d in series and series[exit_d] and p:
                            forward_by_depth[b][h].append((series[exit_d] * (1 - EA_TAX) - p) / p)

            if in_decline and i >= 2:
                prev1, prev2 = series.get(dates[i - 1]), series.get(dates[i - 2])
                if prev1 and prev2:
                    flat_or_up = p >= prev1 * 0.999
                    first_up = p > prev1
                    two_nonneg = p >= prev1 * 0.999 and prev1 >= prev2 * 0.999
                    trailing_window = [series[dd] for dd in dates[max(0, i - 2):i + 1] if dd in series]
                    at_low = bool(trailing_window) and p <= min(trailing_window) * 1.001

                    def _record(key, cond):
                        if cond:
                            for h in (5, 7, 14):
                                exit_d = d + timedelta(days=h)
                                if exit_d in series and series[exit_d] and p:
                                    signal_forward[key][h].append(
                                        (series[exit_d] * (1 - EA_TAX) - p) / p
                                    )

                    _record("flat_or_up_day", flat_or_up)
                    _record("first_up_day", first_up)
                    _record("two_consec_nonneg_days", two_nonneg)
                    _record("at_or_above_trailing_3d_low", at_low)
                    _record("baseline_still_declining", not (flat_or_up or first_up))

    log.info("--- forward after-tax return by decline depth (first day a card crossed each depth threshold) ---")
    log.info("%-10s %8s %8s %8s %8s %8s %8s %8s %8s %8s",
              "depth>=", "n@+5", "med@+5", "n@+7", "med@+7", "n@+14", "med@+14", "", "", "")
    for b in depth_buckets:
        row = []
        for h in (5, 7, 14):
            vals = forward_by_depth[b][h]
            row.append(len(vals))
            row.append(round(statistics.median(vals) * 100, 1) if vals else float("nan"))
        log.info("%-10s %8d %8s %8d %8s %8d %8s", f"{b*100:.0f}%", *row)

    log.info("--- forward after-tax return by observable daily-price 'buy the decline' signal "
              "(instances while a card was in >=15% drawdown from its trailing peak) ---")
    log.info("%-30s %8s %8s %8s %8s %8s %8s", "signal", "n@+5", "med@+5", "n@+7", "med@+7", "n@+14", "med@+14")
    for key, by_h in signal_forward.items():
        row = []
        for h in (5, 7, 14):
            vals = by_h[h]
            row.append(len(vals))
            row.append(round(statistics.median(vals) * 100, 1) if vals else float("nan"))
        log.info("%-30s %8d %8s %8d %8s %8d %8s", key, *row)

    log.info("  (compare each signal row against 'baseline_still_declining' -- a signal is only "
              "useful if it beats that baseline by a meaningful margin with a comparable n, not "
              "just because it has a positive median.)")


# ---------------------------------------------------------------------------
async def main() -> int:
    conn = await connect_with_retry(DATABASE_URL)
    try:
        log.info("Loading fc26_historical_daily_prices + futgg_players (gold common/rare only)...")
        prices, meta = await load_data(conn)
        log.info("Loaded %d cards with price history, %d metadata rows.", len(prices), len(meta))

        section1_timeline(prices)
        section2_market_overview(prices, meta)
        section3_return_matrix(prices, meta)

        ref = build_reference_attrs(prices, meta)
        cohorts = build_cohorts(ref)
        section4_cohort_returns(prices, cohorts)
        section6_full_release_event_study(prices, cohorts)
        section7_best_setups(prices, cohorts)
        section9_falling_knife(prices)

        log.info("=" * 100)
        log.info("PHASE 8 (first cut) complete. No-future-leakage statement: every cohort label "
                  "above (price_band, rating_band, position_group, meta_tier, attribute terciles) "
                  "was assigned from each card's FIRST observed price/date and static metadata "
                  "only -- never from later price performance. Every ROI cell uses only the entry "
                  "day's price to decide entry and a strictly later day's price to evaluate the "
                  "outcome; no cell uses information from after its own entry day to decide "
                  "membership or timing.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
