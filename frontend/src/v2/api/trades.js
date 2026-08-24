const API_BASE = import.meta.env.VITE_API_URL || "";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}/api/v2/trades${path}`, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || body?.message || `Request failed (${response.status})`);
  }
  return response.json();
}

export const openTrade = (trade) => request("/open", { method: "POST", body: JSON.stringify(trade) });
export const closeTrade = (tradeId, sell) => request(`/${tradeId}/close`, { method: "PATCH", body: JSON.stringify({ sell }) });
export const getOpenTrades = () => request("/open");
export const getTradeHistory = (limit = 100) => request(`/history?limit=${limit}`);
export const getTradingPerformance = () => request("/performance");
export const getProfitTimeline = (days = 30) => request(`/profit-timeline?days=${days}`);
export const getCardCommunity = (cardId) => request(`/community/${cardId}`);

// Not under /api/v2/trades - this hits the shared v1 trades router
// (app/routers/trade_share.py) since the trades table it reads from is
// the same one both v1 and v2 write to.
export async function shareTradeCard(tradeId) {
  const response = await fetch(`${API_BASE}/api/trades/${tradeId}/share-card`, {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || body?.message || `Request failed (${response.status})`);
  }
  return response.json();
}

// period: "today" | "week" | "all"
export async function shareSummaryCard(period) {
  const response = await fetch(`${API_BASE}/api/trades/summary-share-card?period=${period}`, {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || body?.message || `Request failed (${response.status})`);
  }
  return response.json();
}
