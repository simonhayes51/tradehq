# app/services/futgg_config.py
"""
Single source of truth for every tunable the FUT.GG intelligence engine
uses, plus the version stamps that make an outcome-graded comparison
between two configurations possible.

Why this module exists (see futgg_intelligence.py's own header for the
longer version): thresholds and blend weights used to be module-level
constants scattered across futgg_intelligence.py. That made two things
impossible:

  1. Recording *which* configuration produced a given recommendation, so
     that when the outcome grader closes the loop weeks later we can
     attribute the result to something specific rather than "whatever
     the code happened to say at the time".

  2. Running two configurations against each other. Every threshold in
     here is a documented guess (there was no closed-outcome history to
     tune against when the engine was written) - the only way to stop
     guessing is to version the guesses and grade them.

ENGINE_VERSION must be bumped whenever a change would alter the
recommendation produced for an unchanged market snapshot. TREND_VERSION
is versioned separately because the trend layer is deliberately
independently testable/validatable (a trend state can be graded against
what the price actually did next, without reference to whether the
buy-side engine agreed).

Nothing in here does I/O or imports any other app module beyond
trading_math, so it stays safe to import from pure/unit-tested code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional

# =============================================================================
# Version stamps
# =============================================================================
#
# Bump ENGINE_VERSION on any change that alters the recommendation for an
# unchanged snapshot. Format is "futgg-<major>.<minor>"; the outcome
# grader groups by this exact string, so it must be stable and unique per
# behavioural configuration.
ENGINE_VERSION = "futgg-3.1-sales-restored"

# Bumped independently of ENGINE_VERSION - see module docstring.
TREND_VERSION = "trend-1.0"

# Bumped when the outcome grading rules themselves change (e.g. a
# different definition of "entry achieved"). Stored on every outcome row
# so a later grading-rule change never silently makes old and new labels
# look comparable.
GRADER_VERSION = "grader-1.0"


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return Decimal(default)
    try:
        return Decimal(raw.strip())
    except Exception:
        return Decimal(default)


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
class EngineConfig:
    """Every threshold/weight the buy-side engine reads. Frozen so a
    config can be safely shared across requests and hashed into a
    recommendation snapshot without risk of mutation mid-evaluation."""

    version: str = ENGINE_VERSION

    # ---- Sample-sufficiency gates -------------------------------------
    min_sales_for_signal: int = 5

    # ---- Staleness ----------------------------------------------------
    # Per-tier acceptable price age. Roughly a third of each tier's own
    # re-price interval in futgg_price_sync.py, so "acceptable" is always
    # meaningfully tighter than the worst realistic gap between scrapes -
    # EXCEPT special, which must stay above the bulk price sync's own
    # worst-case confirmation gap or it structurally fails regardless of
    # real market conditions. futgg_bulk_price_sync.py's change-only-writes
    # heartbeat (added to cut DB write volume) only guarantees a stable
    # card's bin_checked_at is refreshed within
    # FUTGG_BULK_HEARTBEAT_MINUTES + (card_id % that) - up to 29 minutes at
    # the 15-minute default - regardless of how current the price actually
    # is. This was 20 until special-tier cards started disappearing from
    # recommendations/opportunities near-permanently: whenever a card's
    # own heartbeat interval landed above 20 (roughly two thirds of
    # card_ids do, by the modulo above), it would spend part of every cycle
    # legitimately exceeding this gate despite the feed confirming its
    # price on schedule. See test_futgg_price_freshness_clock.py's
    # test_the_worst_case_heartbeat_lag_clears_every_tier_limit. 40 keeps a
    # real margin above the 29-minute worst case even if
    # FUTGG_BULK_HEARTBEAT_MINUTES is raised somewhat in production
    # without a matching code change.
    max_price_age_minutes_by_tier: Mapping[str, int] = field(
        default_factory=lambda: {
            "special": 40,
            "gold_rare": 45,
            "gold_common": 180,
            "silver": 480,
            "bronze": 720,
        }
    )
    default_max_price_age_minutes: int = 120

    # ---- Dispersion ---------------------------------------------------
    extreme_dispersion_ratio: float = 0.45
    # Interdecile spread ((p90 - p10) / median), migration 050. A second,
    # differently-shaped dispersion test rather than a replacement for the
    # stddev ratio above: stddev/median is dominated by how far the tails
    # reach, so a series that is merely WIDE but has no usable centre can
    # sit well under it. Measured on Willian Pacho UCL RTTF 96: 40 sales in
    # six hours spanning 33,500-94,500, with 33,500 and 87,000 printing in
    # the same hour - stddev/median 0.199 (passes 0.45), interdecile 0.484
    # (fails this).
    #
    # This matters far more than a wide error bar on the fair value. With
    # no real trend in the series to find, futgg_trend.py's verdict became
    # a function of how many rows it was handed: identical card, minute and
    # data gave BUY at n=8, STRONG BUY at n=12, AVOID at n=15, BUY at
    # n=20-30 and AVOID at n=40, with reported confidence climbing 0.63 ->
    # 0.85 throughout. Declining to value the card is the only honest
    # outcome; 0.40 sits above the spread of ordinary liquid cards and
    # below the shape above. Tighten only with graded outcomes in hand.
    extreme_interdecile_ratio: float = 0.40

    # ---- Sales freshness ----------------------------------------------
    # Fair value is 70% sales-derived, so the age of the NEWEST sale is as
    # material as the age of the BIN - and until now only the BIN was
    # checked. A card can therefore show a 2-minute-old price beside a
    # fair value anchored to sales days old, and present the resulting
    # verdict with high confidence. Observed live: a card quoting a
    # 337,000 BIN was marked AVOID against a ~286,000 target derived from
    # sales whose newest print was four days earlier, while the chart
    # beside it showed a 396,000 sale.
    #
    # Beyond this age the sales sample stops describing the current
    # market and confidence decays toward zero.
    max_sales_age_minutes: int = 720          # 12h - full confidence below this
    # Past this, the sample is not evidence about today's market at all.
    reject_sales_older_than_minutes: int = 4320   # 72h

    # ---- Plausibility of the edge -------------------------------------
    # Minimum ratio of live BIN to the recent-sales estimate before the
    # two are accepted as describing the same market. Below this, a
    # discount is not an opportunity - it is evidence that the sales
    # window straddles a crash or a promo reprice, and the card cannot
    # honestly be valued against it.
    #
    # 0.5 is deliberately permissive: genuine snipes sit comfortably
    # inside it. The case this was written for was 0.18 - a live, correct,
    # four-minute-old BIN of 138,000 against a ~755,000 sales estimate,
    # which the engine turned into "buy below 500,000" and a claimed
    # 403,500 profit. Tighten only with graded outcomes in hand.
    min_bin_to_sales_ratio: float = 0.5
    # The mirror image, and until now simply absent: a live BIN far ABOVE
    # the sales estimate is the same evidence of two different periods, and
    # went entirely ungated. It is not the harmless direction it looks:
    # fair value is 30% BIN-weighted, so a BIN detached upward drags the
    # sell target up with it, and a user lists at a price nothing is
    # actually clearing at. Seen live on Pacho, whose lowest BIN sat at the
    # 100,000 price cap (every listing parked there, real sales clearing
    # around 70,600) - a 1.42 ratio that produced a 79,000 sell target off
    # a market that was not paying it.
    #
    # 2.0 is deliberately permissive for the same reason 0.5 is: a card
    # legitimately on the way up prints a high BIN before the sales window
    # catches up, and that is a real signal, not an error.
    max_bin_to_sales_ratio: float = 2.0

    # ---- Confidence gates ---------------------------------------------
    min_confidence_for_buy_signal: float = 0.45
    min_confidence_for_any_signal: float = 0.20

    # ---- ROI gates ----------------------------------------------------
    min_net_roi_for_buy: Decimal = Decimal("0.03")
    min_net_roi_for_strong_buy: Decimal = Decimal("0.08")

    # ---- Fair-value blend ---------------------------------------------
    # Sales evidence dominates by design: BIN is a single current ask,
    # not a cleared price. The BIN weight is a deliberate haircut on the
    # apparent edge - it pulls fair value toward the live ask, so a card
    # listed far below its sales median does not get credited with the
    # full gap. Conservative, and explicitly one of the first things the
    # outcome loop should be used to validate or correct.
    fair_value_sales_weight: float = 0.7
    fair_value_bin_weight: float = 0.3

    # ---- Conservative buy margin --------------------------------------
    # The recommended (as opposed to theoretical-maximum) entry sits this
    # far below the theoretical break-even-clearing ceiling, so a
    # recommendation has room to still work if fair value was slightly
    # optimistic.
    conservative_buy_margin: float = 0.04

    # ---- Sell target --------------------------------------------------
    # recommended_sell_target used to be fair value itself, which made it
    # part-derived from the live BIN - so the price we told a user to LIST
    # AT moved whenever the cheapest ASK moved, even though what buyers
    # were actually paying had not changed at all. On Pacho that swung the
    # target between 66,500 and 79,000 purely on the ask, against sales
    # clearing at ~70,600 the whole time; the reported symptom was "it said
    # sell for 90, it actually sold at 72".
    #
    # The sell target is now derived only from what the card has actually
    # been CLEARING at - sales_trimmed_mean, the mean of sales between p10
    # and p90, which is precisely "average sale price with the silly-low
    # (and silly-high) anomalies excluded" - with this margin taken off so
    # the listing sits just under that average and therefore actually
    # sells. Listing exactly at the average clears only about half the
    # time, which is not a target, it is a coin flip.
    #
    # fair_value is deliberately NOT changed: blending the BIN in is a
    # conservatism on the ENTRY side (it stops a card listed far below its
    # median being credited the full gap), and it still governs
    # theoretical_max_buy / recommended_buy_max. Only the exit price stops
    # depending on the ask.
    sell_target_margin: float = 0.03

    # ---- Trend gating -------------------------------------------------
    # Trend states that block a buy-side signal outright regardless of how
    # large the apparent discount is. This is the falling-knife guard: a
    # card 25% below a 14-day-stale median in an unresolved downtrend is
    # not a bargain, it is a card that is still falling.
    trend_states_blocking_buy: tuple = ("falling_knife", "downtrend")
    # In these states a buy is still possible but is capped at "buy" -
    # never promoted to "strong_buy" - because the discount is not yet
    # confirmed to have stopped widening.
    trend_states_capping_signal: tuple = ("stabilising", "insufficient_trend_data")

    # ---- Unverifiable-discount guard (new-cycle launch safety) --------
    # The gap the falling-knife guard above cannot close on its own.
    #
    # falling_knife/downtrend BLOCK a buy, and insufficient_trend_data
    # CAPS it at "buy" - but a cap is not a block, and in the first hours
    # of a new game cycle EVERY card is insufficient_trend_data (the
    # trend layer needs min_observations sales over min_span_minutes,
    # which simply do not exist yet). During price discovery a card can
    # be repricing downward fast while its sales median - being
    # backward-looking - still reflects the older, higher level. The
    # engine then sees a very large apparent discount, no trend evidence
    # to contradict it, and publishes a "buy" on a card in free fall.
    #
    # This guard says: when we have NO trend evidence AND the apparent
    # discount is larger than an ordinary mispricing plausibly is, we
    # cannot distinguish "genuine bargain" from "fast repricing" - so we
    # do not guess. The card becomes a watch, not a buy.
    #
    # Deliberately general, NOT FC26-specific: no dates, no launch-phase
    # check, no drawdown number borrowed from the FC26 research. It is a
    # statement about evidence ("a discount this large with nothing
    # confirming it is unverifiable"), which is true in any market at any
    # time - it simply happens to bind hardest during a launch, which is
    # when it is most needed.
    unverifiable_discount_ratio: float = 0.25
    unverifiable_discount_guard_enabled: bool = True
    # CRITICAL QUALIFIER - what stops this guard eating the core product.
    #
    # The absence of a TREND reading is not by itself evidence that a
    # discount is unverifiable. A card with 30 completed sales in the last
    # hour, all clearing within 2% of each other, has a thoroughly
    # established price level - a BIN far below that level is a genuine
    # mispricing (exactly the snipe Opportunities exists to surface), even
    # though the trend layer cannot speak because its 90-minute span
    # requirement is not met.
    #
    # What actually makes a discount unverifiable is having no
    # well-established level to be discounted FROM. So the guard only
    # fires when the sales evidence is ALSO too thin or too scattered to
    # have established one. During a new cycle's first hours that is the
    # normal condition (a handful of scattered sales); for an established
    # card being sniped it is not.
    unverifiable_discount_min_sales_for_level: int = 20
    unverifiable_discount_max_dispersion_for_level: float = 0.15

    # A card currently obtainable via a live SBC has extra supply flowing
    # in with every completion - see app/services/sbc_reward_match.py for
    # the full rationale and why the match feeding this is conservative
    # but still unproven against live data. This kill switch exists so
    # the gate can be turned off without a deploy if the match rate turns
    # out to be poor in practice.
    sbc_reward_guard_enabled: bool = True

    # ---- Expiry -------------------------------------------------------
    # Base shelf-life of a recommendation before market conditions are
    # assumed to have moved on, scaled down by volatility/velocity and up
    # by liquidity/confidence in _compute_expiry_minutes (futgg_intelligence.py).
    #
    # Raised 45 -> 90 (2026-09-05): the entry bar (ROI/confidence
    # thresholds) is unchanged and not the problem here - two genuinely
    # qualifying strong_buys, confirmed live (Alexander Isak, Joan
    # Jordan), came back with expiry_minutes of 22 and 17 after the
    # liquidity/dispersion/distance-from-threshold factors shrank the old
    # 45-minute base by ~50-60%. futgg_scanner only re-evaluates every
    # ~10 minutes, so a genuine catch with a ~20-minute shelf life gave a
    # user checking the feed every 20-30 minutes, as most do, a real
    # chance of never seeing it at all - not because nothing qualified,
    # but because it had already expired by the next look. Doubling the
    # base roughly doubles every scaled result (confirmed by
    # test_futgg_intelligence.py's own expiry tests), giving a genuine
    # opportunity a realistic window to actually be seen without loosening
    # what counts as one.
    base_expiry_minutes: int = 90
    min_expiry_minutes: int = 5
    max_expiry_minutes: int = 360
    # A recommendation is invalidated (not merely expired) once the live
    # BIN moves this far from the BIN it was computed against.
    invalidation_bin_drift_pct: float = 0.05

    def max_price_age_for_tier(self, price_tier: Optional[str]) -> int:
        if price_tier is None:
            return self.default_max_price_age_minutes
        return self.max_price_age_minutes_by_tier.get(
            price_tier, self.default_max_price_age_minutes
        )

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe snapshot of the whole configuration, for embedding in
        a recommendation row so the exact numbers behind a call are
        recoverable later even if the code has since moved on."""
        out: Dict[str, Any] = {}
        for key, value in asdict(self).items():
            if isinstance(value, Decimal):
                out[key] = float(value)
            elif isinstance(value, tuple):
                out[key] = list(value)
            elif isinstance(value, dict):
                out[key] = dict(value)
            else:
                out[key] = value
        return out


