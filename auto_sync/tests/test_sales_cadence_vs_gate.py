"""
A rating band scraped less often than the engine accepts sales for can
never produce a recommendation.

WHY THIS EXISTS
---------------
Two numbers, set in different places for different reasons, silently
contradicted each other in production:

  * SALES_INTERVALS - how often SalesSync re-reads each rating band. Set
    by env var, tuned down hard for cost ("$20 a month, not $100").
  * futgg_config.reject_sales_older_than_minutes - how old a sale may be
    before the engine refuses to value the card at all. Set in code, for
    advice quality.

Live settings were:

    intervals={'85_plus': 1440, '80_84': 2880,
               '75_79': 5760, 'under_75': 40320}

against a 4320-minute (72h) limit. The bottom two bands - 96 hours and 28
days - are scheduled to be re-read AFTER their sales have already aged
past what the engine will accept. Every card in them is rejected as
STALE_SALES forever, no matter how healthy the scraper is.

Nothing surfaced it. The dashboard just showed no trades, and cards were
rejected one at a time with a reason nobody aggregated. Neither side can
see the contradiction: the cost knob does not know the gate exists, and
the gate does not know the cadence.

The constant is duplicated in futgg_sales_sync (it deploys separately and
cannot import app.services), so the drift check below is the thing
keeping the two honest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "auto_sync"))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_sales_sync as ss  # noqa: E402


class TestTheMirroredConstantDoesNotDrift:
    def test_it_equals_the_backend_engine_config(self):
        """The whole check is worthless if the mirrored limit stops
        matching the real one."""
        sys.path.insert(0, str(ROOT / "backend"))
        from app.services.futgg_config import ENGINE_CONFIG

        assert (
            ss.ENGINE_REJECT_SALES_OLDER_THAN_MINUTES
            == ENGINE_CONFIG.reject_sales_older_than_minutes
        ), (
            "futgg_sales_sync's mirror of reject_sales_older_than_minutes has "
            "drifted from futgg_config. Update both, or the cadence check "
            "silently validates against the wrong limit."
        )


class TestConflictDetection:
    LIMIT = 4320  # 72h

    def test_the_exact_production_configuration_is_flagged(self):
        conflicts = ss.cadence_gate_conflicts(
            {"85_plus": 1440, "80_84": 2880, "75_79": 5760, "under_75": 40320},
            reject_after_minutes=self.LIMIT,
        )
        assert [band for band, _, _ in conflicts] == ["75_79", "under_75"]

    def test_the_shipped_defaults_are_internally_consistent(self):
        """60/240/720/2880 against 4320 - the defaults must not ship
        already broken."""
        assert ss.cadence_gate_conflicts(
            {"85_plus": 60, "80_84": 240, "75_79": 720, "under_75": 2880},
            reject_after_minutes=self.LIMIT,
        ) == []

    def test_a_band_exactly_at_the_limit_is_a_conflict(self):
        """Equality is not safe: a card re-read exactly every 4320 minutes
        has 4320-minute-old sales at the instant it comes due, which the
        gate rejects. It needs headroom, not parity."""
        conflicts = ss.cadence_gate_conflicts(
            {"under_75": self.LIMIT}, reject_after_minutes=self.LIMIT,
        )
        assert [band for band, _, _ in conflicts] == ["under_75"]

    def test_a_band_just_inside_the_limit_is_not_flagged(self):
        assert ss.cadence_gate_conflicts(
            {"under_75": self.LIMIT - 1}, reject_after_minutes=self.LIMIT,
        ) == []

    def test_it_reports_the_numbers_needed_to_act(self):
        (band, interval, limit), = ss.cadence_gate_conflicts(
            {"under_75": 40320}, reject_after_minutes=self.LIMIT,
        )
        assert (band, interval, limit) == ("under_75", 40320, self.LIMIT)

    def test_raising_the_gate_resolves_the_conflict(self):
        """The other half of the trade-off: accepting older sales as
        evidence is a valid answer, and the check must agree it is."""
        assert ss.cadence_gate_conflicts(
            {"under_75": 40320}, reject_after_minutes=40321,
        ) == []


class TestTheWarningIsEmittedAtStartup:
    def test_a_clean_configuration_logs_nothing(self, caplog):
        ss.log_cadence_gate_conflicts()
        conflicts = ss.cadence_gate_conflicts()
        if not conflicts:
            assert "CADENCE EXCEEDS" not in caplog.text

    def test_a_conflicting_configuration_names_the_bands(self, caplog, monkeypatch):
        monkeypatch.setattr(
            ss, "SALES_INTERVALS",
            {"85_plus": 1440, "80_84": 2880, "75_79": 5760, "under_75": 40320},
        )
        monkeypatch.setattr(ss, "ENGINE_REJECT_SALES_OLDER_THAN_MINUTES", 4320)
        with caplog.at_level("WARNING"):
            ss.log_cadence_gate_conflicts()
        assert "CADENCE EXCEEDS" in caplog.text
        assert "75_79=5760min" in caplog.text
        assert "under_75=40320min" in caplog.text
        # Must name both ways out, since which one is right is a product
        # decision about cost versus coverage, not a technical one.
        assert "shorten the interval" in caplog.text
        assert "FUTGG_REJECT_SALES_OLDER_THAN_MIN" in caplog.text
