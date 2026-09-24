import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useGallerySets({ limit = 150, offset = 0, q = "", category = "", grade = "", sort = "tokens" } = {}) {
  const params = { limit, offset, sort };
  if (q) params.q = q;
  if (category) params.category = category;
  if (grade) params.grade = grade;
  return useQuery({
    queryKey: ["v2", "gallery", "sets", params],
    queryFn: async () => (await api.get("/api/v2/gallery/sets", { params })).data,
    staleTime: 5 * 60_000,
  });
}

export function useGallerySet(setId) {
  return useQuery({
    queryKey: ["v2", "gallery", "set", setId],
    queryFn: async () => (await api.get(`/api/v2/gallery/sets/${setId}`)).data,
    enabled: Boolean(setId),
    staleTime: 5 * 60_000,
  });
}

export function useGalleryExposure(limit = 12) {
  return useQuery({
    queryKey: ["v2", "gallery", "exposure", limit],
    queryFn: async () => (await api.get("/api/v2/gallery/exposure", { params: { limit } })).data,
    staleTime: 5 * 60_000,
  });
}
