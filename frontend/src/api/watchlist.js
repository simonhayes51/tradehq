// src/api/watchlist.js
import api from "../axios";

// All functions return JSON payloads from the API host (VITE_API_URL)
export async function getWatchlist() {
  const { data } = await api.get("/api/watchlist");
  return data;
}

export async function addWatch(payload) {
  // payload: { player_name, card_id, version?, platform, notes? }
  const { data } = await api.post("/api/watchlist", payload);
  return data;
}

export async function deleteWatch(id) {
  const { data } = await api.delete(`/api/watchlist/${id}`);
  return data;
}

export async function refreshWatch(id) {
  const { data } = await api.post(`/api/watchlist/${id}/refresh`);
  return data;
}

// Price/liquidity threshold alerts - note the hyphenated path, a separate
// endpoint group from /api/watchlist above (both live in the backend's
// main.py, not app/routers/watchlist.py).
export async function getAlerts(config) {
  // v2's Watchlist page is public-first (unlike v1's, behind PrivateRoute),
  // so callers there need to pass __skipAuthRedirect - a 401 for a
  // logged-out visitor is expected and handled inline, not a reason for
  // axios.js's global interceptor to hard-redirect the whole page.
  const { data } = await api.get("/api/watchlist-alerts", config);
  return data;
}

export async function createAlert(payload) {
  // payload: { card_id, platform, metric: 'price'|'liquidity', rise_pct, fall_pct, cooloff_minutes }
  const { data } = await api.post("/api/watchlist-alerts", payload);
  return data;
}

export async function deleteAlert(id) {
  const { data } = await api.delete(`/api/watchlist-alerts/${id}`);
  return data;
}
