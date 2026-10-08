# app/services/futgg_bin_valuation.py
"""
BIN-history fallback valuation - a separate, explicitly lower-confidence
path used ONLY when completed-sales evidence is unavailable or stale.

WHAT THIS IS NOT
-----------------
Not a second sales feed, and not a way to sneak BIN prices into
completed_sales-shaped data. A BIN is an asking price, never a
confirmation that anything sold - see bin_market_signal.py's own module
docstring for the same point made about a different consumer of the same
raw table. Every result this module produces is stamped
evidence_quality="estimated" and valuation_source="bin_history", and
those two fields must survive unchanged into the API response and the UI
so a user is never shown a BIN-derived number as if it were a confirmed
sale.

WHY THIS IS A SEPARATE MODULE, NOT A BRANCH INSIDE bin_market_signal.py
------------------------------------------------------------------------
bin_market_signal.py already does robust (median/MAD) BIN analysis, but
for a different question: "is this card's asking price interesting
enough to spend an expensive sales request on" (sales_validation_gate.py).
That module's thresholds are tuned for a cheap, frequent, low-stakes
decision. This module answers a different, higher-stakes question - "is
this evidence good enough to tell a user to trade on" - and needs its own,
independently tunable, versioned thresholds. Reusing bin_market_signal's
own dataclasses would couple two things that must be free to diverge; the
robust-statistics PHILOSOPHY (median centre, MAD dispersion, never a raw
mean) is shared and re-implemented here at the same three-line size
rather than importing that module's underscore-prefixed internals.

WHY HOURLY BUCKETS, NOT RAW ROWS
----------------------------------
futgg_bulk_price_sync.py writes futgg_bin_history on change, plus a
periodic per-card heartbeat (see futgg_config.py's own comment on
FUTGG_BULK_HEARTBEAT_MINUTES) even when the price hasn't moved. Counting
raw rows as "observations" would let a card whose price has been flat for
a week look like it has dozens of independent confirmations, when it has
one price sampled repeatedly. Bucketing collapses each hour to one median
first - a flat card contributes one number per hour it was actually
checked, not one per heartbeat - which is what "distinct observations
spanning a meaningful period" has to mean for this to be honest evidence.

GATING (all required - see futgg_bin_valuation requirements)
--------------------------------------------------------------------
  1. Fresh current BIN - reuses the exact same per-tier freshness gate
     futgg_intelligence.py already applies to the sales path
     (EngineConfig.max_price_age_for_tier); no separate threshold.
  2. >= min_bucket_count distinct hourly buckets with a valid observation.
  3. >= min_span_hours between the earliest and latest qualifying bucket.
  4. Outlier/zero filtering: non-positive prices dropped before bucketing;
     bucket medians more than outlier_mad_multiplier MADs from the
     all-bucket median are dropped before the reference price/trend are
     computed from what remains.
  5. Stability-or-trend: either the (outlier-filtered) bucket medians are
     stable (bin_volatility <= max_bin_volatility_for_valuation) or they
     show a clearly measurable trend (>= min_trend_magnitude fractional
     change between the window's early and late thirds) - noise in
     between (unstable but not clearly trending) is rejected.
  6. No trustworthy completed-sales evidence - decided by the CALLER
     (futgg_intelligence.evaluate_card), never here: this module has no
     opinion on when it should be invoked, only on whether the BIN
     evidence it is handed is good enough once invoked.

Every rejection is a specific, stable reason code (futgg_reasons.py) so
futgg_scanner.py's existing blocking_reason_counts metric - already
counting sales-path rejections the same way - picks these up with no
scanner changes beyond the one new valuation-path counter this task also
asked for.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from app.services import futgg_reasons as rc
from app.services import trading_math as tm
from app.services.futgg_reasons import ReasonList

BIN_FALLBACK_VERSION = "bin-fallback-1.0"


@dataclass(frozen=True)
class BinFallbackConfig:
    """Every threshold the BIN-history fallback reads. Versioned and
    frozen for the same reasons EngineConfig/TrendConfig/BinMarketConfig
    are - see futgg_config.py's module docstring."""

    version: str = BIN_FALLBACK_VERSION

    # ---- Time bucketing -------------------------------------------------
    bucket_width_minutes: int = 60
    min_bucket_count: int = 6
    min_span_hours: float = 6.0

    # ---- Outlier filtering ------------------------------------------------
    outlier_mad_multiplier: float = 3.5

    # ---- Stability-or-trend ----------------------------------------------
    max_bin_volatility_for_valuation: float = 0.12
    min_trend_magnitude: float = 0.08
    trend_flat_band: float = 0.04

    # ---- Valuation ----------------------------------------------------
    # Wider than the sales path's 3% (sell_target_margin in
    # EngineConfig): a BIN-derived reference is a listing level, never a
    # cleared price, so the margin taken off it has to be larger.
    bin_sell_target_margin: float = 0.06

    # ---- Stricter recommendation rules (explicit requirement) ----------
    min_net_roi_for_buy: Decimal = Decimal("0.08")
    min_absolute_profit_after_tax: Decimal = Decimal("500")
    conservative_buy_margin: float = 0.04

    # A BIN-derived call can never equal or exceed a sales-confirmed call's
    # confidence, structurally, regardless of how clean its own inputs
    # are - this cap enforces that independent of the geometric blend
    # below ever producing a high number by coincidence.
    confidence_cap: float = 0.55


