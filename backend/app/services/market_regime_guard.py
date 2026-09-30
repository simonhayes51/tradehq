from __future__ import annotations

"""Fail-closed market regime guard for card recommendations.

The core intelligence engine is a mispricing detector. That is normally useful,
but a sudden supply shock can make the recent sales distribution obsolete faster
than its median can move. In that state a collapsing card can look *more*
attractive because the live BIN is far below the historical median.

This module adds a small post-evaluation guard. It does not invent a new fair
value and it never upgrades a signal. It can only downgrade an otherwise
executable BUY/STRONG_BUY to AVOID when the supplied sales series says the card
is repricing violently.

It is deliberately pure and data-source agnostic: evaluate_regime() consumes the
same snapshot + recent completed sales already passed to futgg_intelligence.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Mapping, Optional, Sequence

from app.services.bin_market_signal import (
    MOVING_DOWN,
    compute_bin_market_signal,
)

log = logging.getLogger("market_regime_guard")

REGIME_NORMAL = "normal"
REGIME_REPRICING = "repricing"
REGIME_STABILISED = "stabilised"
REASON_CODE = "market_repricing"

# Defaults are intentionally conservative. A single cheap BIN is not enough.
# We need either a clear oldest-vs-newest completed-sales collapse, or a dense
# burst of sales plus a meaningful live-BIN displacement from the sample median.
SERIES_DROP_PCT = float(os.getenv("EAFC_REGIME_SERIES_DROP_PCT", "0.18"))
SERIES_MAX_SPAN_MINUTES = float(os.getenv("EAFC_REGIME_SERIES_MAX_SPAN_MINUTES", "180"))
SERIES_MIN_SALES = int(os.getenv("EAFC_REGIME_SERIES_MIN_SALES", "12"))
VELOCITY_MIN_SALES = int(os.getenv("EAFC_REGIME_VELOCITY_MIN_SALES", "20"))
VELOCITY_MAX_SPAN_MINUTES = float(os.getenv("EAFC_REGIME_VELOCITY_MAX_SPAN_MINUTES", "60"))
MEDIAN_DISCOUNT_PCT = float(os.getenv("EAFC_REGIME_MEDIAN_DISCOUNT_PCT", "0.08"))
MIN_DISPERSION_RATIO = float(os.getenv("EAFC_REGIME_MIN_DISPERSION_RATIO", "0.06"))
CONFIDENCE_CAP = float(os.getenv("EAFC_REGIME_CONFIDENCE_CAP", "0.35"))
# BIN history is the fastest catalogue-wide evidence we own. A persistent
# lowest-BIN level materially below the completed-sales median means the sales
# anchor has not caught up with the live market; treating that gap as upside is
# the exact stale-anchor failure this guard exists to prevent.
BIN_SALES_DISLOCATION_PCT = float(os.getenv("EAFC_REGIME_BIN_SALES_DISLOCATION_PCT", "0.15"))
BIN_CONFIRMATION_BAND_PCT = float(os.getenv("EAFC_REGIME_BIN_CONFIRMATION_BAND_PCT", "0.05"))
BIN_MIN_PERSISTENCE = float(os.getenv("EAFC_REGIME_BIN_MIN_PERSISTENCE", "0.60"))
BIN_MIN_RECENT_OBSERVATIONS = int(os.getenv("EAFC_REGIME_BIN_MIN_RECENT_OBSERVATIONS", "3"))


@dataclass(frozen=True)
class RegimeAssessment:
    state: str = REGIME_NORMAL
    reason: str = ""
    drop_pct: Optional[float] = None
    sales_span_minutes: Optional[float] = None
    sales_count: int = 0
    current_vs_median_pct: Optional[float] = None
    bin_market_state: Optional[str] = None
    bin_change_1h_pct: Optional[float] = None
    bin_change_24h_pct: Optional[float] = None

    @property
    def blocks_buy(self) -> bool:
        return self.state == REGIME_REPRICING


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out <= 0:
        return None
    return out


def _as_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sale_point(row: Any) -> Optional[tuple[datetime, float]]:
    if isinstance(row, Mapping):
        price = (
            row.get("sold_price")
            or row.get("sale_price")
            or row.get("price")
            or row.get("soldPrice")
        )
        sold_at = (
            row.get("approximate_sold_at")
            or row.get("sold_at")
            or row.get("timestamp")
            or row.get("captured_at")
        )
    else:
        # asyncpg.Record supports mapping access, so this branch is mostly a
        # defensive fallback for tests/legacy tuples. Do not guess tuple order.
        return None
    p = _as_float(price)
    t = _as_dt(sold_at)
    if p is None or t is None:
        return None
    return t, p


def _series_assessment(sales: Optional[Sequence[Any]]) -> tuple[Optional[float], Optional[float], int]:
    points = [p for row in (sales or ()) if (p := _sale_point(row)) is not None]
    if len(points) < SERIES_MIN_SALES:
        return None, None, len(points)
    points.sort(key=lambda item: item[0])
    span = (points[-1][0] - points[0][0]).total_seconds() / 60.0
    if span <= 0 or span > SERIES_MAX_SPAN_MINUTES:
        return None, span, len(points)

    # Compare robust chunks, not one print at each end. This resists one odd
    # sale and catches the exact failure mode seen in a supply-shock waterfall.
    chunk = max(3, len(points) // 4)
    older = median(p for _, p in points[:chunk])
    newer = median(p for _, p in points[-chunk:])
    if older <= 0:
        return None, span, len(points)
    return max(0.0, (older - newer) / older), span, len(points)


def _evaluate_bin_regime(
    snapshot: Mapping[str, Any],
    bin_observations: Optional[Sequence[Any]],
    *,
    as_of: Optional[datetime] = None,
) -> Optional[RegimeAssessment]:
    """Use persistent lowest-BIN history to detect a live level that sales lag.

    BINs are asking prices, never completed-sale proof, so a lone undercut must
    not kill a genuine snipe. We only block when the cheap level is established
    by repeated recent observations, or the BIN signal itself says MOVING_DOWN.
    """
    if not bin_observations:
        return None
    try:
        card_id = int(snapshot.get("source_card_id") or 0)
        signal = compute_bin_market_signal(
            card_id,
            bin_observations,
            as_of=as_of or datetime.now(timezone.utc),
        )
    except Exception:
        raise

    current_bin = _as_float(snapshot.get("current_bin")) or _as_float(signal.current_bin)
    sales_median = _as_float(snapshot.get("sales_median"))
    if current_bin is None:
        return None

    if signal.market_state == MOVING_DOWN:
        change = signal.change_24h_pct if signal.change_24h_pct is not None else signal.change_1h_pct
        change_text = f"{abs(change) * 100:.1f}%" if change is not None else "materially"
        return RegimeAssessment(
            state=REGIME_REPRICING,
            reason=(
                f"Live BIN history has established a lower market level ({change_text} below its recent "
                "baseline). Completed-sale fair value is being suppressed until the decline stabilises."
            ),
            current_vs_median_pct=((current_bin - sales_median) / sales_median) if sales_median else None,
            bin_market_state=signal.market_state,
            bin_change_1h_pct=signal.change_1h_pct,
            bin_change_24h_pct=signal.change_24h_pct,
        )

    # A stable/persistent cheap level can be just as dangerous as an actively
    # falling one: once the market has sat at 42k for an hour, a 70k completed-
    # sales median is stale evidence, not 28k of upside. Require both recent
    # persistence and a short-window median close to the current ask so a lone
    # undercut remains eligible for the core snipe product.
    if sales_median and sales_median > 0:
        dislocation = (sales_median - current_bin) / sales_median
        recent_level = signal.median_1h or signal.median_30m
        recent_confirmed = (
            recent_level is not None
            and recent_level > 0
            and abs(current_bin - recent_level) / recent_level <= BIN_CONFIRMATION_BAND_PCT
        )
        persistent = (
            signal.observation_count_1h >= BIN_MIN_RECENT_OBSERVATIONS
            and signal.price_persistence is not None
            and signal.price_persistence >= BIN_MIN_PERSISTENCE
        )
        if dislocation >= BIN_SALES_DISLOCATION_PCT and recent_confirmed and persistent:
            return RegimeAssessment(
                state=REGIME_REPRICING,
                reason=(
                    f"Completed sales are lagging the live market: BIN has held near {current_bin:,.0f} "
                    f"while the recent-sales median is {sales_median:,.0f} "
                    f"({dislocation * 100:.1f}% higher). The old sales level is not being treated as upside."
                ),
                current_vs_median_pct=(current_bin - sales_median) / sales_median,
                bin_market_state=signal.market_state,
                bin_change_1h_pct=signal.change_1h_pct,
                bin_change_24h_pct=signal.change_24h_pct,
            )
    return None


def evaluate_regime(snapshot: Mapping[str, Any], sales: Optional[Sequence[Any]] = None) -> RegimeAssessment:
    current_bin = _as_float(snapshot.get("current_bin"))
    sales_median = _as_float(snapshot.get("sales_median"))
    count = int(snapshot.get("sales_count") or 0)
    span = _as_float(snapshot.get("sales_window_span_minutes"))
    dispersion = _as_float(snapshot.get("sales_dispersion_ratio"))

    series_drop, series_span, parsed_count = _series_assessment(sales)
    effective_count = max(count, parsed_count)
    effective_span = series_span if series_span is not None else span

    current_vs_median = None
    if current_bin is not None and sales_median is not None and sales_median > 0:
        current_vs_median = (current_bin - sales_median) / sales_median

    # Strongest signal: completed sales themselves have repriced sharply from
    # the oldest robust chunk to the newest robust chunk inside a short window.
    if series_drop is not None and series_drop >= SERIES_DROP_PCT:
        return RegimeAssessment(
            state=REGIME_REPRICING,
            reason=(
                f"Completed sales are repricing rapidly: the newest sales cluster is "
                f"{series_drop * 100:.1f}% below the older cluster over about "
                f"{series_span:.0f} minutes. Historical fair value is temporarily unreliable."
            ),
            drop_pct=series_drop,
            sales_span_minutes=series_span,
            sales_count=effective_count,
            current_vs_median_pct=current_vs_median,
        )

    # Aggregated fallback for paths where the raw sales list is unavailable.
    # Dense selling + a live ask materially below the recent median + non-trivial
    # dispersion is treated as repricing, not as a bargain.
    if (
        effective_count >= VELOCITY_MIN_SALES
        and effective_span is not None
        and effective_span <= VELOCITY_MAX_SPAN_MINUTES
        and current_vs_median is not None
        and current_vs_median <= -MEDIAN_DISCOUNT_PCT
        and dispersion is not None
        and dispersion >= MIN_DISPERSION_RATIO
    ):
        return RegimeAssessment(
            state=REGIME_REPRICING,
            reason=(
                f"Market repricing detected: {effective_count} recent sales occurred within about "
                f"{effective_span:.0f} minutes while the live BIN is "
                f"{abs(current_vs_median) * 100:.1f}% below the recent median. "
                "High selling velocity is being treated as liquidation risk, not extra confidence."
            ),
            drop_pct=None,
            sales_span_minutes=effective_span,
            sales_count=effective_count,
            current_vs_median_pct=current_vs_median,
        )

    return RegimeAssessment(
        state=REGIME_NORMAL,
        sales_span_minutes=effective_span,
        sales_count=effective_count,
        current_vs_median_pct=current_vs_median,
    )


def apply_regime_guard(
    ci: Any,
    snapshot: Mapping[str, Any],
    sales: Optional[Sequence[Any]] = None,
    bin_observations: Optional[Sequence[Any]] = None,
    *,
    as_of: Optional[datetime] = None,
):
    """Downgrade only. Never turns a non-buy into a buy and never fabricates value."""
    if getattr(ci, "signal", None) not in {"buy", "strong_buy"}:
        return ci

    # Fastest evidence first: BIN history updates catalogue-wide and catches a
    # new persistent market level before the expensive per-card sales rotation
    # necessarily does. Completed sales remain the valuation anchor, but stale
    # completed sales are not allowed to manufacture an executable BUY.
    assessment = _evaluate_bin_regime(snapshot, bin_observations, as_of=as_of)
    if assessment is None:
        assessment = evaluate_regime(snapshot, sales)
    if not assessment.blocks_buy:
        return ci

    ci.signal = "avoid"
    ci.status = "insufficient_data"
    ci.risk_level = "avoid"
    if getattr(ci, "confidence_score", None) is not None:
        ci.confidence_score = min(float(ci.confidence_score), CONFIDENCE_CAP)

    # Old valuation outputs are precisely what became unsafe. Remove every
    # executable/actionable price from the guarded result so the UI cannot show
    # "AVOID" beside an apparently actionable BUY BELOW or profit figure.
    ci.fair_value = None
    ci.theoretical_max_buy = None
    ci.recommended_buy_max = None
    ci.current_executable_buy = None
    ci.break_even_price = None
    ci.recommended_sell_target = None
    ci.buy_below = None
    ci.expected_profit_after_tax = None
    ci.expected_roi = None
    ci.expected_profit_basis = None

    reason = assessment.reason
    if reason and reason not in ci.signal_reasons:
        ci.signal_reasons.insert(0, reason)
    if REASON_CODE not in ci.reason_codes:
        ci.reason_codes.insert(0, REASON_CODE)
    if REASON_CODE not in ci.blocking_codes:
        ci.blocking_codes.insert(0, REASON_CODE)
    if not any(r.get("code") == REASON_CODE for r in ci.reasons if isinstance(r, dict)):
        ci.reasons.insert(0, {"code": REASON_CODE, "message": reason, "severity": "blocking"})

    # Reuse the existing free-text trend explanation without introducing a new
    # persisted enum/state that older consumers may reject.
    ci.trend_description = reason
    ci.trend_features = dict(getattr(ci, "trend_features", {}) or {})
    ci.trend_features.update({
        "market_regime": REGIME_REPRICING,
        "regime_drop_pct": assessment.drop_pct,
        "regime_sales_span_minutes": assessment.sales_span_minutes,
        "regime_sales_count": assessment.sales_count,
        "regime_current_vs_median_pct": assessment.current_vs_median_pct,
        "regime_bin_market_state": assessment.bin_market_state,
        "regime_bin_change_1h_pct": assessment.bin_change_1h_pct,
        "regime_bin_change_24h_pct": assessment.bin_change_24h_pct,
    })
    return ci


def fail_closed_regime_guard(ci: Any, snapshot: Mapping[str, Any], exc: Exception):
    """If the safety layer itself breaks, preserve the card but remove any buy."""
    if getattr(ci, "signal", None) not in {"buy", "strong_buy"}:
        return ci

    ci.signal = "avoid"
    ci.status = "insufficient_data"
    ci.risk_level = "avoid"
    if getattr(ci, "confidence_score", None) is not None:
        ci.confidence_score = min(float(ci.confidence_score), CONFIDENCE_CAP)

    for attr in (
        "fair_value",
        "theoretical_max_buy",
        "recommended_buy_max",
        "current_executable_buy",
        "break_even_price",
        "recommended_sell_target",
        "buy_below",
        "expected_profit_after_tax",
        "expected_roi",
        "expected_profit_basis",
    ):
        if hasattr(ci, attr):
            setattr(ci, attr, None)

    reason = "Market regime guard failed, so this card is being suppressed until it can be re-evaluated."
    if reason not in ci.signal_reasons:
        ci.signal_reasons.insert(0, reason)
    if REASON_CODE not in ci.reason_codes:
        ci.reason_codes.insert(0, REASON_CODE)
    if REASON_CODE not in ci.blocking_codes:
        ci.blocking_codes.insert(0, REASON_CODE)
    if not any(r.get("code") == REASON_CODE for r in ci.reasons if isinstance(r, dict)):
        ci.reasons.insert(0, {"code": REASON_CODE, "message": reason, "severity": "blocking"})
    ci.trend_description = reason
    ci.trend_features = dict(getattr(ci, "trend_features", {}) or {})
    ci.trend_features.update({
        "market_regime": REGIME_REPRICING,
        "regime_guard_error": exc.__class__.__name__,
    })
    return ci


def install_market_regime_guard() -> None:
    """Wrap the canonical pure evaluator before application modules import it."""
    from app.services import futgg_intelligence

    if getattr(futgg_intelligence, "_market_regime_guard_installed", False):
        return

    original = futgg_intelligence.evaluate_card

    def guarded_evaluate_card(snapshot, *, as_of=None, sales=None, bin_observations=None, **kwargs):
        ci = original(
            snapshot,
            as_of=as_of,
            sales=sales,
            bin_observations=bin_observations,
            **kwargs,
        )
        try:
            return apply_regime_guard(
                ci,
                snapshot,
                sales,
                bin_observations,
                as_of=as_of,
            )
        except Exception as exc:
            # A guard must never take the market engine down, but launch
            # safety also means an unguarded BUY must not survive the error.
            log.exception("market regime guard failed for card_id=%s", snapshot.get("source_card_id"))
            return fail_closed_regime_guard(ci, snapshot, exc)

    futgg_intelligence.evaluate_card = guarded_evaluate_card
    futgg_intelligence._market_regime_guard_installed = True
    log.info(
        "installed market regime guard (series_drop=%.0f%%, velocity=%d/%dm, median_gap=%.0f%%)",
        SERIES_DROP_PCT * 100,
        VELOCITY_MIN_SALES,
        int(VELOCITY_MAX_SPAN_MINUTES),
        MEDIAN_DISCOUNT_PCT * 100,
    )
