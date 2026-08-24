// src/v2/hooks/useTrackRecord.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

const REQUEST_OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 8_000 };

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
  });
}

// filter: "all" | "wins" | "losses" | "no_entry"
export function useRecentOutcomes(window = "90d", filter = "all", limit = 20) {
  return useQuery({
    queryKey: ["v2", "track-record", "recent", window, filter, limit],
    queryFn: async () => (await api.get("/api/v2/track-record/recent", {
      params: { window, filter, limit },
      ...REQUEST_OPTS,
    })).data,
    retry: false,
    staleTime: 5 * 60_000,
  });
}
