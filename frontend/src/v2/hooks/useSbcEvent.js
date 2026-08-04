// src/v2/hooks/useSbcEvent.js
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useSbcEvent(eventId) {
  return useQuery({
    queryKey: ["v2", "sbc", "event", eventId],
    queryFn: async () => (await api.get(`/api/v2/sbc/events/${eventId}`)).data,
    enabled: !!eventId,
    staleTime: 60_000,
  });
}
