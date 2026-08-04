// src/v2/hooks/useSalesCandles.js
//
// sales-history/sales-candles are PS/Xbox-market-only (per
// bin_sales_history_sync.py's own scope) and have no "platform" query
// param server-side - confirmed against app/routers/players.py directly,
// not assumed.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useSalesCandles(cardId, { days = 7, bucketHours = 4 } = {}, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["v2", "player", "salesCandles", cardId, days, bucketHours],
    queryFn: async () => {
      const res = await api.get(`/api/players/${cardId}/sales-candles`, {
        params: { days, bucket_hours: bucketHours },
      });
      return res.data;
    },
    enabled: enabled && !!cardId,
    staleTime: 60_000,
  });
}

export function useSalesHistory(cardId, { limit = 25 } = {}) {
  return useQuery({
    queryKey: ["v2", "player", "salesHistory", cardId, limit],
    queryFn: async () => {
      const res = await api.get(`/api/players/${cardId}/sales-history`, {
        params: { limit },
      });
      return res.data;
    },
    enabled: !!cardId,
    staleTime: 60_000,
  });
}
