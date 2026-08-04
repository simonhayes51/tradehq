// src/v2/hooks/useAdminPipelineHealth.js
//
// Admin-only - no __skipAuthRedirect, matching useAdminSbcImports.js: a
// 401 here should bounce to /login, a 403 is handled inline by the caller.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminPipelineHealth() {
  return useQuery({
    queryKey: ["v2", "admin", "pipelineHealth"],
    queryFn: async () => (await api.get("/api/admin/pipeline/health")).data,
    retry: false,
    staleTime: 30_000,
  });
}
