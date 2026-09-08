import { api } from "../lib/api";

// Keep the trade journal on the same HTTP path as the rest of v2. The
// shared axios client owns auth expiry, friendly errors and, critically,
// structured 402 handling. The old raw-fetch wrapper swallowed
// limit_reached into a plain Error, so hitting the Free open-trade cap
// looked like a broken save instead of the plan limit the backend had
// deliberately returned.
export const openTrade = async (trade) => (await api.post("/api/v2/trades/open", trade)).data;
export const closeTrade = async (tradeId, sell) => (await api.patch(`/api/v2/trades/${tradeId}/close`, { sell })).data;
export const getOpenTrades = async () => (await api.get("/api/v2/trades/open")).data;
export const getTradeHistory = async (limit = 100) => (await api.get("/api/v2/trades/history", { params: { limit } })).data;
export const getTradingPerformance = async () => (await api.get("/api/v2/trades/performance")).data;
export const getProfitTimeline = async (days = 30) => (await api.get("/api/v2/trades/profit-timeline", { params: { days } })).data;
export const getCardCommunity = async (cardId) => (await api.get(`/api/v2/trades/community/${cardId}`)).data;

// Not under /api/v2/trades - these hit the shared v1 trade-share router
// (app/routers/trade_share.py) since both app generations write the same
// trades table. They still use the shared client for consistent auth and
// error behaviour.
// format: "wide" (900x470, default) | "portrait" (1080x1350, for TikTok/IG)
export async function shareTradeCard(tradeId, format = "wide") {
  return (await api.post(`/api/trades/${tradeId}/share-card`, null, { params: { format } })).data;
}

// period: "today" | "week" | "all"; format: "wide" (default) | "portrait"
export async function shareSummaryCard(period, format = "wide") {
  return (await api.post("/api/trades/summary-share-card", null, { params: { period, format } })).data;
}
