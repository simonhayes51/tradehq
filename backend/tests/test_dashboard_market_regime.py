"""
Market Pulse must not report a confident, healthy market when it cannot
value anything.

WHY THIS EXISTS
---------------
Seen live on the dashboard: "STRONG MARKET 95/100 - Prices and sales are
behaving normally across tracked cards right now", directly above a
"BUY SIGNALS 0" tile, at a moment when the scanner was logging
`evaluated=1344 ... recommendations=0` - i.e. the engine could not value
a single card.

Two independent defects produced that, both in code written to fix the
EARLIER complaint that this number was frozen at 95 forever. It stopped
being frozen and started being wrong, which is not an improvement:

  1. Confidence was `max(50, min(95, 50 + min(total, 500) / 10))` - a
     function of how many candidates the scanner produced, not of how
     much was learned from them. 450+ candidates pinned it to 95 whether
     or not any could be valued, and the floor of 50 meant it could never
     admit to knowing nothing.

  2. Every metric feeding the classifier degrades silently to 0.0 when
     evidence is absent. Zero cards falling and zero cards KNOWN produce
     the same falling_pct of 0.0; no buy signals produces avg_discount
     0.0. So "we know nothing" scored identically to "everything is calm".

The rule these tests pin: not knowing is a real answer, and it must be
reported as one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.routers.v2.dashboard import (
    MIN_VALUED_CARDS_FOR_REGIME,
    _compute_live_market_regime,
)
from app.services.futgg_trend import INSUFFICIENT_TREND_DATA


@dataclass
class FakeIntel:
    signal: str = "insufficient_data"
    trend_state: str = INSUFFICIENT_TREND_DATA
    fair_value: Optional[float] = None


def pairs(total: int, *, valued: int = 0, falling: int = 0, discount_pct: float = 0.0):
    """A scored-candidate pool with an explicit evidence level."""
    out = []
    for i in range(total):
        if i < valued:
            intel = FakeIntel(
                signal="buy" if discount_pct else "watch",
                trend_state="downtrend" if i < falling else "stable",
                fair_value=100_000.0,
            )
            row = {
                "current_bin": 100_000 * (1 - discount_pct / 100),
                "sales_count": 30,
                "sales_window_span_minutes": 600.0,
            }
        else:
            intel = FakeIntel()
            row = {"current_bin": 100_000, "sales_count": 0, "sales_window_span_minutes": None}
        out.append((row, intel))
    return out


class TestNoEvidence:
    def test_the_exact_production_case_reports_unknown(self):
        """1,344 cards scored, none valued - what the dashboard showed as
        a 95/100 strong market."""
        result = _compute_live_market_regime(pairs(1344, valued=0))
        assert result["label"] == "Unknown"
        assert result["confidence"] == 0
        assert result["dataQuality"] == "LIMITED"

    def test_it_says_how_much_it_could_actually_value(self):
        result = _compute_live_market_regime(pairs(1344, valued=0))
        assert "0 of 1344" in result["summary"]

    def test_an_empty_pool_is_still_unknown(self):
        result = _compute_live_market_regime([])
        assert result["label"] == "Unknown"
        assert result["confidence"] == 0

    def test_a_handful_of_valued_cards_is_not_enough_to_call_a_market(self):
        thin = _compute_live_market_regime(
            pairs(1344, valued=MIN_VALUED_CARDS_FOR_REGIME - 1)
        )
        assert thin["label"] == "Unknown"

    def test_a_huge_scan_cannot_buy_confidence_on_its_own(self):
        """The precise mechanism of the bug: scaling the candidate pool
        must not move confidence when none of it can be valued."""
        small = _compute_live_market_regime(pairs(200, valued=0))
        huge = _compute_live_market_regime(pairs(20_000, valued=0))
        assert small["confidence"] == huge["confidence"] == 0


class TestConfidenceTracksEvidence:
    def test_confidence_rises_with_the_share_of_cards_actually_valued(self):
        scores = [
            _compute_live_market_regime(pairs(1000, valued=v))["confidence"]
            for v in (100, 300, 600, 900)
        ]
        assert scores == sorted(scores)
        assert scores[0] < scores[-1]

    def test_confidence_never_claims_certainty(self):
        result = _compute_live_market_regime(pairs(5000, valued=5000, discount_pct=8))
        assert result["confidence"] <= 95

    def test_there_is_no_floor_under_confidence(self):
        """The old formula could not go below 50, so "we know almost
        nothing" and "we know half the market" were indistinguishable."""
        result = _compute_live_market_regime(pairs(1000, valued=60))
        assert result["confidence"] < 50

    def test_data_quality_downgrades_when_evidence_is_thin(self):
        thin = _compute_live_market_regime(pairs(1000, valued=100))
        rich = _compute_live_market_regime(pairs(1000, valued=900, discount_pct=8))
        assert thin["dataQuality"] == "LIMITED"
        assert rich["dataQuality"] == "GOOD"


class TestVerdictIsSeparateFromConfidence:
    def test_a_falling_market_is_labelled_falling_even_when_well_evidenced(self):
        """Confidence and condition are different axes. A confident read
        of a bad market must not read as a good one - which is exactly
        what the frontend's mood(score) did before it was given the
        backend's own label."""
        result = _compute_live_market_regime(
            pairs(1344, valued=900, falling=400, discount_pct=8)
        )
        assert result["label"] == "Prices Dropping"
        assert result["confidence"] > 50

    def test_a_genuinely_good_market_still_says_so(self):
        result = _compute_live_market_regime(pairs(1344, valued=900, discount_pct=8))
        assert result["label"] == "Good Time to Buy"
        assert result["confidence"] > 50
