// src/lib/queryClient.js
// One shared React Query client. Pages that previously refetched the same
// data on every mount (player metadata, prices, entitlements) now share a
// cache with sensible staleness, cutting API load and making navigation
// feel instant.
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,        // most market data is refreshed server-side on a cadence anyway
      gcTime: 5 * 60_000,
      retry: 1,                 // src/axios.js already retries idempotent GETs
      refetchOnWindowFocus: false,
    },
  },
});
