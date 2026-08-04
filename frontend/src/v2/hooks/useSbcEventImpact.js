// src/v2/hooks/useSbcEventImpact.js
//
// __skipAuthRedirect: sbc_impact_predictions is gated as of Phase 4, and
// this page is public-first - a 401 for a logged-out visitor should
// render ImpactSection's PremiumGate, not hijack the page to /login via
// axios's global interceptor (same pattern as useWatchlist.js).
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useSbcEventImpact(eventId) {
  return useQuery({
    queryKey: ["v2", "sbc", "eventImpact", eventId],
    queryFn: async () => (await api.get(`/api/v2/sbc/events/${eventId}/impact`, { __skipAuthRedirect: true })).data,
    enabled: !!eventId,
    staleTime: 60_000,
  });
}
