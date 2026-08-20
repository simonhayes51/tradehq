// src/v2/hooks/useNextPromo.js
//
// The "Next Promo" calendar - a separate table/endpoint from
// /api/admin/market-events (see migration 018's own comment: that one is
// SBC-scoped and card-fingerprinted, this one is a narrow name/kind/
// start_at row on the core DB, e.g. "TOTW 35" or "Winter Wildcards").
// GET never fabricates a placeholder event - see main.py's
// /api/events/next - so a null `name` here means honestly nothing is
// scheduled yet, not a loading/error state.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useNextPromo() {
  return useQuery({
    queryKey: ["v2", "admin", "nextPromo"],
    queryFn: async () => (await api.get("/api/events/next")).data,
    retry: false,
    staleTime: 30_000,
  });
}

export function useCreatePromo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => (await api.post("/api/events", payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2", "admin", "nextPromo"] });
      queryClient.invalidateQueries({ queryKey: ["v2", "dashboard"] });
    },
  });
}
