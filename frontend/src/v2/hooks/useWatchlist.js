// src/v2/hooks/useWatchlist.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useWatchlist() {
  return useQuery({
    queryKey: ["v2", "watchlist", "list"],
    // __skipAuthRedirect: this dashboard is public-first (unlike v1's
    // pages, which sit behind PrivateRoute) - a 401 here is expected and
    // handled inline, not a reason for axios.js's global interceptor to
    // hard-redirect the whole page to /login.
    queryFn: async () => (await api.get("/api/watchlist", {
      __skipAuthRedirect: true,
      __noRetry: true,
      timeout: 8_000,
    })).data,
    retry: false, // 401 when logged out is expected, not worth retrying
    staleTime: 30_000,
  });
}
