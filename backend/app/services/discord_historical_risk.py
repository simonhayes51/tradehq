from __future__ import annotations

"""
Historical lifecycle risk gate for Discord trade calls.

The normal Opportunity Engine is intentionally a current-market mispricing
detector. During launch/repricing that can be dangerous: yesterday's completed
sales remain high while today's market is moving lower, so a falling card can
look *more* undervalued the faster it falls.

EAFC.Live already owns a clean FC26 daily-price research set. This module uses
that data as a PRIOR for Discord only: at the same lifecycle offset (Web App,
Early Access, or Full Release), how did comparable FC26 cards move over the
next 72 hours?

It never creates a BUY and never changes the canonical website opportunity.
It can only suppress a Discord broadcast when the historical prior is bearish
and the live card does not provide enough contradictory evidence.

One prior season is evidence, not destiny. Current live trend wins over a
moderately bearish prior; only genuinely adverse historical setups block.
"""

import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.services.game_cycle import GAME_CYCLE, active_game_year
from app.services.futgg_trend import RECOVERING, UPTREND


# Official EA launch dates. GAME_CYCLE env values take precedence for the
# active year; these known dates keep the safety layer useful if production
# has not yet populated the optional launch-date env vars.
_CYCLE_DATES = {
    26: {
        "web_app": date(2025, 9, 17),
        "early_access": date(2025, 9, 19),
        "full_release": date(2025, 9, 26),
    },
    27: {
        "web_app": date(2026, 9, 16),
        "early_access": date(2026, 9, 18),
        "full_release": date(2026, 9, 25),
    },
}

HISTORICAL_GAME_YEAR = 26
FORWARD_HORIZON_DAYS = 3
LAUNCH_PRIOR_MAX_DAYS_AFTER_FULL_RELEASE = int(
    os.getenv("DISCORD_HISTORICAL_PRIOR_MAX_DAYS_AFTER_FULL_RELEASE", "21")
)
MIN_SAMPLE = int(os.getenv("DISCORD_HISTORICAL_PRIOR_MIN_SAMPLE", "25"))
BEARISH_MEDIAN_RETURN_PCT = float(
    os.getenv("DISCORD_HISTORICAL_PRIOR_BEARISH_MEDIAN_PCT", "-2.0")
)
SEVERE_MEDIAN_RETURN_PCT = float(
    os.getenv("DISCORD_HISTORICAL_PRIOR_SEVERE_MEDIAN_PCT", "-5.0")
)
BEARISH_NEGATIVE_RATE = float(
    os.getenv("DISCORD_HISTORICAL_PRIOR_BEARISH_NEGATIVE_RATE", "0.60")
)
SEVERE_NEGATIVE_RATE = float(
    os.getenv("DISCORD_HISTORICAL_PRIOR_SEVERE_NEGATIVE_RATE", "0.70")
)
LARGE_TARGET_UPLIFT = float(
    os.getenv("DISCORD_HISTORICAL_PRIOR_LARGE_TARGET_UPLIFT", "0.15")
)


@dataclass(frozen=True)
class HistoricalPrior:
    anchor: str
    lifecycle_offset_days: int
    baseline_date: date
    target_date: date
    sample_size: int
    median_return_pct: float
    negative_rate: float
    cohort: str


@dataclass(frozen=True)
class HistoricalRiskDecision:
    block: bool
    reason: str
    prior: Optional[HistoricalPrior] = None


def historical_prior_enabled() -> bool:
    return (os.getenv("DISCORD_HISTORICAL_PRIOR_ENABLED") or "1").strip().lower() not in {
        "0", "false", "off", "disabled",
    }


def _active_dates() -> Optional[dict[str, date]]:
    year = active_game_year()
    known = _CYCLE_DATES.get(year)
    if known is None:
        return None
    return {
        "web_app": GAME_CYCLE.web_app_launch or known["web_app"],
        "early_access": GAME_CYCLE.early_access or known["early_access"],
        "full_release": GAME_CYCLE.full_release or known["full_release"],
    }