BIN_FALLBACK_CONFIG = BinFallbackConfig()


# =============================================================================
# Robust primitives (three-line reimplementation of bin_market_signal.py's
# philosophy - see module docstring for why this isn't a cross-import).
# =============================================================================
def _median(values: Sequence[float]) -> Optional[float]:
    return float(statistics.median(values)) if values else None


def _mad(values: Sequence[float], centre: Optional[float] = None) -> Optional[float]:
    if not values:
        return None
    mid = centre if centre is not None else statistics.median(values)
    return float(statistics.median([abs(v - mid) for v in values]))


def _dispersion(values: Sequence[float]) -> Optional[float]:
    """MAD/median - a robust coefficient of variation. None when there is
    no meaningful level (fewer than 2 points, or a zero centre) to divide
    by."""
    if len(values) < 2:
        return None
    mid = statistics.median(values)
    if mid <= 0:
        return None
    mad = _mad(values, mid)
    return None if mad is None else round(mad / mid, 4)


# =============================================================================
# Bucketing
# =============================================================================
def _bucket_key(at: datetime, bucket_width_minutes: int) -> int:
    """Epoch-minute floor-divided into fixed-width buckets. Purely a
    grouping key - the wall-clock timestamp used for span/trend maths is
    each bucket's OWN observation timestamps, not this key."""
    epoch_minutes = int(at.timestamp() // 60)
    return epoch_minutes // max(1, bucket_width_minutes)


@dataclass
class _Bucket:
    key: int
    prices: List[float] = field(default_factory=list)
    earliest_at: Optional[datetime] = None
    latest_at: Optional[datetime] = None

    @property
    def median_price(self) -> Optional[float]:
        return _median(self.prices)


def _build_buckets(
    observations: Sequence[Tuple[int, datetime]], *, bucket_width_minutes: int,
) -> Dict[int, _Bucket]:
    buckets: Dict[int, _Bucket] = {}
    for price, at in observations:
        if price is None or at is None or int(price) <= 0:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        key = _bucket_key(at, bucket_width_minutes)
        bucket = buckets.setdefault(key, _Bucket(key=key))
        bucket.prices.append(float(price))
        if bucket.earliest_at is None or at < bucket.earliest_at:
            bucket.earliest_at = at
        if bucket.latest_at is None or at > bucket.latest_at:
            bucket.latest_at = at
    return buckets


# =============================================================================
# Result
# =============================================================================
@dataclass
class BinValuationResult:
    card_id: int
    qualifies: bool = False

    # ---- Evidence fields (exact names the API/UI expose) ---------------
    bin_sample_count: Optional[int] = None
    bin_history_span_hours: Optional[float] = None
    bin_reference_price: Optional[Decimal] = None
    bin_trend_direction: Optional[str] = None   # "up" | "down" | "flat"
    bin_volatility: Optional[float] = None
    evidence_age_minutes: Optional[int] = None

    # ---- Valuation (populated only when qualifies=True) -----------------
    recommended_sell_target: Optional[Decimal] = None
    theoretical_max_buy: Optional[Decimal] = None
    recommended_buy_max: Optional[Decimal] = None

    reasons: ReasonList = field(default_factory=ReasonList)

    config_version: str = BIN_FALLBACK_CONFIG.version


def evaluate_bin_fallback(
    card_id: int,
    observations: Sequence[Tuple[int, datetime]],
    *,
    current_bin: Optional[int],
    price_age_minutes: Optional[int],
    max_acceptable_price_age_minutes: int,
    as_of: Optional[datetime] = None,
    config: BinFallbackConfig = BIN_FALLBACK_CONFIG,
) -> BinValuationResult:
    """Pure: no I/O. `observations` is [(lowest_bin, captured_at), ...],
    typically bin_market_signal.load_bin_observations()'s own per-card
    output re-used as-is (same 7-day/2500-row-capped loader; this module
    performs no extra query of its own).

    Never raises - every gate failure returns qualifies=False with a
    specific blocking reason in `.reasons`, mirroring evaluate_card()'s
    own contract."""
    as_of = as_of or datetime.now(timezone.utc)
    result = BinValuationResult(
        card_id=card_id, evidence_age_minutes=price_age_minutes,
        config_version=config.version,
    )
    reasons = result.reasons

    # ---- Gate 1: fresh current BIN --------------------------------------
    if current_bin is None or price_age_minutes is None:
        reasons.add(rc.BIN_NO_LIVE_PRICE, "No live BIN listing to anchor a BIN-derived valuation against.")
        return result
    if price_age_minutes > max_acceptable_price_age_minutes:
        reasons.add(
            rc.BIN_STALE_CURRENT_PRICE,
            f"Current price observation is {price_age_minutes} minutes old - beyond the "
            f"{max_acceptable_price_age_minutes}-minute freshness limit, so even a BIN-derived "
            "estimate cannot be anchored to it.",
        )
        return result

    # ---- Bucketing --------------------------------------------------------
    buckets = _build_buckets(observations, bucket_width_minutes=config.bucket_width_minutes)
    if len(buckets) < config.min_bucket_count:
        result.bin_sample_count = len(buckets)
        reasons.add(
            rc.BIN_INSUFFICIENT_BUCKETS,
            f"Only {len(buckets)} distinct hourly BIN observation(s) in the window - need at "
            f"least {config.min_bucket_count} to treat this as independent evidence.",
        )
        return result

    ordered = sorted(buckets.values(), key=lambda b: b.key)
    earliest_at = min(b.earliest_at for b in ordered if b.earliest_at is not None)
    latest_at = max(b.latest_at for b in ordered if b.latest_at is not None)
    span_hours = (latest_at - earliest_at).total_seconds() / 3600.0
    result.bin_sample_count = len(ordered)
    result.bin_history_span_hours = round(span_hours, 2)

    if span_hours < config.min_span_hours:
        reasons.add(
            rc.BIN_INSUFFICIENT_SPAN,
            f"BIN observations span only {span_hours:.1f} hours - need at least "
            f"{config.min_span_hours:.0f} hours before treating this as a settled history, "
            "not a few samples from the same short window.",
        )
        return result

    # ---- Outlier filtering (bucket medians, then a global MAD pass) ------
    bucket_medians = [b.median_price for b in ordered if b.median_price is not None]
    overall_median = _median(bucket_medians)
    overall_mad = _mad(bucket_medians, overall_median) if overall_median is not None else None
    if overall_median is not None and overall_mad is not None and overall_mad > 0:
        threshold = overall_mad * config.outlier_mad_multiplier
        filtered = [v for v in bucket_medians if abs(v - overall_median) <= threshold]
    else:
        filtered = list(bucket_medians)
    # A filter that would leave nothing (e.g. every bucket sat exactly on
    # the same absurd spike) must never crash the evaluation - keep the
    # unfiltered set rather than dividing by an empty sequence downstream.
    if not filtered:
        filtered = list(bucket_medians)

    reference_price = _median(filtered)
    volatility = _dispersion(filtered)
    result.bin_reference_price = Decimal(str(reference_price)) if reference_price is not None else None
    result.bin_volatility = volatility

    if reference_price is None or reference_price <= 0:
        reasons.add(rc.BIN_NO_LIVE_PRICE, "No usable BIN reference price after outlier filtering.")
        return result

    # ---- Trend ------------------------------------------------------------
    third = max(1, len(filtered) // 3)
    early = filtered[:third]
    late = filtered[-third:]
    early_level = _median(early)
    late_level = _median(late)
    trend_direction = "flat"
    trend_magnitude = 0.0
    if early_level and early_level > 0 and late_level is not None:
        trend_magnitude = (late_level - early_level) / early_level
        if trend_magnitude >= config.trend_flat_band:
            trend_direction = "up"
        elif trend_magnitude <= -config.trend_flat_band:
            trend_direction = "down"
    result.bin_trend_direction = trend_direction

    # A real, established decline (not sales-path trend data - this
    # module never sees completed sales - but the same failure mode: the
    # reference price above is a median over the WHOLE window, so on a
    # genuine downtrend it is anchored on the stale, higher earlier level,
    # not where the price actually is now). Publishing that as an
    # "opportunity" is precisely the falling-knife bug the sales engine's
    # own trend gate exists to prevent (see futgg_config.py's
    # trend_states_blocking_buy) - block outright rather than reproduce it
    # on the BIN-only path.
    if trend_direction == "down":
        reasons.add(
            rc.BIN_EXCESSIVE_VOLATILITY,
            f"Asking price has fallen {abs(trend_magnitude) * 100:.0f}% across this window - "
            "a BIN-derived reference anchored on the earlier, higher level would overstate "
            "value on a card that is still declining, so this is not treated as a buy signal.",
        )
        return result

    is_stable = volatility is not None and volatility <= config.max_bin_volatility_for_valuation
    is_clear_trend = trend_direction != "flat" and abs(trend_magnitude) >= config.min_trend_magnitude
    if not is_stable and not is_clear_trend:
        reasons.add(
            rc.BIN_EXCESSIVE_VOLATILITY,
            f"Asking-price history is too volatile to anchor a value on (volatility "
            f"{volatility if volatility is not None else float('nan'):.2f} against a "
            f"{config.max_bin_volatility_for_valuation:.2f} ceiling) and has no clearly measurable "
            "trend either.",
        )
        return result

    # =====================================================================
    # Valuation (only reached once every gate above has passed)
    # =====================================================================
    sell_anchor = Decimal(str(reference_price)) * (
        Decimal("1") - Decimal(str(config.bin_sell_target_margin))
    )
    recommended_sell_target = tm.round_to_ea_increment(sell_anchor, direction="down")
    theoretical_max_buy = tm.round_to_ea_increment(
        recommended_sell_target * (Decimal("1") - tm.EA_TAX) / (Decimal("1") + config.min_net_roi_for_buy),
        direction="down",
    )
    recommended_buy_max = tm.round_to_ea_increment(
        theoretical_max_buy * (Decimal("1") - Decimal(str(config.conservative_buy_margin))),
        direction="down",
    )

    current_bin_dec = Decimal(str(current_bin))
    expected_roi = tm.net_roi(recommended_sell_target, current_bin_dec)
    expected_profit = tm.net_profit(recommended_sell_target, current_bin_dec)

    if expected_roi is None or expected_roi < config.min_net_roi_for_buy:
        reasons.add(
            rc.BIN_INSUFFICIENT_ROI,
            f"Expected net ROI against the BIN-derived target "
            f"({float(expected_roi) * 100 if expected_roi is not None else 0:.1f}%) does not clear "
            f"the {float(config.min_net_roi_for_buy) * 100:.0f}% floor required for a BIN-only estimate "
            "(higher than the sales-confirmed floor, deliberately).",
        )
        return result
    if expected_profit < config.min_absolute_profit_after_tax:
        reasons.add(
            rc.BIN_INSUFFICIENT_PROFIT,
            f"Expected profit ({int(expected_profit):,} coins) is below the "
            f"{int(config.min_absolute_profit_after_tax):,}-coin minimum required for a BIN-only "
            "estimate - a real percentage margin on a cheap card is not enough on its own.",
        )
        return result

    result.qualifies = True
    result.recommended_sell_target = recommended_sell_target
    result.theoretical_max_buy = theoretical_max_buy
    result.recommended_buy_max = recommended_buy_max
    reasons.add(
        rc.INFO_BIN_FALLBACK_USED,
        f"No trustworthy completed-sales evidence; valuing off {result.bin_sample_count} hourly "
        f"BIN observations spanning {result.bin_history_span_hours:.0f}h instead.",
    )
    return result
