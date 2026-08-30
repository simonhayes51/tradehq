// src/v2/hooks/useAdminFunnel.js
//
// The admin conversion report and the AI cost summary. Both are
// require_admin-gated server-side; these hooks do not check anything
// themselves, because a client-side guess about who is an admin is not a
// control and pretending otherwise invites someone to rely on it.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminFunnel({ window = "30d", start, end } = {}) {
  const params = start ? { start, ...(end ? { end } : {}) } : { window };
  return useQuery({
    queryKey: ["v2", "admin", "funnel", params],
    queryFn: async () => (await api.get("/api/admin/funnel", { params })).data,
    retry: false,
    staleTime: 60_000,
  });
}

export function useAdminAiUsage({ days = 30 } = {}) {
  return useQuery({
    queryKey: ["v2", "admin", "aiUsage", days],
    queryFn: async () => (await api.get("/api/admin/ai-usage", { params: { days } })).data,
    retry: false,
    staleTime: 60_000,
  });
}
