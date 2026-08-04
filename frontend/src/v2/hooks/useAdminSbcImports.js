// src/v2/hooks/useAdminSbcImports.js
//
// Admin-only page - unlike useWatchlist, a 401 here (not logged in at
// all) SHOULD bounce to /login via axios.js's default interceptor
// behavior, so no __skipAuthRedirect. A 403 (logged in but not admin)
// is left to the caller to handle inline.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAdminSbcImports() {
  return useQuery({
    queryKey: ["v2", "admin", "sbcImports"],
    queryFn: async () => (await api.get("/api/admin/sbc/imports")).data,
    retry: false,
    staleTime: 30_000,
  });
}
