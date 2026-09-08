import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminRoadmapReadiness() {
  return useQuery({
    queryKey: ["v2", "admin", "roadmap-readiness"],
    queryFn: async () => {
      const [intelligence, salesShadow, calibration, investments, sbc, openTrades] = await Promise.all([
        api.get("/api/v2/market/intelligence-health"),
        api.get("/api/v2/market/sales-information-shadow", {
          params: { hours: 168, pickup_window_minutes: 30 },
        }),
        api.get("/api/v2/market/calibration", {
          params: { horizon: "24h", window_days: 90 },
        }),
        api.get("/api/v2/investments/shadow-report", {
          params: { window_hours: 168 },
        }),
        api.get("/api/v2/market/sbc-reinforcement-shadow", {
          params: { lookback_hours: 168, limit: 40 },
        }),
        api.get("/api/v2/trades/open"),
      ]);

      return {
        intelligence: intelligence.data,
        salesShadow: salesShadow.data,
        calibration: calibration.data,
        investments: investments.data,
        sbc: sbc.data,
        openTrades: openTrades.data,
      };
    },
    retry: false,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
