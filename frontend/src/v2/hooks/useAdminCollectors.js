// src/v2/hooks/useAdminCollectors.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminCollectors() {
  return useQuery({
    queryKey: ["v2", "admin", "collectors"],
    queryFn: async () => (await api.get("/api/admin/collectors/status")).data,
    retry: false,
    staleTime: 30_000,
  });
}
