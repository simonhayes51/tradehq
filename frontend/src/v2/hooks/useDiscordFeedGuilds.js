import { useQuery } from "@tanstack/react-query";
import api from "../../axios";

/**
 * Discord Server Feed is a separate guild-scoped product, not an Elite
 * entitlement. Any signed-in member who actually manages a configured
 * feed should be able to discover its web controls, regardless of their
 * personal Free/Elite tier.
 */
export function useDiscordFeedGuilds(enabled = true) {
  return useQuery({
    queryKey: ["v2", "discord-feed", "guilds"],
    queryFn: async () => {
      const { data } = await api.get("/api/v2/discord-feed/guilds", {
        __skipAuthRedirect: true,
        __noRetry: true,
        timeout: 8000,
      });
      return Array.isArray(data) ? data : Array.isArray(data?.guilds) ? data.guilds : [];
    },
    enabled,
    retry: false,
    staleTime: 5 * 60_000,
  });
}
