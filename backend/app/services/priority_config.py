# app/services/priority_config.py
"""
Tunables for the adaptive scrape-priority engine (priority_engine.py),
following the same pattern futgg_config.py already established for the
valuation engine: every weight/threshold lives in one place, is
env-var-overridable, and is versioned so a classification run can be
attributed to a specific configuration later.

v2 (see migration 044's header for the full "why"): baseline_tier is now
assigned by an explicit rule table (see priority_engine.py), not a
weighted-score cutoff - the thresholds below (`rule_*`) are the
knobs that table consults. `importance_score` is still computed from
weighted components for observability, but no longer decides the tier
by itself, and critically no longer includes any freshness/staleness
term at all - that was migration 042's bug (a DORMANT card aging
exactly as designed would climb back toward promotion on its own
growing staleness).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict

PRIORITY_ENGINE_VERSION = "priority-2.0"

TIERS = ("CRITICAL", "HIGH", "NORMAL", "LOW", "DORMANT")


def tier_rank(tier: str) -> int:
    """Lower rank = higher priority (CRITICAL=0 .. DORMANT=4). Used to
    compare/merge tiers (e.g. effective_tier = the LOWER rank of
    baseline_tier and an active trigger's forced_min_tier)."""
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


def higher_tier(a: str, b: str) -> str:
    return a if tier_rank(a) <= tier_rank(b) else b


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class PriorityEngineConfig:
    version: str = PRIORITY_ENGINE_VERSION

    # ---- importance_score component weights (observability only - do
    # NOT decide baseline_tier, see priority_engine.py's rule table for
    # what actually does). No freshness component exists at all.
    weight_liquidity: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_LIQUIDITY", 0.32))
    weight_card_type: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_CARD_TYPE", 0.16))
    weight_opportunity: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_OPPORTUNITY", 0.14))
    weight_user_interest: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_USER_INTEREST", 0.16))
    weight_volatility: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_VOLATILITY", 0.12))
    weight_event: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_EVENT", 0.10))

    # ---- Rule-table thresholds (priority_engine.py's classify_baseline_
    # tier() consults these directly - the tier comes from the first
    # matching rule, not from importance_score crossing a cutoff).
    rule_critical_liquidity_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_CRITICAL_LIQUIDITY_MIN", 0.75))
    rule_critical_volatility_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_CRITICAL_VOLATILITY_MIN", 0.6))
    rule_critical_opportunity_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_CRITICAL_OPPORTUNITY_MIN", 0.6))
    rule_high_liquidity_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_HIGH_LIQUIDITY_MIN", 0.45))
    rule_high_card_type_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_HIGH_CARD_TYPE_MIN", 0.85))
    rule_normal_liquidity_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_NORMAL_LIQUIDITY_MIN", 0.15))
    rule_normal_min_sales_count: int = field(default_factory=lambda: _env_int("PRIORITY_RULE_NORMAL_MIN_SALES_COUNT", 3))
    rule_low_min_sales_count: int = field(default_factory=lambda: _env_int("PRIORITY_RULE_LOW_MIN_SALES_COUNT", 1))
    # Below all of the above -> DORMANT.

    # An active SBC/event trigger floor-clamps effective_tier to at
    # least this - see priority_triggers/get_active_sbc_matched_card_ids.
    event_match_floor_tier: str = field(default_factory=lambda: os.getenv("PRIORITY_EVENT_MATCH_FLOOR_TIER", "HIGH"))

    # Card-type component lookup - a card's rarity string maps to a
    # [0,1] baseline "this kind of card tends to matter" score.
    card_type_scores: Dict[str, float] = field(default_factory=lambda: {
        "icon": 1.0, "hero": 0.95, "totw": 0.9, "tots": 1.0,
        "fut birthday": 0.85, "special": 0.75,
        "gold_rare": 0.5, "gold_common": 0.35,
        "silver_rare": 0.25, "silver_common": 0.15, "silver": 0.15,
        "bronze_rare": 0.1, "bronze_common": 0.05, "bronze": 0.05,
    })
    default_card_type_score: float = 0.4

    # ---- Milestone 1.5: conservative cost-control demotion. All must
    # be true (see priority_recompute.py::is_eligible_for_conservative_
    # demotion) before next_sales_due_at is ever pushed later.
    allow_conservative_demotion: bool = field(default_factory=lambda: _env_bool("PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION", False))
    conservative_demotion_max_card_type_score: float = field(default_factory=lambda: _env_float("PRIORITY_CONSERVATIVE_DEMOTION_MAX_CARD_TYPE_SCORE", 0.2))
    conservative_demotion_watcher_max: int = field(default_factory=lambda: _env_int("PRIORITY_CONSERVATIVE_DEMOTION_WATCHER_MAX", 1))
    conservative_demotion_min_card_age_days: int = field(default_factory=lambda: _env_int("PRIORITY_CONSERVATIVE_DEMOTION_MIN_CARD_AGE_DAYS", 14))
    # A genuinely eligible card here (bronze/silver, no sales, no
    # interest, no active trigger) is almost always rated <75, which
    # already sits on auto_sync/futgg_sales_sync.py's own flat
    # FUTGG_SALES_UNDER_75_INTERVAL_MIN band (default 2880min/48h) -
    # the write in priority_recompute.py only ever pushes LATER
    # (GREATEST), so a ceiling below that existing floor would be a
    # silent no-op for exactly the population this milestone targets.
    # 4320min/72h is a deliberately modest first step past that
    # existing floor, not the full DORMANT interval a later, more
    # mature milestone might eventually use ("start with conservative
    # maximum intervals" per the review) - short enough to reconsider
    # quickly, long enough to show a real, measurable request-volume
    # reduction for confirmed-inactive tail cards.
    conservative_demotion_max_interval_minutes: int = field(default_factory=lambda: _env_int("PRIORITY_CONSERVATIVE_DEMOTION_MAX_INTERVAL_MIN", 4320))

    # Sales-refresh cadence per tier - used by get_freshness_summary()'s
    # observability reporting today; not yet consulted by any live
    # scheduling write (that's Milestone 3).
    tier_sales_interval_minutes: Dict[str, int] = field(default_factory=lambda: {
        "CRITICAL": _env_int("TIER_CRITICAL_SALES_INTERVAL_MIN", 15),
        "HIGH": _env_int("TIER_HIGH_SALES_INTERVAL_MIN", 60),
        "NORMAL": _env_int("TIER_NORMAL_SALES_INTERVAL_MIN", 240),
        "LOW": _env_int("TIER_LOW_SALES_INTERVAL_MIN", 1440),
        "DORMANT": _env_int("TIER_DORMANT_SALES_INTERVAL_MIN", 4320),
    })

    # Recent-window (not lifetime) watchlist popularity, per the review's
    # "recent-window counts rather than lifetime totals" - the live
    # `watchlist` table has no expiry of its own (a row exists exactly as
    # long as the user is actively watching), so this bounds "recent" by
    # when the watch was added rather than an activity timestamp that
    # doesn't exist on that table.
    watchlist_popularity_window_days: int = field(default_factory=lambda: _env_int("PRIORITY_WATCHLIST_WINDOW_DAYS", 90))


PRIORITY_ENGINE_CONFIG = PriorityEngineConfig()
