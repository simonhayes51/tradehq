import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminSbcReinforcement(lookbackHours = 72) {
  return useQuery({
    queryKey: ["v2", "admin", "sbc-reinforcement-shadow", lookbackHours],
    queryFn: async () => (
      await api.get("/api/v2/market/sbc-reinforcement-shadow", {
        params: { lookback_hours: lookbackHours, limit: 40 },
      })
    ).data,
    retry: false,
    staleTime: 60_000,
  });
}
