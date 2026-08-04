// src/v2/hooks/useRecommendationFeeds.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useOpportunities({ limit = 10 } = {}) {
  return useQuery({
    queryKey: ["v2", "recommendations", "opportunities", limit],
    queryFn: async () =>
      (await api.get("/api/v2/recommendations/opportunities", { params: { limit }, __skipAuthRedirect: true })).data,
    staleTime: 60_000,
  });
}

export function useHighConfidence({ limit = 10, minConfidence = 70 } = {}) {
  return useQuery({
    queryKey: ["v2", "recommendations", "highConfidence", limit, minConfidence],
    queryFn: async () =>
      (
        await api.get("/api/v2/recommendations/high-confidence", {
          params: { limit, min_confidence: minConfidence },
          __skipAuthRedirect: true,
        })
      ).data,
    staleTime: 60_000,
  });
}

export function useCardsToAvoid({ limit = 10 } = {}) {
  return useQuery({
    queryKey: ["v2", "recommendations", "avoid", limit],
    queryFn: async () =>
      (await api.get("/api/v2/recommendations/avoid", { params: { limit }, __skipAuthRedirect: true })).data,
    staleTime: 60_000,
  });
}

export function useStrategyRecommendations(strategy, { limit = 8 } = {}) {
  return useQuery({
    queryKey: ["v2", "recommendations", "strategy", strategy, limit],
    queryFn: async () => {
      const path = strategy === "best_picks"
        ? "/api/v2/recommendations/opportunities"
        : `/api/v2/recommendations/strategy/${strategy}`;
      return (await api.get(path, { params: { limit }, __skipAuthRedirect: true })).data;
    },
    enabled: Boolean(strategy),
    staleTime: 60_000,
  });
}

export function useGeneratedCardImages(cardIds = []) {
  const ids = [...new Set(cardIds.filter(Boolean).map(String))].sort();
  return useQuery({
    queryKey: ["v2", "recommendations", "generated-card-images", ids],
    queryFn: async () => (
      await api.get("/api/v2/recommendations/card-images", {
        params: { card_ids: ids.join(",") },
        __skipAuthRedirect: true,
      })
    ).data,
    enabled: ids.length > 0,
    staleTime: 5 * 60_000,
    // Cards still being generated (null/"generating") are re-checked every
    // few seconds so the dashboard picks up the finished PNG without a
    // reload. Cards that ended in "error" are left alone here -- the next
    // full refetch of this query (e.g. on remount) re-triggers generation
    // server-side anyway, no need to hammer the endpoint indefinitely.
    refetchInterval: (query) => {
      const statuses = query.state.data?.statuses || {};
      const pending = Object.values(statuses).some((s) => s == null || s === "generating");
      return pending ? 4000 : false;
    },
  });
}
