// src/api/tradeFinder.js
import api from "../axios"; // adjust to "@/utils/axios" if that's where your client lives

// ---- helpers ----
const num = (v, d = undefined) => {
  if (v === "" || v == null) return d;
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
};

const csv = (s) =>
  (s ?? "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .join(",");

const normalisePlatform = (p = "console") => {
  const x = String(p).toLowerCase();
  if (x.startsWith("ps")) return "ps";
  if (x.startsWith("xb") || x.startsWith("xbox")) return "xbox";
  if (x === "console") return "console";
  return x;
};

// ---- API calls ----
export async function fetchTradeFinder(params = {}) {
  const tf = num(params.timeframe, 24);

  const q = {
    platform: normalisePlatform(params.platform),

    // send both keys in case backend expects one or the other
    timeframe: tf,
    timeframe_hours: tf,

    budget_max: num(params.budget_max),
    min_profit: num(params.min_profit),

    // margin aliases
    min_margin_pct: num(params.min_margin_pct ?? params.min_margin),
    min_margin: num(params.min_margin ?? params.min_margin_pct),

    rating_min: num(params.rating_min),
    rating_max: num(params.rating_max),

    leagues: csv(params.leagues),
    nations: csv(params.nations),
    positions: csv(params.positions),
  };

  // helpful debug URL
  try {
    console.log("🔗 TradeFinder GET:", api.getUri({ url: "/api/trade-finder", params: q }));
  } catch {}

  try {
    const { data } = await api.get("/api/trade-finder", { params: q });
    const deals = Array.isArray(data)
      ? data
      : data?.deals ?? data?.items ?? data?.results ?? [];
    return deals || [];
  } catch (err) {
    const status = err.response?.status;
    const serverMsg =
      err.response?.data?.detail ??
      err.response?.data?.message ??
      (typeof err.response?.data === "string" ? err.response.data : null);

    console.error("❌ TradeFinder failed:", {
      status,
      data: err.response?.data,
      url: err.config?.url,
      params: err.config?.params,
    });

    const msg = serverMsg ? `(${status}) ${serverMsg}` : err.message;
    throw new Error(`Trade Finder request failed${msg ? `: ${msg}` : ""}`);
  }
}

export async function fetchDealInsight(deal = {}) {
  const player_id = deal.player_id ?? deal.pid ?? deal.card_id ?? deal.id;
  const tf = num(deal.timeframe_hours ?? deal.timeframe, 24);

  const body = {
    player_id,
    platform: normalisePlatform(deal.platform),
    timeframe_hours: tf,
    current_price: num(deal.current_price),
    expected_sell: num(deal.expected_sell),
  };

  try {
    console.log("🔗 TradeFinder POST:", api.getUri({ url: "/api/trade-finder/explain" }));
  } catch {}

  const { data } = await api.post("/api/trade-finder/explain", body);
  return data;
}