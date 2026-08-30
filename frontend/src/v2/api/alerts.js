// src/v2/api/alerts.js
//
// Fired alerts (the notifications), not alert RULES. The rule endpoints
// live in src/api/watchlist.js and hit /api/watchlist-alerts - they were
// the only "delete an alert" call the app had, which is why deleting an
// alert never removed the thing the user was actually looking at.
import { api } from "../lib/api";

// The alert feed is one of several v2 surfaces that must not bounce a
// signed-out visitor to the login page or retry a 401 three times.
const REQUEST_OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 8_000 };

export async function getAlerts(limit = 20) {
  const { data } = await api.get("/api/v2/alerts", { params: { limit }, ...REQUEST_OPTS });
  return {
    items: Array.isArray(data?.items) ? data.items : [],
    unreadCount: Number(data?.unreadCount) || 0,
    watchedCount: Number(data?.watchedCount) || 0,
  };
}

export async function dismissAlert(id) {
  const { data } = await api.delete(`/api/v2/alerts/${id}`, REQUEST_OPTS);
  return data;
}

export async function dismissAllAlerts() {
  const { data } = await api.post("/api/v2/alerts/dismiss-all", null, REQUEST_OPTS);
  return data;
}

export async function markAlertsRead() {
  const { data } = await api.post("/api/v2/alerts/read", null, REQUEST_OPTS);
  return data;
}
