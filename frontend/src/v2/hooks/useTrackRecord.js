// src/v2/hooks/useTrackRecord.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useTrackRecord() {
  return useQuery({
    queryKey: ["v2", "track-record"],
    // Ungated on the backend (a trust signal only logged-in/paid users
    // could see wouldn't be doing its job) - __skipAuthRedirect matches
    // every other public-first v2 query regardless.
    queryFn: async () => (await api.get("/api/v2/track-record", {
      __skipAuthRedirect: true,
      __noRetry: true,
      timeout: 8_000,
    })).data,
    retry: false,
    staleTime: 5 * 60_000,
  });
}
