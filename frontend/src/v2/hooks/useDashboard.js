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

export function useDashboard() {
  return useQuery({
    queryKey: ["v2", "dashboard"],
    queryFn: async () => (await api.get("/api/v2/dashboard")).data,
    staleTime: 30_000,
  });
}
