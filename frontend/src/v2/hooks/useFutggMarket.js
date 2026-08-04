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
      (await api.get(`/api/v2/players/${cardId}`, { signal, ...REQUEST_OPTS })).data,
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
// timestamp; age_text is the primary display (see MarketFreshness.jsx /
// FutggMarketSection.jsx for the rendering rule this hook feeds).
export function useFutggPlayerSales(cardId) {
  return useQuery({
    queryKey: ["v2", "futgg", "player", cardId, "sales"],
    queryFn: async ({ signal }) =>
      (await api.get(`/api/v2/players/${cardId}/sales`, { signal, ...REQUEST_OPTS })).data,
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
      (await api.get("/api/v2/opportunities", { params: clean, signal, ...REQUEST_OPTS })).data,
    retry: false,
    staleTime: 30_000,
  });
}

// GET /api/v2/trade-finder - broader filter/sort set than /opportunities.
export function useFutggTradeFinder(params = {}) {
  const clean = cleanParams(params);
  return useQuery({
    queryKey: ["v2", "futgg", "trade-finder", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/trade-finder", { params: clean, signal, ...REQUEST_OPTS })).data,
    retry: false,
    staleTime: 30_000,
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
