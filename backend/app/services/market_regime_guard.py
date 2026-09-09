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


@dataclass(frozen=True)
class RegimeAssessment:
    state: str = REGIME_NORMAL
    reason: str = ""
    drop_pct: Optional[float] = None
    sales_span_minutes: Optional[float] = None
    sales_count: int = 0
    current_vs_median_pct: Optional[float] = None

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


def apply_regime_guard(ci: Any, snapshot: Mapping[str, Any], sales: Optional[Sequence[Any]] = None):
    """Downgrade only. Never turns a non-buy into a buy and never fabricates value."""
    if getattr(ci, "signal", None) not in {"buy", "strong_buy"}:
        return ci

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
            return apply_regime_guard(ci, snapshot, sales)
        except Exception:
            # A guard must never take the market engine down. The underlying
            # engine result is preserved if this safety layer itself errors.
            log.exception("market regime guard failed for card_id=%s", snapshot.get("source_card_id"))
            return ci

    futgg_intelligence.evaluate_card = guarded_evaluate_card
    futgg_intelligence._market_regime_guard_installed = True
    log.info(
        "installed market regime guard (series_drop=%.0f%%, velocity=%d/%dm, median_gap=%.0f%%)",
        SERIES_DROP_PCT * 100,
        VELOCITY_MIN_SALES,
        int(VELOCITY_MAX_SPAN_MINUTES),
        MEDIAN_DISCOUNT_PCT * 100,
    )