@dataclass(frozen=True)
class TrendConfig:
    """Thresholds for the trend/falling-knife layer. Versioned separately
    from EngineConfig - see module docstring."""

    version: str = TREND_VERSION

    # Minimum raw sales observations before any trend state other than
    # insufficient_trend_data can be returned. Below this, a "slope" is
    # noise dressed up as a signal.
    min_observations: int = 6
    # Minimum wall-clock span the observations must cover. Six sales in
    # four minutes says nothing about a trend.
    min_span_minutes: float = 90.0

    # Fractional change thresholds. Deliberately expressed against the
    # card's own price level (all the change features are ratios), so a
    # single set of thresholds works across bronze and special tiers.
    downtrend_change_threshold: float = -0.06
    uptrend_change_threshold: float = 0.06
    sideways_band: float = 0.03

    # A falling knife is a *steep, recent, unresolved* decline - all three
    # of: short-term change below this, a majority of recent sales under
    # the earlier median, and no stabilisation evidence.
    falling_knife_short_change: float = -0.10
    falling_knife_below_prior_median_ratio: float = 0.65
    falling_knife_min_drawdown: float = 0.12

    # Stabilisation: the most recent slice has stopped making new lows and
    # its dispersion has tightened relative to the earlier slice.
    stabilising_max_recent_change: float = 0.025
    stabilising_max_new_low_ratio: float = 0.20

    # Recovery: price fell meaningfully below its window high and has
    # since turned back up off the low.
    #
    # Note this is deliberately NOT gated on the window's start-to-end
    # change being negative. A clean V-shaped recovery ends near where it
    # began, so an "overall window still down" test classifies the single
    # most recognisable recovery shape as sideways. What actually
    # separates a recovery from an uptrend is that a recovery is still
    # trading below its earlier peak (drawdown_from_high > 0) whereas an
    # uptrend's latest print IS the peak.
    recovering_min_bounce: float = 0.05
    recovering_min_drawdown: float = 0.05

    # Fraction of the window treated as "recent" for short-term features.
    # 0.4 => last 40% of observations by time.
    recent_fraction: float = 0.4

    def as_dict(self) -> Dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()}


