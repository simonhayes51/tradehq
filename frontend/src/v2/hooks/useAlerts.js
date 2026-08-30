// src/v2/hooks/useAlerts.js
//
// The alert feed and its mutations. Everything about the previous
// behaviour that made alerts feel broken is addressed here or in the
// backend service this reads (app/services/alerts_feed.py):
//
//   * There was no dismiss call at all, so nothing could remove a fired
//     alert. There is now, and it is a real persisted state change
//     (alerts_log.dismissed_at), so a dismissed alert stays dismissed
//     across a refresh and a fresh sign-in.
//   * The dismiss is optimistic AND rolled back on failure, with the
//     failure surfaced as a toast rather than swallowed - an optimistic
//     update that silently keeps a failed removal is how "it disappeared
//     and then came back" happens.
//   * Every mutation invalidates the dashboard query too, because the
//     bell badge and Home's feed are rendered from /api/v2/dashboard -
//     invalidating only the alerts query left the badge stale until the
//     next dashboard refetch.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { dismissAlert, dismissAllAlerts, getAlerts, markAlertsRead } from "../api/alerts";

export const ALERTS_KEY = ["v2", "alerts"];
const DASHBOARD_KEY = ["v2", "dashboard"];

export function useAlerts({ enabled = true, limit = 20 } = {}) {
  return useQuery({
    queryKey: [...ALERTS_KEY, limit],
    queryFn: () => getAlerts(limit),
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

function useAlertMutation(mutationFn, { onOptimistic, errorMessage }) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    async onMutate(variables) {
      // Cancel in-flight refetches first: a response that started before
      // this mutation would otherwise land after the optimistic write and
      // put the removed alert straight back on screen.
      await queryClient.cancelQueries({ queryKey: ALERTS_KEY });
      const previous = queryClient.getQueriesData({ queryKey: ALERTS_KEY });
      queryClient.setQueriesData({ queryKey: ALERTS_KEY }, (current) =>
        current ? onOptimistic(current, variables) : current,
      );
      return { previous };
    },
    onError(_error, _variables, context) {
      // Put the alert back exactly as it was and say so. Silently keeping
      // the optimistic removal would show the user a success they didn't
      // get, and the alert would reappear on the next refetch with no
      // explanation.
      context?.previous?.forEach(([key, value]) => queryClient.setQueryData(key, value));
      toast.error(errorMessage);
    },
    onSettled() {
      queryClient.invalidateQueries({ queryKey: ALERTS_KEY });
      queryClient.invalidateQueries({ queryKey: DASHBOARD_KEY });
    },
  });
}

export function useDismissAlert() {
  return useAlertMutation(dismissAlert, {
    errorMessage: "Couldn't dismiss that alert - it's still here. Try again.",
    onOptimistic: (current, id) => ({
      ...current,
      items: current.items.filter((item) => String(item.id) !== String(id)),
      unreadCount: Math.max(
        0,
        current.unreadCount - (current.items.find((i) => String(i.id) === String(id))?.unread ? 1 : 0),
      ),
    }),
  });
}

export function useDismissAllAlerts() {
  return useAlertMutation(dismissAllAlerts, {
    errorMessage: "Couldn't clear your alerts. Try again.",
    onOptimistic: (current) => ({ ...current, items: [], unreadCount: 0 }),
  });
}

export function useMarkAlertsRead() {
  return useAlertMutation(markAlertsRead, {
    errorMessage: "Couldn't update your alerts.",
    onOptimistic: (current) => ({
      ...current,
      unreadCount: 0,
      items: current.items.map((item) => ({ ...item, unread: false })),
    }),
  });
}
