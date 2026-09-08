// src/v2/hooks/useDashboard.js
//
// Single call backing the terminal-shell Home Dashboard - mirrors
// GET /api/v2/dashboard's own aggregation (one round trip instead of
// separately fetching market regime, recommendation feeds, movers,
// alerts and events). No __skipAuthRedirect needed: the endpoint never
// 401s for a logged-out visitor, it returns `locked.opportunityFeed`
// instead so the UI can render an upsell in place of the gated panels.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

function watchlistItems(data) {
  if (Array.isArray(data)) return data;
  return Array.isArray(data?.items) ? data.items : [];
}

export function normaliseDashboard(data, currentWatchlist) {
  if (!data || typeof data !== "object") return data;

  // Home says "Affecting your watchlist", so historical alerts for cards
  // the member has since removed do not belong here. `alerts_log` keeps
  // that history deliberately, and dashboard.watchedCount can be cached.
  // When the live watchlist request succeeded, its card ids are the source
  // of truth for this current-state Home section.
  if (currentWatchlist !== undefined) {
    const watched = watchlistItems(currentWatchlist);
    const watchedIds = new Set(watched.map((item) => String(item.card_id ?? item.cardId ?? "")));
    const alerts = (data.watchlistAlerts || []).filter((alert) => watchedIds.has(String(alert.cardId ?? alert.card_id ?? "")));
    return {
      ...data,
      watchedCount: watched.length,
      watchlistAlerts: alerts,
      unreadAlertCount: watched.length === 0 ? 0 : data.unreadAlertCount,
    };
  }

  // Safe fallback when the dedicated watchlist request itself failed.
  if (Number(data.watchedCount || 0) === 0) {
    return { ...data, watchlistAlerts: [], unreadAlertCount: 0 };
  }

  return data;
}

export function useDashboard() {
  return useQuery({
    queryKey: ["v2", "dashboard"],
    queryFn: async () => {
      const [dashboardResponse, currentWatchlist] = await Promise.all([
        api.get("/api/v2/dashboard"),
        api.get("/api/watchlist", {
          __skipAuthRedirect: true,
          __noRetry: true,
          timeout: 8_000,
        }).then((response) => response.data).catch(() => undefined),
      ]);
      return normaliseDashboard(dashboardResponse.data, currentWatchlist);
    },
    staleTime: 30_000,
  });
}
