import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminDataQuality(hours = 24, pickupWindowMinutes = 30) {
  return useQuery({
    queryKey: ["v2", "admin", "dataQuality", hours, pickupWindowMinutes],
    queryFn: async () => {
      const [intelligence, salesShadow] = await Promise.all([
        api.get("/api/v2/market/intelligence-health"),
        api.get("/api/v2/market/sales-information-shadow", {
          params: { hours, pickup_window_minutes: pickupWindowMinutes },
        }),
      ]);
      return {
        intelligence: intelligence.data,
        salesShadow: salesShadow.data,
      };
    },
    retry: false,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
