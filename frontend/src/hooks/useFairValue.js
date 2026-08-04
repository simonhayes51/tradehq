// src/hooks/useFairValue.js
import { useQuery } from "@tanstack/react-query";
import {
  getFairValue,
  getUndervalued,
  getUndervaluedTeaser,
  getAnomalies,
} from "../api/fairValue";

export function useFairValue(cardId) {
  return useQuery({
    queryKey: ["fair-value", cardId],
    queryFn: () => getFairValue(cardId),
    enabled: !!cardId,
    staleTime: 120_000, // matview refreshes every ~5 min server-side
  });
}

export function useUndervalued(params = {}) {
  return useQuery({
    queryKey: ["undervalued", params],
    queryFn: () => getUndervalued(params),
    staleTime: 120_000,
  });
}

export function useUndervaluedTeaser() {
  return useQuery({
    queryKey: ["undervalued-teaser"],
    queryFn: () => getUndervaluedTeaser(),
    staleTime: 300_000,
  });
}

export function useAnomalies(params = {}) {
  return useQuery({
    queryKey: ["anomalies", params],
    queryFn: () => getAnomalies(params),
    staleTime: 60_000,
  });
}
