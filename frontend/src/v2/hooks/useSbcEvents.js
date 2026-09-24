// src/v2/hooks/useSbcEvents.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useSbcEvents({ limit = 60, offset = 0, q = "", category = "", status = "live", sort = "newest" } = {}) {
  const params = { limit, offset, status, sort };
  if (q) params.q = q;
  if (category) params.category = category;
  return useQuery({
    queryKey: ["v2", "sbc", "events", params],
    queryFn: async () => (await api.get("/api/v2/sbc/events", { params })).data,
    staleTime: 60_000,
  });
}

export function useSbcExposure(limit = 12) {
  return useQuery({
    queryKey: ["v2", "sbc", "solution-exposure", limit],
    queryFn: async () => (await api.get("/api/v2/sbc/solution-exposure", { params: { limit } })).data,
    staleTime: 60_000,
  });
}
