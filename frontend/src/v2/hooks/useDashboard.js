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

export function normaliseDashboard(data) {
  if (!data || typeof data !== "object") return data;

  // alerts_log intentionally keeps notification history after a card is
  // removed from the watchlist. That is fine for the dedicated alerts
  // panel, but Home labels this slice "Affecting your watchlist". When the
  // server says watchedCount is zero, showing an old Pelé alert under that
  // heading is simply false current-state UI, so suppress it here without
  // deleting or changing the underlying alert history. The Home bell uses
  // the same current-watchlist context, so clear its stale unread count too.
  if (Number(data.watchedCount || 0) === 0) {
    return { ...data, watchlistAlerts: [], unreadAlertCount: 0 };
  }

  return data;
}

export function useDashboard() {
  return useQuery({
    queryKey: ["v2", "dashboard"],
    queryFn: async () => normaliseDashboard((await api.get("/api/v2/dashboard")).data),
    staleTime: 30_000,
  });
}
