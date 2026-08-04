// src/v2/hooks/usePlayerSummary.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function usePlayerSummary(cardId) {
  return useQuery({
    queryKey: ["v2", "player", "summary", cardId],
    queryFn: async ({ signal }) => {
      const res = await api.get(`/api/v2/players/${cardId}/summary`, {
        signal,
        timeout: 6_000,
        // React Query owns this request's lifecycle. Retrying inside Axios
        // and then retrying the whole query again multiplied a slow backend
        // call into minutes of waiting.
        __noRetry: true,
      });
      return res.data;
    },
    enabled: !!cardId,
    staleTime: 5 * 60_000, // matches fair_value_mv's own refresh cadence
    retry: false,
  });
}
