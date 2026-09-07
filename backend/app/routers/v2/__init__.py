# app/routers/v2/
from __future__ import annotations

from fastapi import APIRouter

from app.routers.v2.health import router as health_router
from app.routers.v2.players import router as players_router
from app.routers.v2.market import router as market_router
from app.routers.v2.sbc import router as sbc_router
from app.routers.v2.analytics import router as analytics_router
from app.routers.v2.recommendations import router as recommendations_router
from app.routers.v2.dashboard import router as dashboard_router
from app.routers.v2.trades import router as trades_router
from app.routers.v2.ai_chat import router as ai_chat_router
from app.routers.v2.futgg_market import router as futgg_market_router
from app.routers.v2.companion import router as companion_router
from app.routers.v2.investments import router as investments_router
from app.routers.v2.alerts import router as alerts_router
from app.routers.v2.events import router as events_router
from app.routers.v2.onboarding import router as onboarding_router
from app.routers.v2.intelligence_health import router as intelligence_health_router
from app.routers.v2.market_transitions import router as market_transitions_router
from app.routers.v2.calibration import router as calibration_router

router = APIRouter(prefix="/api/v2")
router.include_router(health_router)
router.include_router(players_router)
router.include_router(market_router)
router.include_router(sbc_router)
router.include_router(analytics_router)
router.include_router(recommendations_router)
router.include_router(dashboard_router)
router.include_router(trades_router)
router.include_router(ai_chat_router)
# FUT.GG-backed market-intelligence layer (migrations/038) - see that
# router module's own docstring for the full endpoint list and why it's
# ungated. Registered last: purely additive, no path overlap with any
# router above (confirmed against players_router/market_router's own
# path prefixes before adding this).
router.include_router(futgg_market_router)
# EAFC Live Companion browser extension API (eafc-live-extension/) - a
# thin read-only reshaping of the same provider/intelligence pair
# futgg_market_router uses, at its own /companion/* prefix so it can
# evolve (rate limits, API-key auth for the extension) independently of
# the main market-intelligence surface.
router.include_router(companion_router)
# Investment Engine (migrations 058-061, app/services/investment_engine/)
# - separate concept from futgg_market_router's opportunities/trade-finder
# surface above; see that package's __init__.py docstring for the
# Trade-vs-Investment distinction. Also purely additive/last.
router.include_router(investments_router)
# Fired-alert feed + dismissal (alerts_log, migration 072). Separate from
# main.py's /api/watchlist-alerts rule CRUD - see this router's docstring
# for why conflating the two is what made alerts undeletable.
router.include_router(alerts_router)
# New-member onboarding state (migration 074). Persisted server-side so a
# member who completes or skips it on desktop is not asked again on their
# phone.
router.include_router(onboarding_router)
# Internal funnel events (migration 075). Mounted here, under /api/v2,
# rather than at /api/events - main.py already owns that path for the
# market-events feed, which is a different thing entirely.
router.include_router(events_router)
# Admin-only cheap aggregates over the current scanner state and state
# transitions. This is intentionally separate from the hot market router
# so operational diagnostics never complicate the request path serving
# opportunities/trade-finder/player pages.
router.include_router(intelligence_health_router)
# Elite/premium market-change feed. Only genuine state transitions are
# returned; initial scanner population never appears as fake "news".
router.include_router(market_transitions_router)
# Admin-only, read-only calibration evidence. This can flag statistically
# supported threshold candidates for human review but never mutates live config.
router.include_router(calibration_router)
