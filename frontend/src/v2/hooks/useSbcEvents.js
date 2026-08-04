// src/v2/hooks/useSbcEvents.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useSbcEvents({ kind = "sbc", limit = 30, offset = 0 } = {}) {
  return useQuery({
    queryKey: ["v2", "sbc", "events", kind, limit, offset],
    queryFn: async () => (await api.get("/api/v2/sbc/events", { params: { kind, limit, offset } })).data,
    staleTime: 60_000,
  });
}