def _lifecycle_match(as_of: date) -> Optional[tuple[str, int, date, date]]:
    current = _active_dates()
    historical = _CYCLE_DATES.get(HISTORICAL_GAME_YEAR)
    if current is None or historical is None:
        return None

    if as_of >= current["full_release"]:
        offset = (as_of - current["full_release"]).days
        if offset > LAUNCH_PRIOR_MAX_DAYS_AFTER_FULL_RELEASE:
            return None
        anchor = "full_release"
    elif as_of >= current["early_access"]:
        offset = (as_of - current["early_access"]).days
        anchor = "early_access"
    elif as_of >= current["web_app"]:
        offset = (as_of - current["web_app"]).days
        anchor = "web_app"
    else:
        return None

    baseline = historical[anchor] + timedelta(days=offset)
    return anchor, offset, baseline, baseline + timedelta(days=FORWARD_HORIZON_DAYS)


def _rating_band(rating: Any) -> Optional[tuple[int, int]]:
    try:
        value = int(rating)
    except (TypeError, ValueError):
        return None
    if value < 78:
        return 0, 77
    if value < 81:
        return 78, 80
    if value < 84:
        return 81, 83
    if value < 87:
        return 84, 86
    return 87, 99


def _summarise(
    rows: Sequence[dict[str, Any]],
    *,
    anchor: str,
    offset: int,
    baseline: date,
    target: date,
    cohort: str,
) -> Optional[HistoricalPrior]:
    returns: list[float] = []
    for row in rows:
        try:
            entry = float(row["entry_price"])
            exit_price = float(row["exit_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry <= 0 or exit_price <= 0:
            continue
        returns.append((exit_price - entry) / entry * 100.0)

    if len(returns) < MIN_SAMPLE:
        return None
    return HistoricalPrior(
        anchor=anchor,
        lifecycle_offset_days=offset,
        baseline_date=baseline,
        target_date=target,
        sample_size=len(returns),
        median_return_pct=statistics.median(returns),
        negative_rate=sum(1 for value in returns if value < 0) / len(returns),
        cohort=cohort,
    )


async def _load_prior_rows(conn, baseline: date, target: date) -> list[dict[str, Any]]:
    """Load clean FC26 base-gold 72h pairs.

    The historical loader is daily and not perfectly dense, so each leg accepts
    the first observation on the requested day or the following day. Cards
    flagged by the extraction-quality table are excluded when that table is
    present. This mirrors the repository's historical research discipline.
    """
    quality_table = await conn.fetchval(
        "SELECT to_regclass(current_schema() || '.fc26_historical_extraction_runs')"
    )
    quality_clause = ""
    if quality_table:
        quality_clause = """
          AND NOT EXISTS (
              SELECT 1
              FROM fc26_historical_extraction_runs q
              WHERE q.card_id = b.card_id
                AND (
                    q.status != 'success'
                    OR COALESCE(q.calibration_max_pct_err, 0) > 20
                    OR COALESCE(array_length(q.tick_warnings, 1), 0) > 0
                )
          )
        """

    rows = await conn.fetch(
        f"""
        WITH b AS (
            SELECT DISTINCT ON (card_id) card_id, price
            FROM fc26_historical_daily_prices
            WHERE observed_date BETWEEN $1::date AND ($1::date + 1)
              AND price > 0
            ORDER BY card_id, observed_date
        ),
        t AS (
            SELECT DISTINCT ON (card_id) card_id, price
            FROM fc26_historical_daily_prices
            WHERE observed_date BETWEEN $2::date AND ($2::date + 1)
              AND price > 0
            ORDER BY card_id, observed_date
        )
        SELECT b.card_id, b.price AS entry_price, t.price AS exit_price,
               p.rating, p.primary_position, p.price_tier
        FROM b
        JOIN t ON t.card_id = b.card_id
        JOIN futgg_players p
          ON p.source_card_id = b.card_id
         AND p.game_year = {HISTORICAL_GAME_YEAR}
        WHERE p.price_tier IN ('gold_common', 'gold_rare')
        {quality_clause}
        """,
        baseline,
        target,
    )
    return [dict(row) for row in rows]


def _pick_prior(
    rows: Sequence[dict[str, Any]],
    row: dict[str, Any],
    *,
    anchor: str,
    offset: int,
    baseline: date,
    target: date,
) -> Optional[HistoricalPrior]:
    """Prefer a comparable gold/rating cohort, then fall back to the broad
    gold market. Promo/Icon/Hero cards intentionally use the broad market
    prior because the FC26 research set measures incumbent gold supply shock,
    not historical promo-card prices."""
    price_tier = str(row.get("price_tier") or "")
    rating_band = _rating_band(row.get("rating"))
    if price_tier in {"gold_common", "gold_rare"} and rating_band is not None:
        lo, hi = rating_band
        matched = [
            item for item in rows
            if item.get("price_tier") == price_tier
            and item.get("rating") is not None
            and lo <= int(item["rating"]) <= hi
        ]
        prior = _summarise(
            matched,
            anchor=anchor,
            offset=offset,
            baseline=baseline,
            target=target,
            cohort=f"{price_tier}:{lo}-{hi}",
        )
        if prior is not None:
            return prior

    return _summarise(
        rows,
        anchor=anchor,
        offset=offset,
        baseline=baseline,
        target=target,
        cohort="broad_gold_market",
    )


def decide_from_prior(prior: HistoricalPrior, ci: Any) -> HistoricalRiskDecision:
    """Pure decision rule so the policy is unit-testable without Postgres."""
    trend = str(getattr(ci, "trend_state", "") or "")
    live_recovery = trend in {RECOVERING, UPTREND}

    current_buy = getattr(ci, "current_executable_buy", None)
    target = getattr(ci, "recommended_sell_target", None)
    target_uplift: Optional[float] = None
    try:
        if current_buy is not None and target is not None and Decimal(str(current_buy)) > 0:
            target_uplift = float(
                (Decimal(str(target)) - Decimal(str(current_buy))) / Decimal(str(current_buy))
            )
    except Exception:
        target_uplift = None

    severe = (
        prior.median_return_pct <= SEVERE_MEDIAN_RETURN_PCT
        and prior.negative_rate >= SEVERE_NEGATIVE_RATE
    )
    bearish = (
        prior.median_return_pct <= BEARISH_MEDIAN_RETURN_PCT
        and prior.negative_rate >= BEARISH_NEGATIVE_RATE
    )

    # Current evidence is allowed to beat one old season. A recovering/uptrend
    # card is not blocked by a merely bearish prior. An extreme historical
    # sell-off still blocks a large upside target: that is exactly where a
    # backward-looking sales median can manufacture a false "discount".
    if severe and (not live_recovery or target_uplift is None or target_uplift >= LARGE_TARGET_UPLIFT):
        return HistoricalRiskDecision(
            True,
            (
                f"FC26 {prior.anchor}+{prior.lifecycle_offset_days}d prior was strongly bearish "
                f"(median {prior.median_return_pct:+.1f}% over 72h; "
                f"{prior.negative_rate:.0%} fell; n={prior.sample_size}, {prior.cohort})"
            ),
            prior,
        )

    if (
        bearish
        and not live_recovery
        and target_uplift is not None
        and target_uplift >= LARGE_TARGET_UPLIFT
    ):
        return HistoricalRiskDecision(
            True,
            (
                f"FC26 {prior.anchor}+{prior.lifecycle_offset_days}d prior was bearish "
                f"(median {prior.median_return_pct:+.1f}% over 72h; "
                f"{prior.negative_rate:.0%} fell) while this call needs "
                f"{target_uplift:.0%} upside from the live price"
            ),
            prior,
        )

    return HistoricalRiskDecision(False, "historical prior does not contradict the live trade", prior)


async def assess_historical_feed_risk(
    conn,
    row: dict[str, Any],
    ci: Any,
    *,
    now: Optional[datetime] = None,
) -> HistoricalRiskDecision:
    if not historical_prior_enabled():
        return HistoricalRiskDecision(False, "historical prior disabled")

    as_of = (now or datetime.now(timezone.utc)).date()
    lifecycle = _lifecycle_match(as_of)
    if lifecycle is None:
        return HistoricalRiskDecision(False, "outside comparable launch lifecycle")

    anchor, offset, baseline, target = lifecycle
    try:
        historical_table = await conn.fetchval(
            "SELECT to_regclass(current_schema() || '.fc26_historical_daily_prices')"
        )
        if not historical_table:
            return HistoricalRiskDecision(False, "FC26 historical price table unavailable")
        rows = await _load_prior_rows(conn, baseline, target)
    except Exception as exc:
        # This gate is additive. A research-table outage must not take the
        # Discord worker down; the existing live quality gates still apply.
        return HistoricalRiskDecision(False, f"historical prior query unavailable: {type(exc).__name__}")

    prior = _pick_prior(
        rows,
        row,
        anchor=anchor,
        offset=offset,
        baseline=baseline,
        target=target,
    )
    if prior is None:
        return HistoricalRiskDecision(False, "not enough clean FC26 comparable cards")

    return decide_from_prior(prior, ci)
