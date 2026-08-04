// src/v2/hooks/useLiveCardLayers.js
//
// card_bg_image/card_cutout_image on fut_players are only ever populated
// by auto_sync's futbin_card_art_backfill.py, which has never actually
// been scheduled as a Cron Job (see that file's own README - it was
// deliberately left pending a live-network verification pass), so those
// columns are null for nearly every card today. GET
// /api/fut-player-definition/{card_id} already solves exactly this for
// v1's Player Search page by fetching the same bg/cutout layers live,
// per request, off the card's futbin player_url - safe to reuse here
// because this hook is only ever used for the ONE featured/selected card
// on the Home Dashboard, not for every card in a list (a live fetch per
// list row is exactly what the batch backfill worker exists to avoid).
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useLiveCardLayers(cardId) {
  return useQuery({
    queryKey: ["v2", "cardLayers", cardId],
    queryFn: async () => (await api.get(`/api/fut-player-definition/${cardId}`)).data?.data,
    enabled: cardId != null,
    staleTime: 5 * 60_000,
    retry: false,
  });
}
