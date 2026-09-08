// src/v2/hooks/useAdminPipelineHealth.js
//
// Admin-only - /api/v2/market/intelligence-health is protected by the same
// require_admin dependency as the rest of the Admin area. This replaces the
// legacy FUTBIN pipeline health endpoint, whose fair-value/analytics loops are
// intentionally disabled in the current FUT.GG deployment.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminPipelineHealth() {
  return useQuery({
    queryKey: ["v2", "admin", "pipelineHealth"],
    queryFn: async () => (await api.get("/api/v2/market/intelligence-health")).data,
    retry: false,
    staleTime: 30_000,
  });
}
