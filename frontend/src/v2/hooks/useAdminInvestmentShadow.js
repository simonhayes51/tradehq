import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminInvestmentShadow(windowHours = 168) {
  return useQuery({
    queryKey: ["v2", "admin", "investment-shadow", windowHours],
    queryFn: async () => (
      await api.get("/api/v2/investments/shadow-report", { params: { window_hours: windowHours } })
    ).data,
    retry: false,
    staleTime: 60_000,
  });
}
