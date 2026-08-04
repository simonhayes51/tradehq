// src/v2/hooks/useAdminApiUsage.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminApiUsage({ days = 14 } = {}) {
  return useQuery({
    queryKey: ["v2", "admin", "apiUsage", days],
    queryFn: async () => (await api.get("/api/admin/api-usage", { params: { days } })).data,
    retry: false,
    staleTime: 30_000,
  });
}
