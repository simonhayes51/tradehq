// src/v2/hooks/useOnboarding.js
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

const KEY = ["v2", "onboarding"];
const OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 8000 };

export function useOnboarding({ enabled = true } = {}) {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => (await api.get("/api/v2/onboarding", OPTS)).data,
    enabled,
    retry: false,
    staleTime: Infinity,
  });
}

export function useCompleteOnboarding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body) => (await api.post("/api/v2/onboarding", body, OPTS)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
}
