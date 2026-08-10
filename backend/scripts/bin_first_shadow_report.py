#!/usr/bin/env python
# scripts/bin_first_shadow_report.py
#
# Read-only. Replays real production BIN and sales history to answer the
# one question that decides whether BIN-first ships:
#
#     Can we credibly remove 90-95% of expensive sales scraping?
#
# It writes nothing, changes no worker behaviour, and needs no feature
# flag to run - it reconstructs both schedules from history that already
# exists rather than waiting for a shadow deployment to accumulate.
#
# WHAT IT COMPARES
# ----------------
# Baseline: futgg_sales_sync.py's real schedule - a rating-band timer
# (85+ 60min, 80-84 240min, 75-79 720min, under-75 2880min, all
# overridable, all read from this process's env so the numbers reflect
# what is actually deployed rather than the documented defaults).
#
# BIN-first: for each card, compute its BinMarketSignal from real
# futgg_bin_history, assemble its CardContext from real tables, and ask
# the gate whether it would have earned a request.
#
# Where a card has BOTH a BIN anomaly and enough real sales around it, the
# report also validates the hypothesis against those sales - so the
# confirmation rate is measured on production data, not asserted.
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from dotenv import load_dotenv

from app.services.bin_market_signal import (
    BIN_MARKET_CONFIG, INSUFFICIENT_BIN_HISTORY, POSSIBLE_SPIKE,
    POSSIBLE_UNDERCUT, compute_bin_market_signal,
)
from app.services.sales_validation_gate import (
    BIN_SPIKE_VALIDATION, BIN_UNDERCUT_VALIDATION, PERIODIC_DISCOVERY,
    SALES_VALIDATION_CONFIG, BudgetState, CardContext, apply_budget,
    classify_discovery_result, discovery_date_bucket, evaluate_eligibility,
    select_discovery_sample, validate_against_sales,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bin_first_shadow_report")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


#: Read from THIS process's environment, deliberately: the point of the
#: baseline is what production actually runs, and the documented defaults
#: have been re-tuned at least once already (see futgg_sales_sync.py's
#: note on halving each band after 144 HTTP 429s in one run).
SALES_INTERVALS_MIN = {
    "85_plus": _env_int("FUTGG_SALES_85_PLUS_INTERVAL_MIN", 60),
    "80_84": _env_int("FUTGG_SALES_80_84_INTERVAL_MIN", 240),
    "75_79": _env_int("FUTGG_SALES_75_79_INTERVAL_MIN", 720),
    "under_75": _env_int("FUTGG_SALES_UNDER_75_INTERVAL_MIN", 2880),
}


def _band(rating: Optional[int]) -> str:
    r = rating or 0
    if r >= 85:
        return "85_plus"
    if r >= 80:
        return "80_84"
    if r >= 75:
        return "75_79"
    return "under_75"


#: sales_first_read_at / sales_consecutive_failures are added by
#: futgg_sales_sync.ensure_schema (the retry-loop fix), not by a backend
#: migration - so on a database whose SalesSync worker has not yet
#: redeployed they simply do not exist. Selected conditionally rather than
#: unconditionally so this read-only report still runs on that database
#: and reports the retry-loop numbers from the old encoding instead of
#: failing outright.
_CARDS_QUERY = """
    SELECT p.source_card_id, p.rating, p.is_active, p.is_tradeable,
           p.first_seen_at, p.next_sales_due_at, p.last_sales_status,
           {retry_columns}
           s.latest_sale_at, coalesce(s.sales_count_14d, 0) AS sales_count_14d
    FROM futgg_players p
    LEFT JOIN LATERAL (
        SELECT max(approximate_sold_at) AS latest_sale_at,
               count(*) AS sales_count_14d
        FROM futgg_sales_history h
        WHERE h.source_card_id = p.source_card_id
          AND h.approximate_sold_at >= now() - interval '14 days'
    ) s ON TRUE
    WHERE p.is_active AND p.is_tradeable IS DISTINCT FROM FALSE
"""

#: Bounded per card by the LATERAL so a 10k-card scan stays one query.
#: 24h is enough for every threshold the gate reads; the 7d median is
#: reported by the signal but does not gate anything, so the extra six
#: days of rows are not worth pulling for a whole-catalogue replay.
_BIN_QUERY = """
    SELECT h.source_card_id, h.lowest_bin, h.captured_at
    FROM unnest($1::bigint[]) AS ids(card_id)
    CROSS JOIN LATERAL (
        SELECT source_card_id, lowest_bin, captured_at
        FROM futgg_bin_history
        WHERE source_card_id = ids.card_id
          AND captured_at >= now() - make_interval(hours => $2)
        ORDER BY captured_at DESC
        LIMIT $3
    ) h
"""

_SALES_QUERY = """
    SELECT h.source_card_id, h.sold_price, h.approximate_sold_at
    FROM unnest($1::bigint[]) AS ids(card_id)
    CROSS JOIN LATERAL (
        SELECT source_card_id, sold_price, approximate_sold_at
        FROM futgg_sales_history
        WHERE source_card_id = ids.card_id
          AND approximate_sold_at >= now() - interval '24 hours'
        ORDER BY approximate_sold_at DESC
        LIMIT 50
    ) h
"""

_WATCHERS_QUERY = """
    SELECT card_id, count(DISTINCT user_id) AS watchers
    FROM watchlist WHERE source = 'futgg' GROUP BY card_id
"""


async def _has_retry_columns(conn) -> bool:
    return bool(await conn.fetchval(
        """
        SELECT count(*) = 2 FROM information_schema.columns
        WHERE table_name = 'futgg_players'
          AND column_name IN ('sales_first_read_at', 'sales_consecutive_failures')
        """
    ))


async def _bin_history_health(conn) -> Dict[str, Any]:
    """Storage/growth audit (spec item 18) - measured, not assumed."""
    row = await conn.fetchrow(
        """
        SELECT count(*) AS rows_24h,
               count(DISTINCT source_card_id) AS cards_24h,
               min(captured_at) AS oldest_24h
        FROM futgg_bin_history
        WHERE captured_at >= now() - interval '24 hours'
        """
    )
    total = await conn.fetchrow(
        """
        SELECT count(*) AS total_rows, min(captured_at) AS oldest,
               max(captured_at) AS newest
        FROM futgg_bin_history
        """
    )
    size = await conn.fetchval(
        "SELECT pg_total_relation_size('futgg_bin_history')"
    )
    # How much of the write volume is a repeat of the previous value for
    # the same card - i.e. how much change-only recording would save.
    unchanged = await conn.fetchrow(
        """
        WITH recent AS (
            SELECT source_card_id, lowest_bin,
                   lag(lowest_bin) OVER (
                       PARTITION BY source_card_id ORDER BY captured_at
                   ) AS prev
            FROM futgg_bin_history
            WHERE captured_at >= now() - interval '24 hours'
        )
        SELECT count(*) AS n, count(*) FILTER (WHERE prev IS NOT NULL AND prev = lowest_bin) AS repeats
        FROM recent
        """
    )
    repeats = int(unchanged["repeats"] or 0)
    n = int(unchanged["n"] or 0)
    return {
        "rows_last_24h": int(row["rows_24h"] or 0),
        "cards_last_24h": int(row["cards_24h"] or 0),
        "total_rows": int(total["total_rows"] or 0),
        "oldest_observation": total["oldest"].isoformat() if total["oldest"] else None,
        "newest_observation": total["newest"].isoformat() if total["newest"] else None,
        "table_bytes": int(size or 0),
        "table_mb": round(int(size or 0) / (1024 * 1024), 1),
        "unchanged_repeat_pct": round(100.0 * repeats / n, 1) if n else 0.0,
        "projected_rows_per_month": int(row["rows_24h"] or 0) * 30,
    }


async def build_report(pool, *, bin_hours: int, max_obs: int) -> Dict[str, Any]:
    as_of = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        storage = await _bin_history_health(conn)
        has_retry_columns = await _has_retry_columns(conn)
        if not has_retry_columns:
            log.warning(
                "futgg_players lacks sales_first_read_at/sales_consecutive_failures - "
                "SalesSync has not redeployed with the retry-loop fix yet. Falling back "
                "to the old next_sales_due_at encoding for never-read counts."
            )
        cards = await conn.fetch(_CARDS_QUERY.format(
            retry_columns=(
                "p.sales_first_read_at, p.sales_consecutive_failures,"
                if has_retry_columns else ""
            )
        ))
        try:
            watcher_rows = await conn.fetch(_WATCHERS_QUERY)
            watchers = {int(r["card_id"]): int(r["watchers"]) for r in watcher_rows}
        except Exception:
            # The watchlist lives on its own pool in production; absent
            # here it simply means "no user-interest signal", which the
            # gate already handles. Never a reason to fail the report.
            log.warning("watchlist not readable from this pool; treating watcher counts as 0")
            watchers = {}

    card_ids = [int(c["source_card_id"]) for c in cards]
    log.info("replaying %d active cards", len(card_ids))

    # ---- Baseline: what the existing schedule costs per day ------------
    band_counts = Counter(_band(c["rating"]) for c in cards)
    legacy_per_day = 0.0
    for band, count in band_counts.items():
        interval = max(1, SALES_INTERVALS_MIN[band])
        legacy_per_day += count * (1440.0 / interval)

    now_ = as_of
    due_now, overdue_1h = 0, 0
    for c in cards:
        due = c["next_sales_due_at"]
        if due is None:
            due_now += 1
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= now_:
            due_now += 1
            if (now_ - due) > timedelta(hours=1):
                overdue_1h += 1

    # "Never read" is now sales_first_read_at IS NULL (see
    # futgg_sales_sync.py's retry-loop fix); fall back to the old
    # next_sales_due_at encoding on a database the fix has not reached.
    def _never_read(c) -> bool:
        try:
            return c["sales_first_read_at"] is None
        except (KeyError, TypeError):
            return c["next_sales_due_at"] is None

    never_read = [c for c in cards if _never_read(c)]
    # A never-read card that keeps FAILING is never given a due date (see
    # process_batch's was_never_read branch), so it returns to the front of
    # select_due()'s queue on every single cycle. That is an unbounded
    # retry loop, and it is invisible in the interval arithmetic above.
    failing_never_read = [
        c for c in never_read
        if (c["last_sales_status"] or "").startswith("error")
    ]
    # Retry waste attributable specifically to the never-read loop, on the
    # OLD behaviour: those cards had no due-at, so select_due() returned
    # them every cycle. One cycle is CYCLE_SLEEP plus however long a batch
    # of SELECT_LIMIT cards takes; the conservative floor below assumes
    # they simply filled the queue once per cycle.
    cycle_seconds = _env_int("FUTGG_SALES_CYCLE_SLEEP", 5) + 60
    cycles_per_day = 86400.0 / max(1, cycle_seconds)
    select_limit = _env_int("FUTGG_SALES_SELECT_LIMIT", 200)
    never_read_retry_waste_per_day = min(
        len(failing_never_read) * cycles_per_day,
        select_limit * cycles_per_day,
    )

    # ---- BIN-first replay ----------------------------------------------
    bin_obs: Dict[int, List] = defaultdict(list)
    sales_by_card: Dict[int, List[float]] = defaultdict(list)
    CHUNK = 500
    async with pool.acquire() as conn:
        for i in range(0, len(card_ids), CHUNK):
            chunk = card_ids[i:i + CHUNK]
            for r in await conn.fetch(_BIN_QUERY, chunk, bin_hours, max_obs):
                bin_obs[int(r["source_card_id"])].append(
                    (int(r["lowest_bin"]), r["captured_at"])
                )
            for r in await conn.fetch(_SALES_QUERY, chunk):
                sales_by_card[int(r["source_card_id"])].append(float(r["sold_price"]))

    decisions = []
    state_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    skip_counts: Counter = Counter()
    class_counts: Counter = Counter()
    thin_markets = 0
    no_bin_history = 0

    for card in cards:
        cid = int(card["source_card_id"])
        signal = compute_bin_market_signal(cid, bin_obs.get(cid, []), as_of=as_of)
        state_counts[signal.market_state] += 1
        if signal.market_state == INSUFFICIENT_BIN_HISTORY:
            no_bin_history += 1
        if signal.thin_market:
            thin_markets += 1

        ctx = CardContext(
            card_id=cid, rating=card["rating"], is_active=card["is_active"],
            is_tradeable=card["is_tradeable"],
            latest_sale_at=card["latest_sale_at"],
            sales_count_14d=int(card["sales_count_14d"] or 0),
            never_read=card["next_sales_due_at"] is None,
            first_seen_at=card["first_seen_at"],
            watcher_count=watchers.get(cid, 0),
            # An anomaly that has survived at least the configured number
            # of bulk observations. Replayed conservatively: only counted
            # where the card genuinely has enough observations for the
            # signal to be trusted at all.
            anomaly_persistence_observations=(
                SALES_VALIDATION_CONFIG.min_anomaly_persistence_observations
                if signal.observation_count_24h >= BIN_MARKET_CONFIG.min_observations_for_anomaly
                else 0
            ),
        )
        decision = evaluate_eligibility(ctx, signal, as_of=as_of)
        decision.notes.append(f"legacy_band={_band(card['rating'])}")
        decisions.append((decision, signal))
        class_counts[decision.card_class] += 1
        if decision.eligible:
            reason_counts[decision.reason] += 1
        else:
            skip_counts[decision.skip_reason or "UNKNOWN"] += 1

    # ---- Discovery / control sample -------------------------------------
    # Promotes a small stratified slice of otherwise-SKIPPED cards. Without
    # it the whole exercise is self-confirming: BIN-first would only ever
    # pay to look at cards BIN-first already liked, so "we missed nothing"
    # would be true by construction rather than measured.
    all_decisions = [d for d, _ in decisions]
    discovery = select_discovery_sample(all_decisions, as_of=as_of)
    signal_by_card = {d.card_id: sig for d, sig in decisions}

    discovery_findings: Counter = Counter()
    discovery_by_class: Counter = Counter()
    discovery_by_band: Counter = Counter()
    rating_by_card = {int(c["source_card_id"]): c["rating"] for c in cards}
    for decision in discovery:
        discovery_by_class[decision.card_class] += 1
        discovery_by_band[_band(rating_by_card.get(decision.card_id))] += 1
        finding = classify_discovery_result(
            signal_by_card[decision.card_id],
            sales_prices=sales_by_card.get(decision.card_id, []),
        )
        discovery_findings[finding] += 1

    # What the control sample says BIN-first would have MISSED: cards the
    # gate skipped whose real sales sit materially above the live ask.
    # Measured on the sampled slice only, then scaled - and the scaling is
    # reported as an estimate, not a count.
    missed_in_sample = discovery_findings.get("POTENTIAL_OPPORTUNITY_MISSED", 0)
    sampled_total = max(1, len(discovery))
    skipped_population = sum(
        1 for d in all_decisions
        if not d.eligible and d.card_class in ("dormant", "cold", "warm")
    )
    estimated_missed_population = (
        round(skipped_population * missed_in_sample / sampled_total, 1)
        if discovery else None
    )

    # ---- Budget ---------------------------------------------------------
    admitted = apply_budget(all_decisions, BudgetState())
    post_budget_reasons = Counter(d.reason for d in admitted)

    # One BIN-first pass admits this many. The gate re-runs each bulk
    # cycle, but cooldown + per-card daily cap bound how often the SAME
    # card can return, so the honest daily figure is the per-card cap
    # applied to the admitted set rather than passes x admitted.
    passes_per_day = 24 * 60 / max(1, int(os.getenv("FUTGG_BULK_INTERVAL_MINUTES", "5")))
    max_per_card = SALES_VALIDATION_CONFIG.max_validations_per_card_per_day
    cooldown_passes_per_day = min(
        max_per_card, 1440.0 / max(1, SALES_VALIDATION_CONFIG.validation_cooldown_minutes)
    )
    binfirst_per_day = min(
        len(admitted) * cooldown_passes_per_day,
        float(SALES_VALIDATION_CONFIG.max_requests_per_day),
    )

    # ---- Confirmation rate on real sales --------------------------------
    confirmations: Counter = Counter()
    examples: List[Dict[str, Any]] = []
    for decision, signal in decisions:
        if signal.market_state not in (POSSIBLE_UNDERCUT, POSSIBLE_SPIKE):
            continue
        prices = sales_by_card.get(signal.card_id, [])
        if len(prices) < 5:
            confirmations["NO_SALES_TO_COMPARE"] += 1
            continue
        reason = (
            BIN_UNDERCUT_VALIDATION if signal.market_state == POSSIBLE_UNDERCUT
            else BIN_SPIKE_VALIDATION
        )
        outcome = validate_against_sales(signal, reason=reason, sales_prices=prices)
        confirmations[outcome.outcome] += 1
        if len(examples) < 12:
            examples.append({
                "card_id": signal.card_id, "market_state": signal.market_state,
                "current_bin": signal.current_bin,
                "bin_baseline": round(signal.baseline, 1) if signal.baseline else None,
                "sales_median": outcome.sales_median,
                "sales_count": outcome.sales_count,
                "anomaly_score": signal.anomaly_score,
                "thin_market": signal.thin_market,
                "outcome": outcome.outcome,
            })

    reduction_pct = (
        round(100.0 * (legacy_per_day - binfirst_per_day) / legacy_per_day, 1)
        if legacy_per_day > 0 else 0.0
    )

    return {
        "generated_at": as_of.isoformat(),
        "bin_signal_config_version": BIN_MARKET_CONFIG.version,
        "gate_config_version": SALES_VALIDATION_CONFIG.version,
        "catalogue": {
            "active_cards": len(cards),
            "band_counts": dict(band_counts),
            "never_read": len(never_read),
            "never_read_and_failing": len(failing_never_read),
            "cards_with_no_bin_history": no_bin_history,
            "thin_markets": thin_markets,
        },
        "bin_history_storage": storage,
        "baseline": {
            "sales_intervals_minutes": SALES_INTERVALS_MIN,
            "scheduled_requests_per_day": round(legacy_per_day, 1),
            "note": (
                "Interval arithmetic only. It EXCLUDES the never-read retry loop: "
                "a card that has never been read and keeps failing is never given a "
                "next_sales_due_at, so select_due() returns it every cycle forever. "
                "See never_read_and_failing."
            ),
        },
        "bin_first": {
            "market_states": dict(state_counts),
            "card_classes": dict(class_counts),
            "eligible_before_budget": sum(reason_counts.values()),
            "reasons_before_budget": dict(reason_counts),
            "skip_reasons": dict(skip_counts),
            "admitted_after_budget": len(admitted),
            "reasons_after_budget": dict(post_budget_reasons),
            "estimated_requests_per_day": round(binfirst_per_day, 1),
            "budget": {
                "max_requests_per_hour": SALES_VALIDATION_CONFIG.max_requests_per_hour,
                "max_requests_per_day": SALES_VALIDATION_CONFIG.max_requests_per_day,
                "discovery_per_day": SALES_VALIDATION_CONFIG.discovery_sample_per_day,
                "cooldown_minutes": SALES_VALIDATION_CONFIG.validation_cooldown_minutes,
                "max_per_card_per_day": max_per_card,
            },
        },
        "workload": {
            "cards_due_now": due_now,
            "cards_overdue_by_1h_plus": overdue_1h,
            "never_read_retry_waste_per_day_estimate": round(never_read_retry_waste_per_day, 0),
            "retry_waste_note": (
                "Pre-fix estimate. Those cards had no next_sales_due_at, so select_due() "
                "returned them every cycle regardless of any interval. Now bounded by "
                "futgg_sales_sync.failure_backoff_minutes()."
            ),
            "retry_columns_present": has_retry_columns,
        },
        "discovery": {
            "date_bucket": discovery_date_bucket(as_of),
            "sampled_per_day": len(discovery),
            "by_card_class": dict(discovery_by_class),
            "by_rating_band": dict(discovery_by_band),
            "pct_of_daily_budget": (
                round(100.0 * len(discovery) / SALES_VALIDATION_CONFIG.max_requests_per_day, 2)
                if SALES_VALIDATION_CONFIG.max_requests_per_day else 0.0
            ),
            "findings": dict(discovery_findings),
            "skipped_population": skipped_population,
            "estimated_missed_opportunities_in_skipped_population": estimated_missed_population,
        },
        "headline": {
            "current_requests_per_day": round(legacy_per_day, 1),
            "projected_requests_per_day": round(binfirst_per_day, 1),
            "reduction_pct": reduction_pct,
            "meets_90pct_target": reduction_pct >= 90.0,
            "meets_95pct_stretch": reduction_pct >= 95.0,
            "page_captures_avoided_per_day": round(legacy_per_day - binfirst_per_day, 0),
            "pct_catalogue_with_no_routine_salessync": (
                round(100.0 * sum(
                    1 for d in all_decisions if d.card_class in ("dormant", "cold")
                ) / len(cards), 1) if cards else 0.0
            ),
            "proportional_monthly_cost_estimate": {
                "observed_baseline_usd_per_month": 115.0,
                "projected_usd_per_month": (
                    round(115.0 * binfirst_per_day / legacy_per_day, 2)
                    if legacy_per_day > 0 else None
                ),
                "note": (
                    "Proportional scaling of the reported ~$80/3-week run rate by request "
                    "volume ONLY. Not a hosting bill: it assumes cost is linear in sales "
                    "requests and ignores the always-on bulk worker, the database and any "
                    "fixed platform charge, so treat the real saving as smaller."
                ),
            },
        },
        "bin_anomaly_confirmation": dict(confirmations),
        "examples": examples,
    }


def _print_human(report: Dict[str, Any]) -> None:
    c, b, f, h = (
        report["catalogue"], report["baseline"],
        report["bin_first"], report["headline"],
    )
    print("=" * 78)
    print("BIN-FIRST SHADOW REPORT (read-only, no behaviour changed)")
    print("=" * 78)
    print(f"\nCATALOGUE: {c['active_cards']:,} active cards")
    for band, n in sorted(c["band_counts"].items()):
        print(f"  {band:<10} {n:>6,}  (interval {b['sales_intervals_minutes'][band]} min)")
    print(f"  never read:            {c['never_read']:,}")
    print(f"  never read AND failing:{c['never_read_and_failing']:,}   <- unbounded retry loop")
    print(f"  no BIN history:        {c['cards_with_no_bin_history']:,}")
    print(f"  thin markets:          {c['thin_markets']:,}")

    s = report["bin_history_storage"]
    print(f"\nBIN HISTORY: {s['total_rows']:,} rows, {s['table_mb']:,} MB")
    print(f"  written last 24h:      {s['rows_last_24h']:,} across {s['cards_last_24h']:,} cards")
    print(f"  repeat (unchanged) writes: {s['unchanged_repeat_pct']}%")
    print(f"  projected rows/month:  {s['projected_rows_per_month']:,}")

    print(f"\nMARKET STATES")
    for state, n in sorted(f["market_states"].items(), key=lambda kv: -kv[1]):
        print(f"  {state:<26} {n:>6,}")
    print(f"\nCARD CLASSES")
    for klass, n in sorted(f["card_classes"].items(), key=lambda kv: -kv[1]):
        print(f"  {klass:<26} {n:>6,}")

    print(f"\nELIGIBILITY (one pass)")
    print(f"  eligible before budget: {f['eligible_before_budget']:,}")
    for reason, n in sorted(f["reasons_before_budget"].items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<30} {n:>6,}")
    print(f"  admitted after budget:  {f['admitted_after_budget']:,}")
    print(f"  top skip reasons:")
    for reason, n in sorted(f["skip_reasons"].items(), key=lambda kv: -kv[1])[:6]:
        print(f"    {reason:<30} {n:>6,}")

    w, d = report["workload"], report["discovery"]
    print(f"\nWORKLOAD")
    print(f"  due now:                    {w['cards_due_now']:,}")
    print(f"  overdue >1h:                {w['cards_overdue_by_1h_plus']:,}")
    print(f"  never-read retry waste/day: {w['never_read_retry_waste_per_day_estimate']:,.0f} (pre-fix estimate)")
    print(f"  retry columns present:      {w['retry_columns_present']}")

    print(f"\nDISCOVERY / CONTROL SAMPLE (bucket {d['date_bucket']})")
    print(f"  sampled:                    {d['sampled_per_day']:,}  "
          f"({d['pct_of_daily_budget']}% of daily budget)")
    print(f"  by class: {d['by_card_class']}")
    print(f"  by band:  {d['by_rating_band']}")
    for finding, n in sorted(d["findings"].items(), key=lambda kv: -kv[1]):
        print(f"    {finding:<34} {n:>6,}")
    print(f"  skipped population:         {d['skipped_population']:,}")
    print(f"  est. missed opportunities:  {d['estimated_missed_opportunities_in_skipped_population']}")

    print(f"\nBIN ANOMALY CONFIRMATION (against real sales)")
    for outcome, n in sorted(report["bin_anomaly_confirmation"].items(), key=lambda kv: -kv[1]):
        print(f"  {outcome:<34} {n:>6,}")

    if report["examples"]:
        print(f"\nEXAMPLES")
        for e in report["examples"][:8]:
            print(
                f"  card={e['card_id']:<11} {e['market_state']:<18} "
                f"bin={e['current_bin']:<9} baseline={e['bin_baseline']:<10} "
                f"sales_med={e['sales_median']:<10} -> {e['outcome']}"
            )

    print("\n" + "=" * 78)
    print(f"CURRENT   requests/day: {h['current_requests_per_day']:,}")
    print(f"BIN-FIRST requests/day: {h['projected_requests_per_day']:,}")
    print(f"REDUCTION:              {h['reduction_pct']}%")
    print(f"  >= 90% target:  {'YES' if h['meets_90pct_target'] else 'NO'}")
    print(f"  >= 95% stretch: {'YES' if h['meets_95pct_stretch'] else 'NO'}")
    print(f"Chromium navigations avoided/day: {h['page_captures_avoided_per_day']:,.0f}")
    print(f"Catalogue with no routine SalesSync: {h['pct_catalogue_with_no_routine_salessync']}%")
    c = h["proportional_monthly_cost_estimate"]
    print(f"Proportional monthly estimate: ${c['observed_baseline_usd_per_month']:.0f} -> "
          f"${c['projected_usd_per_month']}")
    print(f"  {c['note']}")
    print("=" * 78)


async def main() -> int:
    p = argparse.ArgumentParser(description="BIN-first shadow report (read-only)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of the human report")
    p.add_argument("--bin-hours", type=int, default=24, help="BIN history window to replay")
    p.add_argument("--max-observations", type=int, default=400, help="max BIN rows per card")
    args = p.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL is required")
        return 1

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    try:
        report = await build_report(
            pool, bin_hours=args.bin_hours, max_obs=args.max_observations,
        )
    finally:
        await pool.close()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
