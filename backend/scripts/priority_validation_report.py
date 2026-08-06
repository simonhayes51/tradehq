"""
Read-only production-readiness validation report for the adaptive
priority engine (Milestones 1 + 1.5 - see priority_recompute.py and
/root/.claude/plans/binary-meandering-raccoon.md).

Every query in this script is a SELECT. Nothing here writes to
card_priority, priority_triggers, priority_engine_events, or
futgg_players - the one exception is that it calls the same pure,
in-memory classification function (compute_priority) production uses,
on a sample of live rows, purely to compare its output against what's
already stored (see check_classification_invariance()); that call never
touches the database.

Usage (run from the backend/ directory, same convention as the other
scripts here - e.g. scripts/grant_premium.py):

    python scripts/priority_validation_report.py
    python scripts/priority_validation_report.py --json
    python scripts/priority_validation_report.py --sample-per-tier 10

Requires DATABASE_URL (single-DB deployment - watchlist popularity is
read from the already-materialized watchlist_popularity table on the
core DB, not the separate WATCHLIST_DATABASE_URL pool, so no second
connection is needed for this report).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.priority_config import PRIORITY_ENGINE_CONFIG, TIERS, tier_rank  # noqa: E402
from app.services.priority_engine import compute_priority  # noqa: E402
from app.services.priority_recompute import is_eligible_for_conservative_demotion  # noqa: E402


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# =============================================================================
# 1. Tier distribution
# =============================================================================

async def report_tier_distribution(conn: asyncpg.Connection, grace_hours: int) -> Dict[str, Any]:
    total_active = await conn.fetchval("SELECT count(*) FROM futgg_market_snapshot WHERE is_active")
    total_classified = await conn.fetchval("SELECT count(*) FROM card_priority")
    missing = await conn.fetchval(
        """
        SELECT count(*) FROM futgg_market_snapshot ms
        WHERE ms.is_active
          AND NOT EXISTS (SELECT 1 FROM card_priority cp WHERE cp.card_id = ms.source_card_id)
        """
    )
    baseline_rows = await conn.fetch("SELECT baseline_tier, count(*) AS n FROM card_priority GROUP BY baseline_tier")
    effective_rows = await conn.fetch("SELECT effective_tier, count(*) AS n FROM card_priority GROUP BY effective_tier")
    diff = await conn.fetchval("SELECT count(*) FROM card_priority WHERE baseline_tier IS DISTINCT FROM effective_tier")
    with_active_trigger = await conn.fetchval(
        f"""
        SELECT count(DISTINCT card_id) FROM priority_triggers t
        WHERE (t.expires_at IS NULL OR t.expires_at > now())
          AND (t.trigger_type = 'manual_override' OR t.last_confirmed_at > now() - ($1 * interval '1 hour'))
        """,
        grace_hours,
    )

    by_baseline = {r["baseline_tier"]: int(r["n"]) for r in baseline_rows}
    by_effective = {r["effective_tier"]: int(r["n"]) for r in effective_rows}
    return {
        "total_active_cards": int(total_active or 0),
        "total_classified": int(total_classified or 0),
        "cards_missing_classification": int(missing or 0),
        "cards_by_baseline_tier": {t: by_baseline.get(t, 0) for t in TIERS},
        "cards_by_effective_tier": {t: by_effective.get(t, 0) for t in TIERS},
        "baseline_pct": {t: _pct(by_baseline.get(t, 0), total_classified or 0) for t in TIERS},
        "effective_pct": {t: _pct(by_effective.get(t, 0), total_classified or 0) for t in TIERS},
        "cards_where_baseline_differs_from_effective": int(diff or 0),
        "cards_with_active_trigger": int(with_active_trigger or 0),
    }


# =============================================================================
# 2. Trigger health
# =============================================================================

async def report_trigger_health(conn: asyncpg.Connection, grace_hours: int) -> Dict[str, Any]:
    active_by_type = await conn.fetch(
        f"""
        SELECT trigger_type, count(*) AS n FROM priority_triggers t
        WHERE (t.expires_at IS NULL OR t.expires_at > now())
          AND (t.trigger_type = 'manual_override' OR t.last_confirmed_at > now() - ($1 * interval '1 hour'))
        GROUP BY trigger_type
        """,
        grace_hours,
    )
    expired_still_present = await conn.fetchval(
        "SELECT count(*) FROM priority_triggers WHERE expires_at IS NOT NULL AND expires_at <= now()"
    )
    missing_expiry = await conn.fetchval(
        "SELECT count(*) FROM priority_triggers WHERE expires_at IS NULL AND trigger_type != 'manual_override'"
    )
    orphaned = await conn.fetchval(
        """
        SELECT count(*) FROM priority_triggers t
        WHERE NOT EXISTS (SELECT 1 FROM futgg_players p WHERE p.source_card_id = t.card_id)
        """
    )
    # A trigger currently active but whose forced_min_tier ranks better
    # (lower rank) than card_priority's own recorded effective_tier is a
    # data-integrity mismatch - the trigger exists but hasn't been
    # reflected into card_priority yet (e.g. created between recompute
    # passes). Computed in Python since tier_rank isn't a SQL concept.
    mismatch_rows = await conn.fetch(
        f"""
        SELECT t.card_id, t.forced_min_tier, cp.effective_tier
        FROM priority_triggers t
        JOIN card_priority cp ON cp.card_id = t.card_id
        WHERE (t.expires_at IS NULL OR t.expires_at > now())
          AND (t.trigger_type = 'manual_override' OR t.last_confirmed_at > now() - ($1 * interval '1 hour'))
        """,
        grace_hours,
    )
    tier_mismatches = sum(
        1 for r in mismatch_rows if tier_rank(r["forced_min_tier"]) < tier_rank(r["effective_tier"])
    )

    sbc_by_event = await conn.fetch(
        f"""
        SELECT e.title, e.id AS event_id, count(*) AS n
        FROM priority_triggers t
        JOIN market_events e ON e.id = t.source_event_id
        WHERE t.trigger_type = 'sbc_match'
          AND (t.expires_at IS NULL OR t.expires_at > now())
          AND (t.last_confirmed_at > now() - ($1 * interval '1 hour'))
        GROUP BY e.title, e.id
        ORDER BY n DESC
        LIMIT 20
        """,
        grace_hours,
    )
    expiry_bounds = await conn.fetchrow(
        f"""
        SELECT min(expires_at) AS earliest, max(expires_at) AS latest
        FROM priority_triggers t
        WHERE (t.expires_at IS NULL OR t.expires_at > now())
          AND (t.trigger_type = 'manual_override' OR t.last_confirmed_at > now() - ($1 * interval '1 hour'))
        """,
        grace_hours,
    )
    # "Getting stale" warning zone: confirmed more than half the grace
    # period ago but not yet past it - not a failure, an early signal
    # that the SBC sync subsystem may be struggling to re-confirm.
    aging = await conn.fetchval(
        f"""
        SELECT count(*) FROM priority_triggers t
        WHERE (t.expires_at IS NULL OR t.expires_at > now())
          AND t.trigger_type != 'manual_override'
          AND t.last_confirmed_at <= now() - (($1 / 2.0) * interval '1 hour')
          AND t.last_confirmed_at > now() - ($1 * interval '1 hour')
        """,
        grace_hours,
    )

    return {
        "active_triggers_by_type": {r["trigger_type"]: int(r["n"]) for r in active_by_type},
        "expired_triggers_still_present_rows": int(expired_still_present or 0),
        "triggers_missing_expiry_non_manual": int(missing_expiry or 0),
        "triggers_referencing_missing_card": int(orphaned or 0),
        "triggers_where_effective_tier_understates_forced_tier": tier_mismatches,
        "sbc_triggers_by_event": [
            {"event_id": r["event_id"], "title": r["title"], "card_count": int(r["n"])} for r in sbc_by_event
        ],
        "earliest_active_expiry": _iso(expiry_bounds["earliest"]) if expiry_bounds else None,
        "latest_active_expiry": _iso(expiry_bounds["latest"]) if expiry_bounds else None,
        "triggers_aging_toward_grace_period_expiry": int(aging or 0),
        "confirmation_grace_hours": grace_hours,
    }


# =============================================================================
# 3. Classification integrity
# =============================================================================

async def report_classification_integrity(
    conn: asyncpg.Connection, config, sample_per_tier: int,
) -> Dict[str, Any]:
    no_reason_codes = await conn.fetchval(
        "SELECT count(*) FROM card_priority WHERE reason_codes IS NULL OR reason_codes = '{}'"
    )
    # A reason code is expected to start with the tier it justified
    # (e.g. "CRITICAL_...", "DORMANT_..."). A card whose baseline_tier
    # doesn't match ANY of its own reason codes' prefixes is either a
    # stale row (rules changed since it was written) or a real bug.
    contradictory = await conn.fetchval(
        """
        SELECT count(*) FROM card_priority
        WHERE NOT EXISTS (
            SELECT 1 FROM unnest(reason_codes) rc WHERE rc LIKE baseline_tier || '\\_%' ESCAPE '\\'
        ) AND reason_codes != '{}'
        """
    )
    dormant_with_trigger = await conn.fetchval(
        f"""
        SELECT count(*) FROM card_priority cp
        WHERE cp.effective_tier = 'DORMANT'
          AND EXISTS (
              SELECT 1 FROM priority_triggers t
              WHERE t.card_id = cp.card_id
                AND (t.expires_at IS NULL OR t.expires_at > now())
                AND (t.trigger_type = 'manual_override' OR t.last_confirmed_at > now() - ($1 * interval '1 hour'))
          )
        """,
        config.trigger_confirmation_grace_hours,
    )
    low_dormant_despite_sales = await conn.fetchval(
        """
        SELECT count(*) FROM card_priority cp
        JOIN futgg_market_snapshot ms ON ms.source_card_id = cp.card_id
        WHERE cp.baseline_tier IN ('LOW', 'DORMANT') AND ms.sales_count >= $1
        """,
        config.rule_normal_min_sales_count,
    )
    high_critical_no_reason = await conn.fetchval(
        """
        SELECT count(*) FROM card_priority
        WHERE baseline_tier IN ('HIGH', 'CRITICAL')
          AND COALESCE(liquidity_component, 0) < $1
          AND COALESCE(card_type_component, 0) < $2
        """,
        config.rule_high_liquidity_min, config.rule_high_card_type_min,
    )
    never_scraped_delayed = await conn.fetchval(
        """
        SELECT count(DISTINCT e.card_id)
        FROM priority_engine_events e
        JOIN futgg_players p ON p.source_card_id = e.card_id
        WHERE e.reason = 'conservative_demotion' AND p.next_sales_due_at IS NULL
        """
    )
    new_cards_wrongly_eligible = await conn.fetchval(
        """
        SELECT count(*) FROM futgg_players p
        JOIN card_priority cp ON cp.card_id = p.source_card_id
        WHERE cp.effective_tier = 'DORMANT'
          AND p.first_seen_at > now() - ($1 * interval '1 day')
          AND EXISTS (
              SELECT 1 FROM priority_engine_events e
              WHERE e.card_id = p.source_card_id AND e.reason = 'conservative_demotion'
                AND e.occurred_at > now() - interval '7 days'
          )
        """,
        config.conservative_demotion_min_card_age_days,
    )

    invariance = await check_classification_invariance(conn, sample_per_tier)

    return {
        "cards_with_no_reason_codes": int(no_reason_codes or 0),
        "cards_with_contradictory_reason_codes": int(contradictory or 0),
        "dormant_effective_tier_despite_active_trigger": int(dormant_with_trigger or 0),
        "low_or_dormant_baseline_despite_meaningful_sales": int(low_dormant_despite_sales or 0),
        "high_or_critical_with_no_observable_driver": int(high_critical_no_reason or 0),
        "never_scraped_cards_ever_delayed_by_demotion": int(never_scraped_delayed or 0),
        "new_cards_actually_demoted_within_min_age_window": int(new_cards_wrongly_eligible or 0),
        "classification_invariance_check": invariance,
    }


async def check_classification_invariance(conn: asyncpg.Connection, sample_per_tier: int) -> Dict[str, Any]:
    """For a sample of live cards per baseline tier, recompute
    compute_priority() at two different `as_of` timestamps (30 days
    apart) using the SAME stored inputs, and confirm baseline_tier
    doesn't change. Read-only: samples real rows, never writes.
    This is the direct regression test for migration 042's original bug
    (staleness silently re-promoting a card) run against live data."""
    mismatches: List[Dict[str, Any]] = []
    checked = 0
    for tier in TIERS:
        rows = await conn.fetch(
            """
            SELECT ms.source_card_id, ms.rarity, ms.sales_count, ms.sales_window_span_minutes,
                   ms.sales_dispersion_ratio, COALESCE(wp.watcher_count, 0) AS watcher_count
            FROM card_priority cp
            JOIN futgg_market_snapshot ms ON ms.source_card_id = cp.card_id
            LEFT JOIN watchlist_popularity wp ON wp.card_id = cp.card_id
            WHERE cp.baseline_tier = $1
            ORDER BY random()
            LIMIT $2
            """,
            tier, sample_per_tier,
        )
        for row in rows:
            checked += 1
            snapshot = dict(row)
            watcher_count = snapshot.pop("watcher_count")
            early = compute_priority(
                snapshot, watcher_count=watcher_count,
                as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            late = compute_priority(
                snapshot, watcher_count=watcher_count,
                as_of=datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
            if early.baseline_tier != late.baseline_tier:
                mismatches.append({
                    "card_id": row["source_card_id"],
                    "tier_at_earlier_as_of": early.baseline_tier,
                    "tier_at_later_as_of": late.baseline_tier,
                })
    return {"cards_checked": checked, "as_of_dependent_mismatches": mismatches}


# =============================================================================
# 4. Conservative-demotion projection
# =============================================================================

_EXCLUSION_ORDER = [
    ("never_scraped_or_missing_timestamps", lambda r: r["next_sales_due_at"] is None or r["last_sales_status_at"] is None),
    ("active_trigger", lambda r: r["has_active_trigger"]),
    ("premium_card_type", lambda r: (r["card_type_component"] or 0.0) > PRIORITY_ENGINE_CONFIG.conservative_demotion_max_card_type_score),
    ("recent_meaningful_sales", lambda r: r["sales_count"] > 0),
    ("user_interest_above_threshold", lambda r: r["watcher_count"] > PRIORITY_ENGINE_CONFIG.conservative_demotion_watcher_max),
    ("newly_released", lambda r: r["first_seen_at"] is None or (
        datetime.now(timezone.utc) - r["first_seen_at"]
    ).days < PRIORITY_ENGINE_CONFIG.conservative_demotion_min_card_age_days),
]


async def report_conservative_demotion_projection(conn: asyncpg.Connection, config) -> Dict[str, Any]:
    """Computes what Milestone 1.5 WOULD do if the flag were enabled
    right now, regardless of its actual current setting - a pure
    projection over already-stored data, no writes."""
    total_active = await conn.fetchval("SELECT count(*) FROM futgg_market_snapshot WHERE is_active")

    candidates = await conn.fetch(
        f"""
        SELECT p.source_card_id, p.rarity, p.first_seen_at, p.next_sales_due_at, p.last_sales_status_at,
               cp.card_type_component, COALESCE(ms.sales_count, 0) AS sales_count,
               COALESCE(wp.watcher_count, 0) AS watcher_count,
               EXISTS (
                   SELECT 1 FROM priority_triggers t
                   WHERE t.card_id = p.source_card_id
                     AND (t.expires_at IS NULL OR t.expires_at > now())
                     AND (t.trigger_type = 'manual_override' OR t.last_confirmed_at > now() - ($1 * interval '1 hour'))
               ) AS has_active_trigger
        FROM futgg_players p
        JOIN card_priority cp ON cp.card_id = p.source_card_id
        LEFT JOIN futgg_market_snapshot ms ON ms.source_card_id = p.source_card_id
        LEFT JOIN watchlist_popularity wp ON wp.card_id = p.source_card_id
        WHERE cp.effective_tier = 'DORMANT' AND p.is_active
        """,
        config.trigger_confirmation_grace_hours,
    )

    eligible: List[asyncpg.Record] = []
    exclusion_counts: Dict[str, int] = {name: 0 for name, _ in _EXCLUSION_ORDER}
    for row in candidates:
        as_of = datetime.now(timezone.utc)
        is_eligible = is_eligible_for_conservative_demotion(
            card_type_component=row["card_type_component"] or 0.0,
            sales_count=int(row["sales_count"]),
            watcher_count=int(row["watcher_count"]),
            has_active_trigger=bool(row["has_active_trigger"]),
            first_seen_at=row["first_seen_at"],
            next_sales_due_at=row["next_sales_due_at"],
            last_sales_status_at=row["last_sales_status_at"],
            as_of=as_of,
            config=config,
        )
        if is_eligible:
            eligible.append(row)
            continue
        for name, predicate in _EXCLUSION_ORDER:
            if predicate(row):
                exclusion_counts[name] += 1
                break

    by_rarity: Dict[str, int] = {}
    for row in eligible:
        by_rarity[row["rarity"] or "unknown"] = by_rarity.get(row["rarity"] or "unknown", 0) + 1

    intervals_minutes = [
        (row["next_sales_due_at"] - row["last_sales_status_at"]).total_seconds() / 60.0
        for row in eligible
        if row["next_sales_due_at"] and row["last_sales_status_at"]
    ]
    current_avg_interval = sum(intervals_minutes) / len(intervals_minutes) if intervals_minutes else None
    proposed_interval = float(config.conservative_demotion_max_interval_minutes)

    eligible_count = len(eligible)
    # Formula, stated explicitly (not just computed): requests/day for N
    # cards each refreshed once per `interval_minutes` = N * (1440 /
    # interval_minutes). "Before" uses each eligible card's OWN current
    # empirical interval (next_sales_due_at - last_sales_status_at) summed
    # per-card rather than one averaged rate, so a mixed population of
    # slightly-different current intervals is represented honestly rather
    # than smoothed into a single number first.
    requests_per_day_before = sum(1440.0 / m for m in intervals_minutes) if intervals_minutes else None
    requests_per_day_after = eligible_count * (1440.0 / proposed_interval) if proposed_interval else None
    reduction = (
        requests_per_day_before - requests_per_day_after
        if requests_per_day_before is not None and requests_per_day_after is not None
        else None
    )

    return {
        "note": "Projection as if PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION were enabled right now, computed from already-stored data. Does not reflect the flag's actual current value.",
        "eligible_card_count": eligible_count,
        "eligible_pct_of_active_catalogue": _pct(eligible_count, total_active or 0),
        "eligible_by_rarity": by_rarity,
        "current_avg_interval_minutes": round(current_avg_interval, 1) if current_avg_interval is not None else None,
        "proposed_interval_minutes": proposed_interval,
        "estimate_formula": "requests_per_day = sum_over_cards(1440 / interval_minutes_for_that_card); 'after' uses the flat proposed_interval_minutes for every eligible card",
        "estimated_requests_per_day_before": round(requests_per_day_before, 1) if requests_per_day_before is not None else None,
        "estimated_requests_per_day_after": round(requests_per_day_after, 1) if requests_per_day_after is not None else None,
        "estimated_absolute_reduction_per_day": round(reduction, 1) if reduction is not None else None,
        "estimated_pct_reduction": _pct(round(reduction), round(requests_per_day_before)) if reduction and requests_per_day_before else None,
        "excluded_by_reason": exclusion_counts,
        "excluded_reasons_not_applicable_this_milestone": {
            "current_opportunity": "opportunity_component is always 0 in Milestone 1 (deferred - see priority_recompute.py's module docstring); not a demotion-eligibility criterion yet",
            "recent_abnormal_price_movement": "volatility_component is not currently part of the demotion-eligibility predicate; not fabricated here",
        },
    }


# =============================================================================
# 5. Scheduling health
# =============================================================================

async def report_scheduling_health(conn: asyncpg.Connection, config) -> Dict[str, Any]:
    overdue_by_tier = await conn.fetch(
        """
        SELECT cp.effective_tier, count(*) AS n
        FROM futgg_players p
        JOIN card_priority cp ON cp.card_id = p.source_card_id
        WHERE p.is_active AND p.next_sales_due_at IS NOT NULL AND p.next_sales_due_at < now()
        GROUP BY cp.effective_tier
        """
    )
    overdue_stats = await conn.fetchrow(
        """
        SELECT
            percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (now() - p.next_sales_due_at)) / 60.0) AS median_minutes,
            max(EXTRACT(EPOCH FROM (now() - p.next_sales_due_at)) / 60.0) AS max_minutes
        FROM futgg_players p
        WHERE p.is_active AND p.next_sales_due_at IS NOT NULL AND p.next_sales_due_at < now()
        """
    )
    oldest_scrape_by_tier = await conn.fetch(
        """
        SELECT cp.effective_tier, min(p.last_sales_status_at) AS oldest
        FROM futgg_players p
        JOIN card_priority cp ON cp.card_id = p.source_card_id
        WHERE p.is_active AND p.last_sales_status_at IS NOT NULL
        GROUP BY cp.effective_tier
        """
    )
    beyond_ceiling = await conn.fetchval(
        """
        SELECT count(*) FROM futgg_players p
        WHERE p.is_active AND p.next_sales_due_at IS NOT NULL AND p.last_sales_status_at IS NOT NULL
          AND EXTRACT(EPOCH FROM (p.next_sales_due_at - p.last_sales_status_at)) / 60.0 > $1
        """,
        config.conservative_demotion_max_interval_minutes,
    )
    negative_gap = await conn.fetchval(
        """
        SELECT count(*) FROM futgg_players p
        WHERE p.is_active AND p.next_sales_due_at IS NOT NULL AND p.last_sales_status_at IS NOT NULL
          AND p.next_sales_due_at < p.last_sales_status_at
        """
    )
    no_due_timestamp = await conn.fetchval(
        "SELECT count(*) FROM futgg_players WHERE is_active AND next_sales_due_at IS NULL"
    )
    beyond_ceiling_but_demoted = await conn.fetchval(
        """
        SELECT count(DISTINCT e.card_id) FROM priority_engine_events e
        JOIN futgg_players p ON p.source_card_id = e.card_id
        WHERE e.reason = 'conservative_demotion'
          AND p.next_sales_due_at IS NOT NULL AND p.last_sales_status_at IS NOT NULL
          AND EXTRACT(EPOCH FROM (p.next_sales_due_at - p.last_sales_status_at)) / 60.0 > $1 + 5
        """,
        config.conservative_demotion_max_interval_minutes,
    )
    repeated_mutations = await conn.fetch(
        """
        SELECT card_id, count(*) AS n
        FROM priority_engine_events
        WHERE reason = 'conservative_demotion' AND occurred_at > now() - interval '24 hours'
        GROUP BY card_id
        HAVING count(*) > 1
        ORDER BY n DESC
        LIMIT 20
        """
    )

    return {
        "overdue_cards_by_effective_tier": {r["effective_tier"]: int(r["n"]) for r in overdue_by_tier},
        "overdue_median_minutes": round(overdue_stats["median_minutes"], 1) if overdue_stats and overdue_stats["median_minutes"] is not None else None,
        "overdue_max_minutes": round(overdue_stats["max_minutes"], 1) if overdue_stats and overdue_stats["max_minutes"] is not None else None,
        "oldest_successful_scrape_by_effective_tier": {
            r["effective_tier"]: _iso(r["oldest"]) for r in oldest_scrape_by_tier
        },
        "cards_beyond_discovery_ceiling": int(beyond_ceiling or 0),
        "cards_with_negative_due_gap": int(negative_gap or 0),
        "cards_with_no_due_timestamp": int(no_due_timestamp or 0),
        "demoted_cards_exceeding_ceiling_by_over_5min": int(beyond_ceiling_but_demoted or 0),
        "cards_with_repeated_demotion_writes_in_24h": [
            {"card_id": r["card_id"], "count": int(r["n"])} for r in repeated_mutations
        ],
        "discovery_ceiling_minutes_checked_against": config.conservative_demotion_max_interval_minutes,
    }


# =============================================================================
# 6b. SBC resource-demand summary
# =============================================================================
# SBC relevance model revision (production-readiness review). See
# app/services/sbc_demand_classifier.py's own module docstring for the
# full "why" - broad requirements (TOTW, rating bands, promo types, ...)
# are aggregated market-segment signals here, never individual card
# triggers.

async def report_resource_demand_summary(conn: asyncpg.Connection) -> Dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT
            resource_type, resource_value,
            count(DISTINCT challenge_id) AS contributing_challenges,
            count(DISTINCT source_event_id) AS contributing_events,
            sum(required_count) AS total_required_count,
            min(event_started_at) AS oldest_event_started_at,
            max(event_started_at) AS newest_event_started_at,
            min(event_expires_at) AS nearest_expiry,
            bool_or(repeatable) AS any_repeatable,
            avg(decayed_weight) AS avg_decayed_weight,
            sum(required_count * COALESCE(decayed_weight, 0)) AS weighted_active_demand,
            count(*) FILTER (WHERE last_confirmed_at >= now() - interval '6 hours') AS added_last_6h,
            count(*) FILTER (WHERE last_confirmed_at >= now() - interval '24 hours') AS added_last_24h,
            count(*) FILTER (WHERE last_confirmed_at >= now() - interval '72 hours') AS added_last_72h,
            array_agg(DISTINCT market_status) AS market_statuses
        FROM sbc_resource_demand
        WHERE ambiguity_status = 'CONFIRMED'
          AND (event_expires_at IS NULL OR event_expires_at > now())
        GROUP BY resource_type, resource_value
        ORDER BY weighted_active_demand DESC NULLS LAST
        """
    )
    stored_only_ambiguous = await conn.fetchval(
        "SELECT count(*) FROM sbc_resource_demand WHERE ambiguity_status != 'CONFIRMED'"
    )
    return {
        "active_segments": [
            {
                "resource_type": r["resource_type"],
                "resource_value": r["resource_value"],
                "contributing_challenges": int(r["contributing_challenges"]),
                "contributing_events": int(r["contributing_events"]),
                "total_required_count": int(r["total_required_count"] or 0),
                "oldest_event_started_at": _iso(r["oldest_event_started_at"]),
                "newest_event_started_at": _iso(r["newest_event_started_at"]),
                "nearest_expiry": _iso(r["nearest_expiry"]),
                "any_repeatable": bool(r["any_repeatable"]),
                "avg_decayed_weight": round(float(r["avg_decayed_weight"]), 4) if r["avg_decayed_weight"] is not None else None,
                "weighted_active_demand": round(float(r["weighted_active_demand"]), 4) if r["weighted_active_demand"] is not None else None,
                "added_last_6h": int(r["added_last_6h"]),
                "added_last_24h": int(r["added_last_24h"]),
                "added_last_72h": int(r["added_last_72h"]),
                "market_statuses": list(r["market_statuses"] or []),
            }
            for r in rows
        ],
        "stored_but_not_active_ambiguous_or_unparsed_rows": int(stored_only_ambiguous or 0),
    }


