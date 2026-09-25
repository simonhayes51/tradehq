// src/v2/hooks/useFutggMarket.js
//
// FUT.GG migration (see the FUTBIN -> FUT.GG migration plan): hooks for
// the new v2 endpoints backed by FUT.GG market data (backend built in a
// sibling repo, in parallel). Kept in their own file rather than folded
// into useRecommendationFeeds.js/usePlayerSummary.js because those hit a
// different, still-live endpoint family (/api/v2/recommendations/*,
// /api/v2/players/{id}/summary) that stays FUTBIN-backed for now - this
// file is only the new /api/v2/players, /api/v2/opportunities,
// /api/v2/trade-finder, /api/v2/market/freshness contract.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// Every list/detail endpoint here can 404 or fail entirely until the
// sibling backend repo ships its FUT.GG endpoints - __skipAuthRedirect
// keeps a 401 from hijacking the page, __noRetry hands retry ownership
// to react-query (see usePlayerSummary.js's note on double-retry storms),
// and retry:false means a 404 surfaces immediately as query.isError
// instead of hammering a route that plainly doesn't exist yet.
const REQUEST_OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 8_000 };
const DETAIL_REQUEST_OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 20_000 };

// /opportunities, /trade-finder and its strategy tabs all run through the
// backend's shared candidate/evaluation pipeline. A cold server-side pass
// can still be expensive, so these endpoints retain a longer request
// ceiling than cheap single-card lookups. More importantly, their React
// Query stale time now matches the backend's 90s candidate/evaluation
// cache: refetching after only 30s could deliberately arrive after an old
// 20s cache had expired and trigger another full scoring wave. Keeping the
// client and server cache windows aligned makes normal navigation reuse
// the answer we just paid to compute instead of creating avoidable load.
const SCAN_REQUEST_OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 20_000 };
const SCAN_STALE_TIME_MS = 90_000;

function cleanParams(params = {}) {
  const out = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === "" || value === null || value === undefined) continue;
    out[key] = value;
  }
  return out;
}

// GET /api/v2/players - paginated FUT.GG-backed card search/browse.
export function useFutggPlayers(params = {}, { enabled = true } = {}) {
  const clean = cleanParams(params);
  return useQuery({
    queryKey: ["v2", "futgg", "players", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/players", { params: clean, signal, ...REQUEST_OPTS })).data,
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

// GET /api/v2/players/{card_id} - full FUT.GG detail for one card.
export function useFutggPlayer(cardId) {
  return useQuery({
    queryKey: ["v2", "futgg", "player", cardId],
    queryFn: async ({ signal }) =>
      (await api.get(`/api/v2/players/${cardId}`, { signal, ...DETAIL_REQUEST_OPTS })).data,
    enabled: !!cardId,
    retry: false,
    staleTime: 30_000,
  });
}

// GET /api/v2/players/{card_id}/prices - BIN history for the chart.
export function useFutggPlayerPrices(cardId) {
  return useQuery({
    queryKey: ["v2", "futgg", "player", cardId, "prices"],
    queryFn: async ({ signal }) =>
      (await api.get(`/api/v2/players/${cardId}/prices`, { signal, ...REQUEST_OPTS })).data,
    enabled: !!cardId,
    retry: false,
    staleTime: 30_000,
  });
}

// GET /api/v2/players/{card_id}/sales - recent completed sales.
// approximate_sold_at is APPROXIMATE - never render it as an exact
// timestamp. But it IS the field to derive a displayed age from: age_text
// is the relative string FUT.GG showed at SCRAPE time and is frozen the
// moment it is stored, so rendering it verbatim makes a four-day-old sale
// claim it happened two minutes ago. See FutggMarketSection.jsx's header
// for the production case this rule was written from.
// `days` is part of the query key on purpose: the chart's range picker
// used to filter client-side over a single fixed response (always the
// newest 50 sales, i.e. a few hours on a liquid card), so every range drew
// the identical picture and the toggle looked dead. Each range is now its
// own server query over its own window.
export function useFutggPlayerSales(cardId, days = 14) {
  return useQuery({
    queryKey: ["v2", "futgg", "player", cardId, "sales", days],
    queryFn: async ({ signal }) =>
      (await api.get(`/api/v2/players/${cardId}/sales`, {
        signal, params: { days }, ...REQUEST_OPTS,
      })).data,
    enabled: !!cardId,
    retry: false,
    staleTime: 30_000,
  });
}

// GET /api/v2/opportunities - pre-filtered buy/strong_buy signal feed.
export function useFutggOpportunities(params = {}) {
  const clean = cleanParams(params);
  return useQuery({
    queryKey: ["v2", "futgg", "opportunities", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/opportunities", { params: clean, signal, ...SCAN_REQUEST_OPTS })).data,
    retry: false,
    staleTime: SCAN_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

// GET /api/v2/opportunities/preview - the ONLY opportunities call the
// public homepage (eafc.live, no account) is allowed to make. Returns at
// most one card with the actionable fields already stripped server-side
// (see futgg_market.py's _redact_for_public_preview) - never fetch
// useFutggOpportunities() for a signed-out visitor instead of this, that
// endpoint returns full buy/sell targets and profit to anyone.
export function useFutggOpportunityPreview() {
  return useQuery({
    queryKey: ["v2", "futgg", "opportunities", "preview"],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/opportunities/preview", { signal, ...SCAN_REQUEST_OPTS })).data,
    retry: false,
    staleTime: SCAN_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

// GET /api/v2/trade-finder - broader filter/sort set than /opportunities.
export function useFutggTradeFinder(params = {}) {
  const clean = cleanParams(params);
  return useQuery({
    queryKey: ["v2", "futgg", "trade-finder", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/trade-finder", { params: clean, signal, ...SCAN_REQUEST_OPTS })).data,
    retry: false,
    staleTime: SCAN_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

// GET /api/v2/trade-finder/strategy/{strategy} - the Strategy Finder
// tabs (Best overall/Quick flips/Low risk/Big profit/Budget picks). Each
// tab's filter thresholds are now defined and enforced server-side
// (app/services/strategy_definitions.py) instead of the frontend sending
// raw min_profit/min_confidence/etc query params - see that module's
// docstring for why (a cheap card could clear "Big profit" with no real
// coin floor when the frontend owned the params).
export function useStrategyFinder(strategy, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["v2", "futgg", "trade-finder", "strategy", strategy],
    queryFn: async ({ signal }) =>
      (await api.get(`/api/v2/trade-finder/strategy/${strategy}`, { signal, ...SCAN_REQUEST_OPTS })).data,
    enabled: enabled && !!strategy,
    retry: false,
    staleTime: SCAN_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

// GET /api/v2/market/freshness - overall FUT.GG pipeline health, used by
// MarketFreshness.jsx when no per-card price_age_seconds is available.
export function useFutggMarketFreshness() {
  return useQuery({
    queryKey: ["v2", "futgg", "market", "freshness"],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/market/freshness", { signal, ...REQUEST_OPTS })).data,
    retry: false,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}
