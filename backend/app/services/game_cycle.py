# app/services/game_cycle.py
"""
Single source of truth for WHICH EA FC game cycle this deployment is
operating on, and where inside that cycle's launch we currently are.

Why this module exists
----------------------
Before it, "which game year" was answered independently in two places
with CONFLICTING defaults:

  * auto_sync/futgg_bulk_price_sync.py + futgg_sales_sync.py:
        GAME_YEAR = os.getenv("FUTGG_GAME_YEAR", "26")
  * backend/main.py:
        int(os.getenv("INVESTMENT_ENGINE_GAME_YEAR", "27"))

So a deployment that set neither would ingest FC26 prices while the
Investment Engine looked for FC27 cohorts - and would silently find
nothing at all, with no error, because "no cards in this cohort" is a
legitimate quiet state. That is exactly the kind of launch-morning
failure that is invisible until someone asks why the engine has produced
nothing for six hours.

This module makes the game cycle explicit, single-valued, and
inspectable. `EAFC_GAME_YEAR` is the one variable that decides it;
everything else derives. The legacy per-service variables are still
honoured as overrides (so nothing breaks mid-deploy), but a mismatch
between them is reported loudly by `describe()` and surfaced in the
launch-health endpoint rather than being left to silently misbehave.

Launch phases
-------------
The phase concept is deliberately DESCRIPTIVE, not prescriptive: it
labels where the calendar says we are, and is used for reporting, copy,
and the FC26-prior decay weighting that already existed in
investment_engine/config.py. No investment DECISION is ever gated on a
phase - a strategy's eligibility comes from live market evidence
(cohort regime state, coverage/readiness gates), never from "the
calendar says it is day 9". That distinction matters: hardcoding
"FR+9 is when to buy" is precisely the overfitting the FC26 replay work
warned against.

Dates are configurable because EA has not announced FC27's calendar. An
unset launch date is a first-class state (`launch_date is None` ->
phase UNKNOWN), never a guess.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------
# Launch phases (see module docstring: descriptive labels, never gates)
# ---------------------------------------------------------------------
PHASE_UNKNOWN = "UNKNOWN"              # no launch date configured
PHASE_PRE_LAUNCH = "PRE_LAUNCH"        # before the web app opens
PHASE_WEB_APP = "WEB_APP"              # web app open, pre-Early-Access
PHASE_EARLY_ACCESS = "EARLY_ACCESS"    # Early Access period
PHASE_FULL_RELEASE = "FULL_RELEASE"    # first ~3 weeks after full release
PHASE_ESTABLISHED = "ESTABLISHED"      # launch is over; an ordinary market

ALL_PHASES = (
    PHASE_UNKNOWN, PHASE_PRE_LAUNCH, PHASE_WEB_APP,
    PHASE_EARLY_ACCESS, PHASE_FULL_RELEASE, PHASE_ESTABLISHED,
)

# EA has now published the FC27 launch calendar. Environment values still
# override these defaults, but leaving the optional vars unset must no longer
# make the live game cycle UNKNOWN (which also silently disables FC26-prior
# weighting in the Investment Engine).
_KNOWN_DATES_BY_GAME_YEAR = {
    27: {
        "web_app_launch": date(2026, 9, 16),
        "early_access": date(2026, 9, 18),
        "full_release": date(2026, 9, 25),
    },
}

# How long after Full Release we still consider the market to be in its
# launch/repricing period rather than an ordinary established market.
# Matches the window the FC26 research and replay actually examined
# (Full Release + 21 days) - used only for labelling/reporting.
FULL_RELEASE_WINDOW_DAYS = 21


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_date(name: str) -> Optional[date]:
    """YYYY-MM-DD, or None. An unparseable value is treated as unset
    rather than crashing the app at import time - a typo'd launch date
    must not take the whole backend down, it must show up as UNKNOWN in
    the launch-health report."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class GameCycle:
    """The active game cycle. `game_year` is the two-digit year FUT.GG
    encodes in its own URLs/APIs (26 = FC26, 27 = FC27) - confirmed
    against auto_sync/futgg_common.py's parse_card_url regex, which
    reads it straight out of the card URL path."""
    game_year: int
    web_app_launch: Optional[date]
    early_access: Optional[date]
    full_release: Optional[date]

    # ---- Phase ------------------------------------------------------
    def phase(self, now: Optional[datetime] = None) -> str:
        today = (now or datetime.now(timezone.utc)).date()
        if self.web_app_launch is None:
            return PHASE_UNKNOWN
        if today < self.web_app_launch:
            return PHASE_PRE_LAUNCH
        if self.early_access is not None and today < self.early_access:
            return PHASE_WEB_APP
        if self.full_release is not None and today < self.full_release:
            return PHASE_EARLY_ACCESS
        if self.full_release is None:
            # Web app open, no full-release date known yet.
            return PHASE_WEB_APP
        if today <= self.full_release + timedelta(days=FULL_RELEASE_WINDOW_DAYS):
            return PHASE_FULL_RELEASE
        return PHASE_ESTABLISHED

    def hours_since_launch(self, now: Optional[datetime] = None) -> Optional[float]:
        """Elapsed hours since the web app opened - the input
        investment_engine/config.py's fc26_prior_decay_bands_hours and
        LAUNCH_STARTER_ACCUMULATION's launch window both already expect.
        None when no launch date is configured (never 0, which would
        read as 'launch just happened')."""
        if self.web_app_launch is None:
            return None
        now = now or datetime.now(timezone.utc)
        launch_dt = datetime.combine(self.web_app_launch, datetime.min.time(), tzinfo=timezone.utc)
        return (now - launch_dt).total_seconds() / 3600.0

    def full_release_offset_days(self, now: Optional[datetime] = None) -> Optional[int]:
        if self.full_release is None:
            return None
        today = (now or datetime.now(timezone.utc)).date()
        return (today - self.full_release).days

    def as_dict(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        return {
            "game_year": self.game_year,
            "phase": self.phase(now),
            "web_app_launch": self.web_app_launch.isoformat() if self.web_app_launch else None,
            "early_access": self.early_access.isoformat() if self.early_access else None,
            "full_release": self.full_release.isoformat() if self.full_release else None,
            "hours_since_launch": self.hours_since_launch(now),
            "full_release_offset_days": self.full_release_offset_days(now),
        }


def _load() -> GameCycle:
    game_year = _env_int("EAFC_GAME_YEAR", 27) or 27
    known = _KNOWN_DATES_BY_GAME_YEAR.get(game_year, {})
    return GameCycle(
        game_year=game_year,
        web_app_launch=_env_date("EAFC_WEB_APP_LAUNCH_DATE") or known.get("web_app_launch"),
        early_access=_env_date("EAFC_EARLY_ACCESS_DATE") or known.get("early_access"),
        full_release=_env_date("EAFC_FULL_RELEASE_DATE") or known.get("full_release"),
    )


GAME_CYCLE = _load()


def active_game_year() -> int:
    """THE game year every service should use. Prefer this over reading
    any env var directly."""
    return GAME_CYCLE.game_year


def ingestion_game_year() -> int:
    """What the FUT.GG ingestion workers will actually use. Reads the
    legacy FUTGG_GAME_YEAR override if set, so this reports REALITY (what
    auto_sync will really scrape), not what we wish were true - the whole
    point of consistency_report() below is to compare the two honestly."""
    legacy = _env_int("FUTGG_GAME_YEAR", None)
    return legacy if legacy is not None else GAME_CYCLE.game_year


def investment_game_year() -> int:
    """What the Investment Engine will use, honouring its own legacy
    override for the same reason as ingestion_game_year()."""
    legacy = _env_int("INVESTMENT_ENGINE_GAME_YEAR", None)
    return legacy if legacy is not None else GAME_CYCLE.game_year


def consistency_report() -> Dict[str, Any]:
    """Is every service actually operating on the SAME game year?

    A mismatch here is the specific silent failure this module was
    written to prevent: ingestion writing FC26 rows while the Investment
    Engine queries FC27 cohorts produces no error anywhere, just an
    engine that mysteriously never finds any cards. Surfaced by
    /api/ops/launch-health so it is visible on launch morning rather
    than after hours of confusion."""
    configured = GAME_CYCLE.game_year
    ingestion = ingestion_game_year()
    investment = investment_game_year()
    consistent = configured == ingestion == investment
    problems = []
    if ingestion != configured:
        problems.append(
            f"FUTGG_GAME_YEAR={ingestion} overrides EAFC_GAME_YEAR={configured} - "
            "ingestion will scrape a different game year than the rest of the app"
        )
    if investment != configured:
        problems.append(
            f"INVESTMENT_ENGINE_GAME_YEAR={investment} overrides EAFC_GAME_YEAR={configured} - "
            "the Investment Engine will look for cohorts in a different game year than ingestion writes"
        )
    return {
        "consistent": consistent,
        "configured_game_year": configured,
        "ingestion_game_year": ingestion,
        "investment_game_year": investment,
        "problems": problems,
    }


def describe(now: Optional[datetime] = None) -> Dict[str, Any]:
    out = GAME_CYCLE.as_dict(now)
    out["consistency"] = consistency_report()
    return out
