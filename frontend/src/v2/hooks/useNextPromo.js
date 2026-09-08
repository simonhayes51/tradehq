// src/v2/hooks/useNextPromo.js
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useNextPromo() {
  return useQuery({
    queryKey: ["v2", "admin", "nextPromo"],
    queryFn: async () => (await api.get("/api/events/next")).data,
    retry: false,
    staleTime: 30_000,
  });
}

export function useUpcomingEvents() {
  return useQuery({
    queryKey: ["v2", "admin", "upcomingEvents"],
    queryFn: async () => (await api.get("/api/admin/upcoming-events")).data,
    retry: false,
    staleTime: 15_000,
  });
}

function invalidateCalendar(queryClient) {
  queryClient.invalidateQueries({ queryKey: ["v2", "admin", "nextPromo"] });
  queryClient.invalidateQueries({ queryKey: ["v2", "admin", "upcomingEvents"] });
  queryClient.invalidateQueries({ queryKey: ["v2", "dashboard"] });
}

export function useCreatePromo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => (await api.post("/api/admin/upcoming-events", payload)).data,
    onSuccess: () => invalidateCalendar(queryClient),
  });
}

export function useUpdateUpcomingEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }) => (await api.patch(`/api/admin/upcoming-events/${id}`, payload)).data,
    onSuccess: () => invalidateCalendar(queryClient),
  });
}

export function useDeleteUpcomingEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id) => (await api.delete(`/api/admin/upcoming-events/${id}`)).data,
    onSuccess: () => invalidateCalendar(queryClient),
  });
}
