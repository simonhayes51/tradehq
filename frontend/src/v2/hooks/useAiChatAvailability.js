// src/v2/hooks/useAiChatAvailability.js
//
// GET /api/v2/ai/status - whether this deployment has the assistant
// configured at all. The AI Chat nav entry is hidden when it isn't,
// rather than routing users to a page whose only possible reply was an
// apology naming an environment variable.
//
// Cached for the session (staleTime Infinity): the answer is a
// deployment property, not user or market data, and re-asking it on every
// navigation would be a request per page view for a value that cannot
// change while the tab is open.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useAiChatAvailability() {
  return useQuery({
    queryKey: ["v2", "ai", "status"],
    queryFn: async () =>
      (await api.get("/api/v2/ai/status", {
        __skipAuthRedirect: true,
        __noRetry: true,
        timeout: 6_000,
      })).data,
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}
