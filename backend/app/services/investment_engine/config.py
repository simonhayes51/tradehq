# app/services/investment_engine/config.py
"""
Tunables and feature flags for the Investment Engine, following the same
pattern app/services/priority_config.py already established: every
threshold/flag lives in one place, is env-var-overridable, and the
resulting config object is versioned so a run can be attributed to a
specific configuration later.

None of the FC26 research numbers (40%/50% drawdown, 2-3 day hold, etc.)
are hardcoded as the live decision rule - they inform the DEFAULTS below
(severity percentile bands, reference range) but the actual trigger is
always evaluated dynamically against the current cycle's own observed
distribution (see drawdown.py) or against these named, overridable
settings. See INVESTMENT_FC26_REF_DRAWDOWN_LOW/HIGH's own comment for
why an absolute FC26 number appears at all despite that.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

INVESTMENT_ENGINE_VERSION = "investment-1.0"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


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
class InvestmentEngineConfig:
    version: str = INVESTMENT_ENGINE_VERSION

    # ---- Feature flags --------------------------------------------------
    enabled: bool = field(default_factory=lambda: _env_bool("INVESTMENT_ENGINE_ENABLED", False))
    # Shadow mode: calculate and store everything, publish nothing to
    # users. Both flags default OFF - the engine is entirely inert until
    # explicitly enabled, and even once enabled it stays in shadow mode
    # (calculated, never shown) until this is separately turned on.
    publish_signals: bool = field(default_factory=lambda: _env_bool("INVESTMENT_ENGINE_PUBLISH_SIGNALS", False))
    fc26_prior_enabled: bool = field(default_factory=lambda: _env_bool("INVESTMENT_ENGINE_FC26_PRIOR_ENABLED", True))

    # ---- Scheduling -------------------------------------------------------
    # FC27 has live intraday data, so this can run far more often than
    # FC26's own daily-resolution backtest cadence - default 5 minutes,
    # comparable to the other refresher loops registered in main.py
    # (futgg_market_snapshot: 120s, priority_recompute: 900s).
    recompute_interval_seconds: int = field(default_factory=lambda: _env_int("INVESTMENT_ENGINE_INTERVAL_SECONDS", 300))
    cohort_refresh_interval_seconds: int = field(default_factory=lambda: _env_int("INVESTMENT_ENGINE_COHORT_REFRESH_INTERVAL_SECONDS", 21600))

    # ---- Cohort classification (cohorts.py) --------------------------------
    # Data-driven percentile cutoffs, matching the FC26 research method
    # (research/fc26_launch_market Phase 8's build_cohorts) exactly:
    # within-position-group launch-price percentile, quintile-ish bands.
    premium_meta_top_percentile: float = field(default_factory=lambda: _env_float("INVESTMENT_PREMIUM_META_PERCENTILE", 0.90))
    mid_tier_meta_percentile: float = field(default_factory=lambda: _env_float("INVESTMENT_MID_TIER_META_PERCENTILE", 0.70))
    desirable_starter_percentile: float = field(default_factory=lambda: _env_float("INVESTMENT_DESIRABLE_STARTER_PERCENTILE", 0.45))
    ordinary_percentile: float = field(default_factory=lambda: _env_float("INVESTMENT_ORDINARY_PERCENTILE", 0.20))
    min_cohort_size: int = field(default_factory=lambda: _env_int("INVESTMENT_MIN_COHORT_SIZE", 20))

    # ---- Dynamic drawdown / severity (drawdown.py) --------------------------
    # Percentile-of-own-cycle bands. "extreme" is deliberately a high bar
    # (top 5% of everything this cycle has shown so far) so it can't be
    # satisfied by ordinary day-to-day noise early in a cycle when there
    # is very little history to rank against - see
    # min_cycle_observations_for_percentile below.
    severity_percentile_elevated: float = field(default_factory=lambda: _env_float("INVESTMENT_SEVERITY_PCTILE_ELEVATED", 0.60))
    severity_percentile_severe: float = field(default_factory=lambda: _env_float("INVESTMENT_SEVERITY_PCTILE_SEVERE", 0.85))
    severity_percentile_extreme: float = field(default_factory=lambda: _env_float("INVESTMENT_SEVERITY_PCTILE_EXTREME", 0.95))
    # FC26's premium/meta cohort validated plateau (Phase 8C): 40-55%
    # drawdown all converged on the same, zero-false-trigger entry. Used
    # ONLY as a REFERENCE weighted into severity_score alongside the
    # cycle's own percentile (see drawdown.py) - never as a standalone
    # "wait for exactly this number" gate. Explicitly NOT the sole
    # trigger, per the product requirement to avoid a hardcoded magic
    # number: a milder or harsher FC27 launch shifts the cycle-relative
    # percentile automatically even if this reference range stays fixed.
    fc26_reference_drawdown_low_pct: float = field(default_factory=lambda: _env_float("INVESTMENT_FC26_REF_DRAWDOWN_LOW", 40.0))
    fc26_reference_drawdown_high_pct: float = field(default_factory=lambda: _env_float("INVESTMENT_FC26_REF_DRAWDOWN_HIGH", 56.5))
    # Below this many of the cycle's own observations, a percentile rank
    # is too noisy to trust on its own - severity falls back more heavily
    # on the FC26 reference (see drawdown.py's blend weighting, and the
    # "FC26 prior vs FC27 reality" weighting concept in runner.py).
    min_cycle_observations_for_percentile: int = field(default_factory=lambda: _env_int("INVESTMENT_MIN_CYCLE_OBS", 5))
    # Launch-inflation ("OVERHEATED") detector: how far the cohort index
    # sits above its own post-entry low before the engine calls it hype
    # rather than a real floor. Not FC26-derived - purely a "well above
    # its own recent range" heuristic.
    overheat_threshold_pct: float = field(default_factory=lambda: _env_float("INVESTMENT_OVERHEAT_THRESHOLD_PCT", 40.0))

    # ---- Deceleration (momentum.py) -----------------------------------------
    # FC26 found requiring 3 consecutive decelerating periods entered
    # measurably later (and worse) than 1-2; 2 is the default here as the
    # more conservative of the two combinations Phase 8C validated as a
    # zero-false-trigger plateau, but see sensitivity notes in
    # backend/INVESTMENT_ENGINE.md - both 1 and 2 are supported and
    # configurable, not hardcoded into the logic.
    deceleration_required_periods: int = field(default_factory=lambda: _env_int("INVESTMENT_DECEL_REQUIRED_PERIODS", 2))

    # ---- PREMIUM_POST_CRASH_REBOUND strategy --------------------------------
    # FC26-supported: Phase 8B/8C found 2-3 day holds worked, 5+ days
    # deteriorated substantially, 7+ was poor. 72h default gives room for
    # a 3-day hold without inviting the 5+ day deterioration the research
    # explicitly warned against.
    premium_max_hold_hours: float = field(default_factory=lambda: _env_float("INVESTMENT_PREMIUM_MAX_HOLD_HOURS", 72.0))
    premium_target_roi_pct: float = field(default_factory=lambda: _env_float("INVESTMENT_PREMIUM_TARGET_ROI_PCT", 6.0))
    # A regime that's been open this long without closing is treated as
    # stale and force-exited - FC26's own usable window was ~2 days past
    # first trigger before fresh entries turned poor (Phase 8C section 6).
    premium_regime_max_age_hours: float = field(default_factory=lambda: _env_float("INVESTMENT_PREMIUM_REGIME_MAX_AGE_HOURS", 48.0))

    # ---- LAUNCH_STARTER_ACCUMULATION strategy -------------------------------
    # FC26-supported: Phase 8 found this cohort's edge came from pre-
    # Full-Release entry with 3-7 day holds, NOT from timing a bottom -
    # this strategy deliberately never touches the drawdown/deceleration
    # trigger above (see strategies/launch_starter_accumulation.py).
    starter_max_hold_hours: float = field(default_factory=lambda: _env_float("INVESTMENT_STARTER_MAX_HOLD_HOURS", 168.0))
    starter_target_roi_pct: float = field(default_factory=lambda: _env_float("INVESTMENT_STARTER_TARGET_ROI_PCT", 8.0))
    # Days after Web App launch the accumulation window stays open. FC26's
    # own window ran from launch through roughly Full Release - this is a
    # translated, round default, not the literal FC26 date range.
    starter_launch_window_days: float = field(default_factory=lambda: _env_float("INVESTMENT_STARTER_LAUNCH_WINDOW_DAYS", 9.0))

    # ---- New-cycle bootstrap / readiness gates (readiness.py) ----------------
    # "Insufficient data is a valid state." These are the binary
    # are-the-inputs-even-present gates that run BEFORE any market
    # judgement, so a brand-new game cycle produces a NAMED suppression
    # reason rather than a silent zero-signal pass. See readiness.py.
    #
    # Fraction of a cohort's priced cards whose price must be fresh
    # (within max_price_age_minutes) before the cohort is judged at all.
    # Deliberately not 1.0: a handful of stragglers in a 200-card cohort
    # should not block the whole market read, but a cohort where half the
    # prices are hours old is not something to make investment calls on.
    min_fresh_price_fraction: float = field(default_factory=lambda: _env_float("INVESTMENT_MIN_FRESH_PRICE_FRACTION", 0.60))
    # How many of this cohort's OWN market-state observations must exist
    # before its drawdown/momentum history means anything. At 0-1
    # observations every drawdown is 0% by definition (nothing to have
    # fallen from), so acting on it would be acting on an artefact.
    min_market_state_observations: int = field(default_factory=lambda: _env_int("INVESTMENT_MIN_MARKET_STATE_OBS", 6))
    # Only checked for strategies that declare they need sales evidence
    # (evaluate_readiness(require_sales=True)) - see that function's note
    # on why a missing sales pipeline is not the same as zero sales.
    min_cards_with_sales: int = field(default_factory=lambda: _env_int("INVESTMENT_MIN_CARDS_WITH_SALES", 10))

    # ---- Risk controls --------------------------------------------------------
    min_sales_count_recent: int = field(default_factory=lambda: _env_int("INVESTMENT_MIN_SALES_COUNT_RECENT", 3))
    min_sales_for_investable: int = field(default_factory=lambda: _env_int("INVESTMENT_MIN_SALES_FOR_INVESTABLE", 5))
    max_price_age_minutes: int = field(default_factory=lambda: _env_int("INVESTMENT_MAX_PRICE_AGE_MINUTES", 180))
    max_signals_per_cohort_per_run: int = field(default_factory=lambda: _env_int("INVESTMENT_MAX_SIGNALS_PER_COHORT_PER_RUN", 25))

    # ---- FC26 prior weighting (runner.py) --------------------------------------
    # Elapsed-hours-since-launch bands -> weight in [0,1] given to the
    # FC26 prior when blending severity/eligibility. Mirrors
    # priority_config.py's demand_decay_bands_hours pattern: age-banded
    # decay rather than a single hardcoded cutoff. The longer FC27 runs,
    # the more its own live evidence dominates.
    fc26_prior_decay_bands_hours: dict = field(default_factory=lambda: {
        48.0: 1.0,     # first 2 days: FC27 has almost no history of its own
        168.0: 0.6,    # through week 1: blend
        336.0: 0.25,   # through week 2: FC27 evidence increasingly dominant
    })
    # Beyond the last band (336h / 2 weeks): prior treated as largely
    # irrelevant (an "established market", not a launch any more).
    fc26_prior_minimal_weight: float = field(default_factory=lambda: _env_float("INVESTMENT_FC26_PRIOR_MINIMAL_WEIGHT", 0.05))


INVESTMENT_ENGINE_CONFIG = InvestmentEngineConfig()