# =============================================================================
# 6c. SBC identity-trigger summary
# =============================================================================
# Re-derives the SAME accept/reject decision app/services/sbc_card_
# matching.py::sync_sbc_identity_triggers() makes, read-only - no writes,
# same "as-if" preview pattern report_conservative_demotion_projection()
# already uses. This is what answers "why did/didn't this requirement
# create individual triggers" for an operator without needing to add a
# second persisted diagnostics table just to explain a decision the live
# sync already makes deterministically from the same inputs.

async def report_identity_trigger_summary(conn: asyncpg.Connection, config) -> Dict[str, Any]:
    from app.services.sbc_card_matching import _ACTIVE_FILTER_ROWS_QUERY, _row_to_parsed_requirement, _eligible_pool_card_ids
    from app.services.sbc_demand_classifier import evaluate_identity_pool, is_identity_requirement

    as_of = datetime.now(timezone.utc)
    rows = await conn.fetch(_ACTIVE_FILTER_ROWS_QUERY)
    results = []
    for row in rows:
        req = _row_to_parsed_requirement(row)
        if row["ambiguous"] or not is_identity_requirement(req):
            continue
        pool_ids = await _eligible_pool_card_ids(conn, req)
        pool_size = len(pool_ids)
        event_started_at = row["starts_at"] or row["first_seen_at"]
        already_active = False
        if pool_size:
            already_active = bool(await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM priority_triggers
                    WHERE card_id = ANY($1::bigint[]) AND source_event_id = $2
                      AND trigger_type = 'sbc_match'
                      AND (expires_at IS NULL OR expires_at > now())
                )
                """,
                pool_ids, row["event_id"],
            ))
        gate = evaluate_identity_pool(
            required_count=row["min_count"], pool_size=pool_size,
            is_new_trigger=not already_active, event_started_at=event_started_at,
            as_of=as_of, config=config,
        )
        event_age_hours = (
            round((as_of - event_started_at).total_seconds() / 3600.0, 1) if event_started_at else None
        )
        results.append({
            "source_event_id": row["event_id"],
            "event_title": row["title"],
            "challenge_id": row["challenge_id"],
            "raw_text": row["raw_text"],
            "required_count": row["min_count"],
            "eligible_pool_size": pool_size,
            "concentration_ratio": gate.concentration_ratio,
            "event_age_hours": event_age_hours,
            "accepted": gate.accepted,
            "rejection_reason": None if gate.accepted else gate.reason,
            "triggered_card_count": pool_size if gate.accepted else 0,
        })

    accepted = [r for r in results if r["accepted"]]
    rejected = [r for r in results if not r["accepted"]]
    return {
        "evaluated_identity_requirements": len(results),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejected_by_reason": {
            reason: sum(1 for r in rejected if r["rejection_reason"] == reason)
            for reason in sorted({r["rejection_reason"] for r in rejected})
        },
        "total_triggered_cards": sum(r["triggered_card_count"] for r in accepted),
        "decisions": results,
    }


# =============================================================================
# 6. Audit health
# =============================================================================

async def report_audit_health(conn: asyncpg.Connection) -> Dict[str, Any]:
    # Pulls raw rows per window and ranks tiers in Python using the same
    # source of truth (priority_config.tier_rank) production code itself
    # uses, rather than duplicating the CRITICAL..DORMANT ordering as a
    # throwaway SQL CASE expression.
    async def _counts_for_window(hours: int) -> Dict[str, int]:
        rows = await conn.fetch(
            """
            SELECT previous_effective_tier, new_effective_tier, new_due_at
            FROM priority_engine_events
            WHERE occurred_at > now() - ($1 * interval '1 hour')
            """,
            hours,
        )
        promotions = demotions = schedule_writes = 0
        for r in rows:
            if r["new_due_at"] is not None:
                schedule_writes += 1
            prev, new = r["previous_effective_tier"], r["new_effective_tier"]
            if prev is None or new is None or prev == new:
                continue
            if tier_rank(new) < tier_rank(prev):
                promotions += 1
            elif tier_rank(new) > tier_rank(prev):
                demotions += 1
        return {"promotions": promotions, "demotions": demotions, "schedule_writes": schedule_writes, "total_events": len(rows)}

    windows = {"1h": await _counts_for_window(1), "24h": await _counts_for_window(24), "7d": await _counts_for_window(24 * 7)}

    missing_tiers = await conn.fetchval(
        """
        SELECT count(*) FROM priority_engine_events
        WHERE new_baseline_tier IS NULL OR new_effective_tier IS NULL
           OR NOT (new_baseline_tier = ANY($1::text[]))
           OR NOT (new_effective_tier = ANY($1::text[]))
        """,
        list(TIERS),
    )
    missing_reason_codes = await conn.fetchval(
        "SELECT count(*) FROM priority_engine_events WHERE reason_codes IS NULL OR reason_codes = '{}'"
    )
    missing_engine_version = await conn.fetchval(
        "SELECT count(*) FROM priority_engine_events WHERE engine_version IS NULL OR engine_version = ''"
    )
    # A row logged with reason NOT related to scheduling should never
    # carry a due-time mutation, and conservative_demotion rows should
    # always show new_due_at > previous_due_at (push-later-only).
    mismatched_due_mutation = await conn.fetchval(
        """
        SELECT count(*) FROM priority_engine_events
        WHERE (reason != 'conservative_demotion' AND new_due_at IS NOT NULL)
           OR (reason = 'conservative_demotion' AND (new_due_at IS NULL OR previous_due_at IS NULL OR new_due_at <= previous_due_at))
        """
    )
    top_cards = await conn.fetch(
        """
        SELECT card_id, count(*) AS n FROM priority_engine_events
        GROUP BY card_id ORDER BY n DESC LIMIT 20
        """
    )

    return {
        "events_1h": windows["1h"], "events_24h": windows["24h"], "events_7d": windows["7d"],
        "events_with_invalid_or_missing_tier": int(missing_tiers or 0),
        "events_missing_reason_codes": int(missing_reason_codes or 0),
        "events_missing_engine_version": int(missing_engine_version or 0),
        "events_with_due_time_mutation_inconsistent_with_reason": int(mismatched_due_mutation or 0),
        "top_cards_by_event_count": [{"card_id": r["card_id"], "event_count": int(r["n"])} for r in top_cards],
    }


# =============================================================================
# Orchestration / output
# =============================================================================

async def build_report(dsn: str, sample_per_tier: int) -> Dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        config = PRIORITY_ENGINE_CONFIG
        grace_hours = config.trigger_confirmation_grace_hours
        report: Dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "engine_version": config.version,
            "conservative_demotion_flag_currently": config.allow_conservative_demotion,
        }
        try:
            report["tier_distribution"] = await report_tier_distribution(conn, grace_hours)
            report["trigger_health"] = await report_trigger_health(conn, grace_hours)
            report["classification_integrity"] = await report_classification_integrity(conn, config, sample_per_tier)
            report["conservative_demotion_projection"] = await report_conservative_demotion_projection(conn, config)
            report["scheduling_health"] = await report_scheduling_health(conn, config)
            report["audit_health"] = await report_audit_health(conn)
            report["resource_demand_summary"] = await report_resource_demand_summary(conn)
            report["identity_trigger_summary"] = await report_identity_trigger_summary(conn, config)
        except asyncpg.exceptions.UndefinedTableError as e:
            report["error"] = f"Required table not found - has migration 044/045/046/048/049 run yet? ({e})"
        return report
    finally:
        await conn.close()


def _print_human(report: Dict[str, Any]) -> None:
    print(f"\n{'=' * 70}\nAdaptive Priority Engine — Production Validation Report\n{'=' * 70}")
    print(f"Generated: {report['generated_at']}")
    print(f"Engine version: {report['engine_version']}")
    print(f"PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION currently: {report['conservative_demotion_flag_currently']}")
    if "error" in report:
        print(f"\n⚠ {report['error']}")
        return

    td = report["tier_distribution"]
    print(f"\n--- Tier Distribution ---")
    print(f"Active cards: {td['total_active_cards']}  |  Classified: {td['total_classified']}  |  Missing: {td['cards_missing_classification']}")
    print("Baseline tier:  " + "  ".join(f"{t}={td['cards_by_baseline_tier'][t]}({td['baseline_pct'][t]}%)" for t in TIERS))
    print("Effective tier: " + "  ".join(f"{t}={td['cards_by_effective_tier'][t]}({td['effective_pct'][t]}%)" for t in TIERS))
    print(f"Baseline != effective: {td['cards_where_baseline_differs_from_effective']}  |  With active trigger: {td['cards_with_active_trigger']}")

    th = report["trigger_health"]
    print(f"\n--- Trigger Health ---")
    print(f"Active by type: {th['active_triggers_by_type']}")
    print(f"Missing expiry (non-manual): {th['triggers_missing_expiry_non_manual']}  |  Orphaned (card gone): {th['triggers_referencing_missing_card']}")
    print(f"Effective-tier understates forced tier: {th['triggers_where_effective_tier_understates_forced_tier']}  |  Aging toward grace expiry: {th['triggers_aging_toward_grace_period_expiry']}")
    print(f"Active expiry range: {th['earliest_active_expiry']} .. {th['latest_active_expiry']}")

    ci = report["classification_integrity"]
    print(f"\n--- Classification Integrity ---")
    print(f"No reason codes: {ci['cards_with_no_reason_codes']}  |  Contradictory: {ci['cards_with_contradictory_reason_codes']}")
    print(f"DORMANT effective despite active trigger (should be 0): {ci['dormant_effective_tier_despite_active_trigger']}")
    print(f"LOW/DORMANT despite meaningful sales: {ci['low_or_dormant_baseline_despite_meaningful_sales']}")
    print(f"HIGH/CRITICAL with no observable driver: {ci['high_or_critical_with_no_observable_driver']}")
    print(f"Never-scraped cards ever delayed (should be 0): {ci['never_scraped_cards_ever_delayed_by_demotion']}")
    print(f"New cards actually demoted within min-age window (should be 0): {ci['new_cards_actually_demoted_within_min_age_window']}")
    inv = ci["classification_invariance_check"]
    print(f"as_of invariance: checked {inv['cards_checked']} cards, {len(inv['as_of_dependent_mismatches'])} mismatches (should be 0)")

    dp = report["conservative_demotion_projection"]
    print(f"\n--- Conservative Demotion Projection (as-if enabled) ---")
    print(f"Eligible: {dp['eligible_card_count']} ({dp['eligible_pct_of_active_catalogue']}% of catalogue)")
    print(f"By rarity: {dp['eligible_by_rarity']}")
    print(f"Interval: current avg {dp['current_avg_interval_minutes']}min -> proposed {dp['proposed_interval_minutes']}min")
    print(f"Est. requests/day: {dp['estimated_requests_per_day_before']} -> {dp['estimated_requests_per_day_after']} (reduction {dp['estimated_absolute_reduction_per_day']}, {dp['estimated_pct_reduction']}%)")
    print(f"Excluded by reason: {dp['excluded_by_reason']}")

    sh = report["scheduling_health"]
    print(f"\n--- Scheduling Health ---")
    print(f"Overdue by tier: {sh['overdue_cards_by_effective_tier']}")
    print(f"Overdue median/max minutes: {sh['overdue_median_minutes']} / {sh['overdue_max_minutes']}")
    print(f"Beyond discovery ceiling: {sh['cards_beyond_discovery_ceiling']}  |  Negative due gap: {sh['cards_with_negative_due_gap']}  |  No due timestamp: {sh['cards_with_no_due_timestamp']}")
    print(f"Demoted cards exceeding ceiling: {sh['demoted_cards_exceeding_ceiling_by_over_5min']}  |  Repeated 24h demotion writes: {len(sh['cards_with_repeated_demotion_writes_in_24h'])}")

    ah = report["audit_health"]
    print(f"\n--- Audit Health ---")
    print(f"Events 1h/24h/7d: {ah['events_1h']} / {ah['events_24h']} / {ah['events_7d']}")
    print(f"Invalid/missing tier: {ah['events_with_invalid_or_missing_tier']}  |  Missing reason codes: {ah['events_missing_reason_codes']}  |  Missing engine version: {ah['events_missing_engine_version']}")
    print(f"Due-time mutation inconsistent with reason (should be 0): {ah['events_with_due_time_mutation_inconsistent_with_reason']}")
    print(f"Top cards by event count: {ah['top_cards_by_event_count'][:5]}")

    rd = report["resource_demand_summary"]
    print(f"\n--- SBC Resource-Demand Summary ---")
    print(f"Stored-but-inactive (ambiguous/unparsed) rows: {rd['stored_but_not_active_ambiguous_or_unparsed_rows']}")
    for seg in rd["active_segments"][:15]:
        print(
            f"  {seg['resource_type']:<16} {seg['resource_value']:<20} "
            f"challenges={seg['contributing_challenges']} events={seg['contributing_events']} "
            f"required={seg['total_required_count']} weighted_demand={seg['weighted_active_demand']} "
            f"repeatable={seg['any_repeatable']} new_24h={seg['added_last_24h']}"
        )

    it = report["identity_trigger_summary"]
    print(f"\n--- SBC Identity-Trigger Summary ---")
    print(f"Evaluated: {it['evaluated_identity_requirements']}  |  Accepted: {it['accepted_count']}  |  Rejected: {it['rejected_count']}")
    print(f"Rejected by reason: {it['rejected_by_reason']}")
    print(f"Total triggered cards (accepted pools): {it['total_triggered_cards']}")
    for d in it["decisions"][:15]:
        status = "ACCEPTED" if d["accepted"] else f"REJECTED ({d['rejection_reason']})"
        print(
            f"  [{status}] event={d['event_title']!r} required={d['required_count']} "
            f"pool={d['eligible_pool_size']} ratio={d['concentration_ratio']} "
            f"age_h={d['event_age_hours']} raw={d['raw_text']!r}"
        )
    print()


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print the full report as JSON instead of a human summary")
    ap.add_argument("--sample-per-tier", type=int, default=15, help="cards per baseline tier sampled for the as_of invariance check (default 15)")
    args = ap.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    report = await build_report(dsn, args.sample_per_tier)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
    return 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
