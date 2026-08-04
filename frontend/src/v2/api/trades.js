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
