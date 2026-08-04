// src/v2/hooks/useMarketRegime.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useMarketRegime() {
  return useQuery({
    queryKey: ["v2", "market", "regime"],
    queryFn: async () => (await api.get("/api/v2/market/regime")).data,
    staleTime: 60_000,
    retry: false,
  });
}
