// src/v2/hooks/useAdminMarketEvents.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminMarketEvents({ kind, limit = 30 } = {}) {
  return useQuery({
    queryKey: ["v2", "admin", "marketEvents", kind, limit],
    queryFn: async () =>
      (await api.get("/api/admin/market-events", { params: { kind, limit } })).data,
    retry: false,
    staleTime: 30_000,
  });
}
