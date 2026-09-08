import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminCalibration({ horizon = "24h", windowDays = 90 } = {}) {
  return useQuery({
    queryKey: ["v2", "admin", "calibration", horizon, windowDays],
    queryFn: async () => (
      await api.get("/api/v2/market/calibration", {
        params: { horizon, window_days: windowDays },
      })
    ).data,
    retry: false,
    staleTime: 60_000,
  });
}

export function useAdminCalibrationDecisions() {
  return useQuery({
    queryKey: ["v2", "admin", "calibration", "decisions"],
    queryFn: async () => (await api.get("/api/v2/market/calibration/decisions", { params: { limit: 50 } })).data,
    retry: false,
    staleTime: 60_000,
  });
}