# =============================================================================
# Active configuration
# =============================================================================
#
# Env-var overrides exist so a threshold can be moved in staging without a
# deploy, but the *default* remains the documented value above - an
# unset environment must always produce the versioned, reviewed config.
def _load_engine_config() -> EngineConfig:
    return EngineConfig(
        version=os.getenv("FUTGG_ENGINE_VERSION", ENGINE_VERSION),
        min_sales_for_signal=_env_int("FUTGG_MIN_SALES_FOR_SIGNAL", 5),
        extreme_dispersion_ratio=_env_float("FUTGG_EXTREME_DISPERSION_RATIO", 0.45),
        extreme_interdecile_ratio=_env_float("FUTGG_EXTREME_INTERDECILE_RATIO", 0.40),
        min_bin_to_sales_ratio=_env_float("FUTGG_MIN_BIN_TO_SALES_RATIO", 0.5),
        max_bin_to_sales_ratio=_env_float("FUTGG_MAX_BIN_TO_SALES_RATIO", 2.0),
        min_confidence_for_buy_signal=_env_float("FUTGG_MIN_CONFIDENCE_FOR_BUY", 0.45),
        min_confidence_for_any_signal=_env_float("FUTGG_MIN_CONFIDENCE_ANY", 0.20),
        min_net_roi_for_buy=_env_decimal("FUTGG_MIN_NET_ROI_FOR_BUY", "0.03"),
        min_net_roi_for_strong_buy=_env_decimal("FUTGG_MIN_NET_ROI_STRONG_BUY", "0.08"),
        fair_value_sales_weight=_env_float("FUTGG_FAIR_VALUE_SALES_WEIGHT", 0.7),
        fair_value_bin_weight=_env_float("FUTGG_FAIR_VALUE_BIN_WEIGHT", 0.3),
        conservative_buy_margin=_env_float("FUTGG_CONSERVATIVE_BUY_MARGIN", 0.04),
        sell_target_margin=_env_float("FUTGG_SELL_TARGET_MARGIN", 0.03),
        base_expiry_minutes=_env_int("FUTGG_BASE_EXPIRY_MINUTES", 90),
        invalidation_bin_drift_pct=_env_float("FUTGG_INVALIDATION_DRIFT_PCT", 0.05),
        unverifiable_discount_ratio=_env_float("FUTGG_UNVERIFIABLE_DISCOUNT_RATIO", 0.25),
        unverifiable_discount_guard_enabled=_env_bool("FUTGG_UNVERIFIABLE_DISCOUNT_GUARD", True),
        unverifiable_discount_min_sales_for_level=_env_int("FUTGG_UNVERIFIABLE_MIN_SALES_FOR_LEVEL", 20),
        unverifiable_discount_max_dispersion_for_level=_env_float("FUTGG_UNVERIFIABLE_MAX_DISPERSION_FOR_LEVEL", 0.15),
        sbc_reward_guard_enabled=_env_bool("FUTGG_SBC_REWARD_GUARD", True),
    )


ENGINE_CONFIG = _load_engine_config()
TREND_CONFIG = TrendConfig()
