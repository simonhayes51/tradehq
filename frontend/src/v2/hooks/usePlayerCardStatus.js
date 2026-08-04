// src/v2/hooks/usePlayerCardStatus.js
//
// Polls a lightweight per-card endpoint until an on-demand generated card
// PNG finishes rendering (or errors), so a page that showed the fallback
// composition because the card was still generating can swap to the real
// PNG without a manual reload.
//
// Endpoint choice: `GET /api/players/{cardId}` - this is the same plain,
// DB-backed per-card lookup already used elsewhere in the app for cheap
// "just give me this card's row" reads (see fetchBioStats in
// PlayerSearch.jsx and the `/api/players/{id}` meta fetch in
// SmartBuyerAI.jsx), as opposed to `/api/v2/players/{id}/summary` which
// also computes/returns the full recommendation + market-metrics payload.
// Whichever backend player-data endpoint a given page already loads (search,
// player summary, etc.) will start returning generated_card_url/_status/
// _flagged for free once the backend PR lands - this hook is only needed
// where a page wants to keep re-checking status after the initial load
// without a full page refetch.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function usePlayerCardStatus({ cardId, initialStatus, enabled = true } = {}) {
  const shouldPoll = Boolean(enabled) && cardId != null;

  const query = useQuery({
    queryKey: ["player-card-status", cardId],
    queryFn: async () => {
      const res = await api.get(`/api/players/${cardId}`);
      const data = res.data || {};
      return {
        status: data.generated_card_status ?? null,
        url: data.generated_card_url ?? null,
        flagged: Boolean(data.generated_card_flagged),
      };
    },
    enabled: shouldPoll,
    initialData: shouldPoll
      ? { status: initialStatus ?? null, url: null, flagged: false }
      : undefined,
    staleTime: 0,
    gcTime: 60_000,
    retry: 1,
    refetchOnWindowFocus: false,
    // Keeps polling every ~3s while the card is still generating (or we
    // don't know yet - `null`/undefined just means "possibly just
    // triggered"), and stops automatically the moment the backend reports
    // ready/error.
    refetchInterval: (q) =>
      q.state.data?.status === "generating" || q.state.data?.status == null ? 3000 : false,
  });

  const data = query.data;
  return {
    status: data?.status ?? null,
    generatedCardUrl: data?.url ?? null,
    flagged: Boolean(data?.flagged),
    isPolling: shouldPoll && (data?.status === "generating" || data?.status == null),
  };
}
