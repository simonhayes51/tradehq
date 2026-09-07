// src/v2/hooks/useTrackRecord.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// Track Record is aggregate/history data, not a live-price surface. It
// should be allowed more time than a cheap lookup under load, but it also
// should not be re-fired just because the browser window regains focus.
// The result is already cached for five minutes below; honour that cache
// instead of creating an avoidable extra database wave during navigation.
const REQUEST_OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 20_000 };

// window: "7d" | "30d" | "90d" | "all" - must match
// app/routers/v2/recommendations.py's _TRACK_RECORD_WINDOW_DAYS keys.
export function useTrackRecord(window = "90d") {
  return useQuery({
    queryKey: ["v2", "track-record", window],
    // Ungated on the backend (a trust signal only logged-in/paid users
    // could see wouldn't be doing its job) - __skipAuthRedirect matches
    // every other public-first v2 query regardless.
    queryFn: async () => (await api.get("/api/v2/track-record", {
      params: { window },
      ...REQUEST_OPTS,
    })).data,
    retry: false,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

// filter: "all" | "wins" | "losses" | "no_entry"
// enabled: pass false to defer the fetch entirely (e.g. an initially-
// collapsed "full ledger" accordion has no reason to fetch before the
// user opens it - avoids an unnecessary request on every page load).
export function useRecentOutcomes(window = "90d", filter = "all", limit = 20, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["v2", "track-record", "recent", window, filter, limit],
    queryFn: async () => (await api.get("/api/v2/track-record/recent", {
      params: { window, filter, limit },
      ...REQUEST_OPTS,
    })).data,
    enabled,
    retry: false,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}