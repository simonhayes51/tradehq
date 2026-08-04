// src/v2/hooks/useAdminSubscriptions.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminSubscriptions({ status, limit = 30 } = {}) {
  return useQuery({
    queryKey: ["v2", "admin", "subscriptions", status, limit],
    queryFn: async () =>
      (await api.get("/api/admin/subscriptions", { params: { status, limit } })).data,
    retry: false,
    staleTime: 30_000,
  });
}
