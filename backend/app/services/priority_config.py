# app/services/priority_config.py
"""
Tunables for the adaptive scrape-priority engine (priority_engine.py),
following the same pattern futgg_config.py already established for the
valuation engine: every weight/threshold lives in one place, is
env-var-overridable, and is versioned so a scoring run can be attributed
to a specific configuration later.

PHASE 1 NOTE: TIER_*_INTERVAL_MIN below are not yet consulted by any
live scraper - see priority_recompute.py's module docstring for the
phased rollout this belongs to. They exist now so the tier system and
its intended cadence are defined in one place from the start, rather
than bolted on when scheduling wiring actually happens.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict

PRIORITY_ENGINE_VERSION = "priority-1.0"

TIERS = ("CRITICAL", "HIGH", "NORMAL", "LOW", "DORMANT")


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


@dataclass(frozen=True)
class PriorityEngineConfig:
    version: str = PRIORITY_ENGINE_VERSION

    # Component weights - must sum to ~1.0 for priority_score to land in
    # [0,1], but this isn't enforced structurally since a deliberate
    # experiment might want to over/under-weight while tuning.
    weight_liquidity: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_LIQUIDITY", 0.28))
    weight_freshness: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_FRESHNESS", 0.16))
    weight_card_type: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_CARD_TYPE", 0.14))
    weight_opportunity: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_OPPORTUNITY", 0.12))
    weight_user_interest: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_USER_INTEREST", 0.14))
    weight_volatility: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_VOLATILITY", 0.08))
    weight_event: float = field(default_factory=lambda: _env_float("PRIORITY_WEIGHT_EVENT", 0.08))

    # Tier cutoffs on the final blended [0,1] score, checked highest-first.
    tier_critical_min: float = field(default_factory=lambda: _env_float("PRIORITY_TIER_CRITICAL_MIN", 0.85))
    tier_high_min: float = field(default_factory=lambda: _env_float("PRIORITY_TIER_HIGH_MIN", 0.60))
    tier_normal_min: float = field(default_factory=lambda: _env_float("PRIORITY_TIER_NORMAL_MIN", 0.30))
    tier_low_min: float = field(default_factory=lambda: _env_float("PRIORITY_TIER_LOW_MIN", 0.10))
    # Below tier_low_min -> DORMANT.

    # An active SBC/event match floor-clamps a card to at least this
    # tier, regardless of its blended score - see priority_engine.py's
    # compute_priority() for where this is applied. Kept as its own
    # setting (not just "give event_component a huge weight") because a
    # weighted blend can never GUARANTEE promotion the way a floor can -
    # the spec asks for immediate promotion, not "probably soon".
    event_match_floor_tier: str = field(default_factory=lambda: os.getenv("PRIORITY_EVENT_MATCH_FLOOR_TIER", "HIGH"))

    # Card-type component lookup - a card's rarity string (or price_tier
    # fallback) maps to a [0,1] baseline "this kind of card tends to
    # matter" score, independent of its own current liquidity.
    card_type_scores: Dict[str, float] = field(default_factory=lambda: {
        "icon": 1.0, "hero": 0.95, "totw": 0.9, "tots": 1.0,
        "fut birthday": 0.85, "special": 0.75,
        "gold_rare": 0.5, "gold_common": 0.35,
        "silver_rare": 0.25, "silver_common": 0.15, "silver": 0.15,
        "bronze_rare": 0.1, "bronze_common": 0.05, "bronze": 0.05,
    })
    default_card_type_score: float = 0.4  # unrecognised rarity string - assume mid-liquidity gold-ish

    # 48h with no promotion-worthy activity is the spec's own stated
    # demotion trigger.
    inactivity_demote_hours: int = field(default_factory=lambda: _env_int("PRIORITY_INACTIVITY_DEMOTE_HOURS", 48))

    # Refresh cadence per tier, for the sales worker specifically (the
    # expensive per-card Playwright/signed-fetch path) - see
    # auto_sync/futgg_sales_sync.py's SALES_INTERVALS for the existing
    # flat rating-band equivalent this is designed to eventually sit
    # alongside/replace. Not yet consulted anywhere (Phase 1).
    tier_sales_interval_minutes: Dict[str, int] = field(default_factory=lambda: {
        "CRITICAL": _env_int("TIER_CRITICAL_SALES_INTERVAL_MIN", 15),
        "HIGH": _env_int("TIER_HIGH_SALES_INTERVAL_MIN", 60),
        "NORMAL": _env_int("TIER_NORMAL_SALES_INTERVAL_MIN", 240),
        "LOW": _env_int("TIER_LOW_SALES_INTERVAL_MIN", 1440),
        "DORMANT": _env_int("TIER_DORMANT_SALES_INTERVAL_MIN", 4320),
    })


PRIORITY_ENGINE_CONFIG = PriorityEngineConfig()
