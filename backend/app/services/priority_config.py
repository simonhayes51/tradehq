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
    # REVERTED (4 September, ~2h after deploy) - lowering this to 0.75 to
    # give "special" cards a liquidity-independent HIGH floor made things
    # worse, not better, and the live evidence is unambiguous:
    # stale_sales_by_priority_tier before/after -
    #   CRITICAL 1299->1780 (+37%), HIGH 4026->3814 (-5%),
    #   NORMAL 865->2563 (+196%), DORMANT 1550->2091 (+35%),
    #   total 7749->10275 (+33%, i.e. WORSE overall).
    # select_due() (auto_sync/futgg_sales_sync.py) gives CRITICAL/HIGH a
    # fixed 25%-each floor of every cycle's batch, then fills the
    # remaining budget in strict priority order - CRITICAL, HIGH, NORMAL,
    # LOW, DORMANT. Pulling "special" cards into HIGH grew HIGH's due
    # backlog, so HIGH claimed more of that shared overflow pool -
    # directly at NORMAL's expense, since NORMAL sits right below HIGH in
    # that same queue. HIGH's own stale count barely moved (-5%) while
    # NORMAL's nearly tripled: a small, unconfirmed win (no per-rarity
    # breakdown exists to show "special" cards specifically got fresher)
    # bought with a large, measured regression for the far bigger
    # population of ordinary cards in NORMAL. CRITICAL and DORMANT also
    # got worse despite being untouched by this change - the collector's
    # real throughput ceiling (FUT.GG rate limiting, see
    # auto_sync/futgg_sales_sync.py's circuit-breaker machinery) is the
    # actual constraint here, and no internal reallocation of a fixed,
    # insufficient budget can fix that - it can only move which tier pays
    # for the shortfall. Left at 0.85 (icon/hero/tots/totw/fut birthday
    # only) until real collector throughput improves; see this file's
    # git history for the abandoned 0.75 attempt and its numbers.
    rule_high_card_type_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_HIGH_CARD_TYPE_MIN", 0.85))
    rule_normal_liquidity_min: float = field(default_factory=lambda: _env_float("PRIORITY_RULE_NORMAL_LIQUIDITY_MIN", 0.15))
    rule_normal_min_sales_count: int = field(default_factory=lambda: _env_int("PRIORITY_RULE_NORMAL_MIN_SALES_COUNT", 3))
    rule_low_min_sales_count: int = field(default_factory=lambda: _env_int("PRIORITY_RULE_LOW_MIN_SALES_COUNT", 1))
    # Below all of the above -> DORMANT.

    # An active SBC/event trigger floor-clamps effective_tier to at
    # least this - see priority_triggers/get_active_sbc_matched_card_ids.
    event_match_floor_tier: str = field(default_factory=lambda: os.getenv("PRIORITY_EVENT_MATCH_FLOOR_TIER", "HIGH"))

    # A trigger is only "active" if it was re-confirmed within this many
    # hours (see migration 046's header for the full "why" - both SBC
    # writers can leave ends_at stale if an SBC silently disappears
    # upstream, so expires_at alone isn't a safe-enough bound). Must be
    # comfortably longer than the 15min recompute cadence so a single
    # missed/failed sync pass doesn't false-expire a still-valid trigger,
    # but bounded so a genuinely-vanished SBC's promotion doesn't linger
    # indefinitely. manual_override triggers are exempt (see
    # priority_recompute.py's active-trigger query) - there is no
    # automated source to re-confirm them against.
    trigger_confirmation_grace_hours: int = field(default_factory=lambda: _env_int("PRIORITY_TRIGGER_CONFIRMATION_GRACE_HOURS", 72))

    # ---- SBC relevance model: constrained identity-trigger gates -----
    # (see app/services/sbc_demand_classifier.py). A requirement with a
    # named nation/league/club dimension only becomes an individual-card
    # priority_triggers row when its eligible pool is small enough and
    # the required count is a meaningful fraction of it - "6 Saudi League
    # silver players from a pool of 40" should trigger those 6-ish cards;
    # "1 Premier League player from 1,000+ cards" should not trigger any
    # single one of them. Defaults below are a first-pass estimate, not
    # validated against a full season of real SBC data - see the
    # production-readiness review's report for the specific counts they
    # were checked against.
    identity_trigger_max_pool_size: int = field(default_factory=lambda: _env_int("SBC_IDENTITY_MAX_POOL_SIZE", 60))
    identity_trigger_min_concentration_ratio: float = field(default_factory=lambda: _env_float("SBC_IDENTITY_MIN_CONCENTRATION_RATIO", 0.12))
    # A pool at or below this size is accepted regardless of the
    # concentration ratio - "3 players from a small club" (pool=4,
    # ratio=0.75) obviously qualifies, but so should "1 player from a
    # club with only 5 cards total" (ratio=0.2, below the ratio gate
    # above) precisely because the pool itself is tiny.
    identity_trigger_small_pool_override: int = field(default_factory=lambda: _env_int("SBC_IDENTITY_SMALL_POOL_OVERRIDE", 15))
    # New identity triggers are not created for a requirement whose event
    # is older than this - an old, likely-decaying SBC promoting a fresh
    # individual card for the first time is a weaker claim than
    # re-confirming one it already promoted. Re-confirmation of an
    # already-active trigger is NOT gated on this (that would fight the
    # grace-period mechanism migration 046 already provides).
    identity_trigger_max_event_age_hours_for_new: int = field(default_factory=lambda: _env_int("SBC_IDENTITY_MAX_NEW_EVENT_AGE_HOURS", 240))

    # ---- SBC relevance model: resource-demand time decay -------------
    # (see app/services/sbc_demand_classifier.py::compute_demand_weight).
    # Age-band decay, in hours-since-event-start -> weight in [0,1].
    # "Reviewed against current data" per the task's own instruction:
    # checked against this review's live shadow data (12 active source
    # events, oldest ~"a few days", none older than ~2 weeks) - there was
    # no multi-month-old SBC in the sample to calibrate the tail bands
    # against, so 3-7d/7d+ are a reasoned extrapolation of the 0-72h
    # bands' slope, not independently confirmed. Revisit once real data
    # spans that range.
    demand_decay_bands_hours: Dict[float, float] = field(default_factory=lambda: {
        6.0: 1.0,     # 0-6h: full strength
        24.0: 0.8,    # 6-24h: strong
        72.0: 0.5,    # 1-3d: moderate
        168.0: 0.2,   # 3-7d: weak
    })
    # Beyond the last band's upper bound (168h/7d here): minimal unless
    # reinforced (market_status='MARKET_CONFIRMED' - not computed by this
    # round, see sbc_resource_demand's own header).
    demand_decay_minimal_weight: float = field(default_factory=lambda: _env_float("SBC_DEMAND_DECAY_MINIMAL_WEIGHT", 0.05))
    # A permanent/tutorial SBC (event_expires_at NULL or further out than
    # this many days) never contributes more than the minimal weight
    # above, regardless of how recently it was first seen - "expires in
    # 3 years" is not "urgent demand", it's a standing onboarding
    # objective. Repeatable (non-permanent) SBCs are NOT exempted here -
    # they still decay on the normal age bands off event_started_at, so
    # a repeatable upgrade doesn't sit at full strength forever purely
    # for being repeatable (see the module's own test coverage).
    demand_permanent_event_expiry_threshold_days: int = field(default_factory=lambda: _env_int("SBC_DEMAND_PERMANENT_EXPIRY_THRESHOLD_DAYS", 180))

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
    # BUG FIX (production-readiness review): the milestone's own spec is
    # "plain bronze or silver" cards are eligible - card_type_scores has
    # silver_rare at 0.25, but the old default here (0.2) excluded it,
    # silently narrowing eligibility to bronze + silver_common/silver
    # only. 0.3 includes every bronze/silver bucket (max 0.25) while
    # still excluding gold_common (0.35) and above.
    conservative_demotion_max_card_type_score: float = field(default_factory=lambda: _env_float("PRIORITY_CONSERVATIVE_DEMOTION_MAX_CARD_TYPE_SCORE", 0.3))
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
    # observability reporting (stale_sales_by_priority_tier), which feeds
    # both readiness.py's health check and EmptyFeedReason.jsx's
    # customer-facing "sales data is behind" message. Milestone 3
    # (auto_sync/futgg_sales_sync.py's select_due() actually scheduling
    # by this same tier) has since shipped, and it exposed these original
    # 15/60/240-minute CRITICAL/HIGH/NORMAL defaults as aspirational
    # rather than real: they were set before Milestone 3 existed, with no
    # live collector to check them against. Live evidence once
    # select_due() started trying to hit them: CRITICAL/HIGH's stale
    # counts stayed flat-to-rising for hours even as overall sales
    # coverage climbed steadily and the collector ran cleanly - because
    # 15 minutes for ~1,700 CRITICAL cards needs ~118 cards/min sustained
    # just for that tier, against a real, FUT.GG-rate-limited ceiling of
    # roughly 12-15 cards/min even during a healthy stretch (see
    # auto_sync's own CIRCUIT_BREAKER_* machinery and
    # LAUNCH_READINESS.md item 10). No scheduling improvement can close
    # that gap - only a realistic target can.
    #
    # CRITICAL now matches the fastest cadence this catalogue has
    # actually sustained in production (the old rating-band system's
    # 95_plus interval, 180min). The rest step up from there in
    # proportion to roughly how much larger each tier's population is
    # (HIGH's stale count alone, at the old target, ran ~2.6x
    # CRITICAL's) - not measured precisely, but a deliberately
    # conservative staircase so the same "aspirational, never checked
    # against real throughput" mistake is not simply repeated one tier
    # down. LOW's new value matches the old rating-band system's
    # under_75 default (2880min), which WAS proven sustainable in
    # production. DORMANT drops from 4320 to 4000 to match
    # auto_sync/futgg_sales_sync.py's own default and stay clear of
    # ENGINE_REJECT_SALES_OLDER_THAN_MINUTES (4320) rather than sitting
    # exactly on it. auto_sync mirrors this dict (cannot import
    # app.services - separate deployment); test_sales_cadence_vs_gate.py
    # asserts the two stay equal.
    tier_sales_interval_minutes: Dict[str, int] = field(default_factory=lambda: {
        "CRITICAL": _env_int("TIER_CRITICAL_SALES_INTERVAL_MIN", 180),
        "HIGH": _env_int("TIER_HIGH_SALES_INTERVAL_MIN", 720),
        "NORMAL": _env_int("TIER_NORMAL_SALES_INTERVAL_MIN", 1440),
        "LOW": _env_int("TIER_LOW_SALES_INTERVAL_MIN", 2880),
        "DORMANT": _env_int("TIER_DORMANT_SALES_INTERVAL_MIN", 4000),
    })

    # Recent-window (not lifetime) watchlist popularity, per the review's
    # "recent-window counts rather than lifetime totals" - the live
    # `watchlist` table has no expiry of its own (a row exists exactly as
    # long as the user is actively watching), so this bounds "recent" by
    # when the watch was added rather than an activity timestamp that
    # doesn't exist on that table.
    watchlist_popularity_window_days: int = field(default_factory=lambda: _env_int("PRIORITY_WATCHLIST_WINDOW_DAYS", 90))


PRIORITY_ENGINE_CONFIG = PriorityEngineConfig()
