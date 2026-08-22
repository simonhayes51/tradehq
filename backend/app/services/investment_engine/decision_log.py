# app/services/investment_engine/decision_log.py
"""
Writes and grades investment_decision_log (migration 062) - the FC27
learning dataset.

Two disciplines this module exists to enforce:

1. RECORD REFUSALS, NOT JUST RECOMMENDATIONS. The engine's most
   defensible behaviour during a launch crash is everything it declined
   to recommend. Without a row per refusal we can never answer "was
   suppressing that card correct" - which is exactly the question FC26's
   data could not answer and FC27's must.

2. NEVER WRITE A ROW PER PASS PER CARD. The engine recomputes every few
   minutes over thousands of cards; logging unconditionally would write
   millions of near-identical rows a day and bury the signal. Instead a
   decision is fingerprinted, and an unchanged fingerprint is a no-op via
   migration 062's partial unique index. The table becomes a change-log
   of decision EPISODES.

Grading is strictly no-hindsight in the same sense as
futgg_outcome_grader.py and ledger.py: an outcome column is filled in
only once that horizon has genuinely elapsed, from the price as it was
observed, and NEVER feeds back into any decision. A NULL outcome means
"not yet known", never "no change".
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services import trading_math as tm

log = logging.getLogger("investment_decision_log")

DECISION_RECOMMENDED = "recommended"
DECISION_SUPPRESSED = "suppressed"

# Horizons graded onto a decision row, in hours. Chosen to match the
# holds the two shipped strategies actually use (premium: up to 72h;
# starter: longer) plus the short horizons that reveal whether a
# suppression was immediately vindicated.
GRADING_HORIZONS_HOURS = (6, 12, 24, 48, 72)

# Price is fingerprinted in coarse bands so ordinary tick-by-tick noise
# does not count as "the decision changed". 5% bands: a card drifting
# 1-2% keeps the same fingerprint (no new row); a card that moves enough
# to plausibly change the decision opens a new episode.
_PRICE_BAND_PCT = 0.05


def _price_band(price: Optional[float]) -> str:
    if price is None or price <= 0:
        return "none"
    # Log-spaced banding so the band width is proportional at every price
    # level (a 5% band on a 2M card and on a 500-coin card both mean "5%").
    import math
    return str(int(math.log(price) / math.log(1 + _PRICE_BAND_PCT)))


def decision_fingerprint(
    *, decision: str, reason: str, market_state: Optional[str], reference_price: Optional[float],
) -> str:
    """What counts as 'the same decision, still true'. Deliberately does
    NOT include confidence or momentum: those wobble continuously and
    would defeat the deduplication this fingerprint exists to provide."""
    raw = f"{decision}|{reason}|{market_state or ''}|{_price_band(reference_price)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


_INSERT = """
INSERT INTO investment_decision_log (
    game_year, source_card_id, strategy_key, cohort_key, decision, reason,
    reference_price, card_drawdown_pct, card_momentum_pct, relative_strength_pct,
    liquidity, investability, confidence,
    market_state, market_severity_class, market_drawdown_pct, cohort_price_index,
    signal_id, decision_fingerprint, shadow, engine_version, decided_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
ON CONFLICT (source_card_id, strategy_key, decision_fingerprint) WHERE superseded_at IS NULL
DO NOTHING
RETURNING id
"""

_SUPERSEDE_OTHERS = """
UPDATE investment_decision_log
SET superseded_at = now()
WHERE source_card_id = $1 AND strategy_key = $2
  AND superseded_at IS NULL
  AND decision_fingerprint <> $3
"""


async def record_decision(
    conn, *, game_year: int, card_id: int, strategy_key: str, cohort_key: str,
    decision: str, reason: str, engine_version: str, shadow: bool,
    reference_price: Optional[float] = None,
    card_drawdown_pct: Optional[float] = None,
    card_momentum_pct: Optional[float] = None,
    relative_strength_pct: Optional[float] = None,
    liquidity: Optional[str] = None,
    investability: Optional[str] = None,
    confidence: Optional[float] = None,
    market_state: Optional[str] = None,
    market_severity_class: Optional[str] = None,
    market_drawdown_pct: Optional[float] = None,
    cohort_price_index: Optional[float] = None,
    signal_id: Optional[int] = None,
) -> Optional[int]:
    """Record one decision episode. Returns the new row id, or None when
    this exact decision was already current (the dedup no-op case - by
    far the most common outcome, and not an error)."""
    fingerprint = decision_fingerprint(
        decision=decision, reason=reason, market_state=market_state, reference_price=reference_price,
    )
    try:
        # Close out any DIFFERENT current episode for this card+strategy
        # first, so exactly one row is ever current per card+strategy.
        await conn.execute(_SUPERSEDE_OTHERS, card_id, strategy_key, fingerprint)
        return await conn.fetchval(
            _INSERT, game_year, card_id, strategy_key, cohort_key, decision, reason,
            reference_price, card_drawdown_pct, card_momentum_pct, relative_strength_pct,
            liquidity, investability, confidence,
            market_state, market_severity_class, market_drawdown_pct, cohort_price_index,
            signal_id, fingerprint, shadow, engine_version, datetime.now(timezone.utc),
        )
    except Exception:
        # A learning-dataset write must never break an engine pass.
        log.warning("failed to record investment decision for card_id=%s", card_id, exc_info=True)
        return None


async def record_decisions_bulk(conn, decisions: List[Dict[str, Any]]) -> int:
    """Convenience wrapper - the runner collects a pass's decisions and
    writes them in one place rather than interleaving DB calls with the
    ranking loop."""
    written = 0
    for d in decisions:
        if await record_decision(conn, **d) is not None:
            written += 1
    return written


# ---------------------------------------------------------------------
# Outcome grading
# ---------------------------------------------------------------------

_COLUMN_FOR_HORIZON = {
    6: "price_after_6h",
    12: "price_after_12h",
    24: "price_after_24h",
    48: "price_after_48h",
    72: "price_after_72h",
}

_SELECT_UNGRADED = """
SELECT id, source_card_id, reference_price, decided_at,
       price_after_6h, price_after_12h, price_after_24h, price_after_48h, price_after_72h
FROM investment_decision_log
WHERE outcome_graded_at IS NULL
  AND decided_at <= now() - ($1 || ' hours')::interval
ORDER BY decided_at
LIMIT $2
"""


async def grade_pending_outcomes(
    conn, *, price_lookup, max_rows: int = 500, now: Optional[datetime] = None,
) -> int:
    """Fill in whichever horizons have genuinely elapsed for each row.

    NO HINDSIGHT: a horizon column is written from the price observed
    when that horizon actually passed - never from a best/worst price
    across the window, and never before the horizon elapsed. A row is
    marked outcome_graded_at only once its LONGEST horizon is done, so a
    partially-graded row keeps being revisited rather than being frozen
    early with NULLs that look like "no change".

    `price_lookup(card_id) -> Optional[float]` is injected (not queried
    here) for the same reason ledger.py injects it: this function stays
    testable, and never hardcodes which table backs "current price".
    """
    now = now or datetime.now(timezone.utc)
    shortest = min(GRADING_HORIZONS_HOURS)
    longest = max(GRADING_HORIZONS_HOURS)

    rows = await conn.fetch(_SELECT_UNGRADED, str(shortest), max_rows)
    graded = 0

    for row in rows:
        try:
            elapsed_hours = (now - row["decided_at"]).total_seconds() / 3600.0
            entry = row["reference_price"]
            if entry is None or float(entry) <= 0:
                # Nothing to measure against - mark done so it stops
                # being re-selected forever, rather than retrying a row
                # that can never be graded.
                await conn.execute(
                    "UPDATE investment_decision_log SET outcome_graded_at = $2 WHERE id = $1",
                    row["id"], now,
                )
                continue

            updates: Dict[str, Any] = {}
            for horizon in GRADING_HORIZONS_HOURS:
                column = _COLUMN_FOR_HORIZON[horizon]
                if row[column] is not None:
                    continue  # already graded at this horizon
                if elapsed_hours < horizon:
                    continue  # NOT YET KNOWN - leave NULL, revisit later
                price = await price_lookup(row["source_card_id"])
                if price is not None:
                    updates[column] = price

            if not updates and elapsed_hours < longest:
                continue

            # Derived after-tax ROI at the two horizons the reporting
            # surfaces use. Always via trading_math - never reimplemented.
            price_24 = updates.get("price_after_24h", row["price_after_24h"])
            price_72 = updates.get("price_after_72h", row["price_after_72h"])
            roi_24 = tm.net_roi(price_24, entry) if price_24 is not None else None
            roi_72 = tm.net_roi(price_72, entry) if price_72 is not None else None

            fully_graded = elapsed_hours >= longest
            sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(updates))
            args = list(updates.values())
            extra_index = len(args) + 2
            clause = f"{sets + ', ' if sets else ''}roi_after_24h_pct = ${extra_index}, " \
                     f"roi_after_72h_pct = ${extra_index + 1}, outcome_graded_at = ${extra_index + 2}"
            await conn.execute(
                f"UPDATE investment_decision_log SET {clause} WHERE id = $1",
                row["id"], *args,
                (float(roi_24) * 100) if roi_24 is not None else None,
                (float(roi_72) * 100) if roi_72 is not None else None,
                now if fully_graded else None,
            )
            graded += 1
        except Exception:
            log.warning("failed to grade investment_decision_log.id=%s", row["id"], exc_info=True)

    return graded
