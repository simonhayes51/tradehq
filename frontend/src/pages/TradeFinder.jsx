// src/pages/TradeFinder.jsx
import React, { useEffect, useMemo, useState } from "react";
import { RefreshCcw, Info, PlusCircle, AlertTriangle } from "lucide-react";
import { fetchTradeFinder as fetchDeals, fetchDealInsight as explainDeal } from "../api/tradeFinder";
import { addWatch as addToWatchlist } from "../api/watchlist";
import { apiFetch } from "../api/http";

// Real liquidity/real-price/snipe metrics for many cards at once, from
// bin_history/sales_history - see /api/players/batch/market-metrics.
async function fetchMarketMetrics(cardIds) {
  const ids = [...new Set(cardIds.filter(Boolean))];
  if (!ids.length) return {};
  try {
    const res = await apiFetch("/api/players/batch/market-metrics", {
      query: { ids: ids.join(",") },
    });
    return res?.items || {};
  } catch (e) {
    console.error("Failed to fetch market metrics:", e);
    return {};
  }
}

// Debug logging - remove this after fixing
console.log('🔍 TradeFinder Environment Check:');
console.log('  VITE_API_URL:', import.meta.env.VITE_API_URL);
console.log('  MODE:', import.meta.env.MODE);
console.log('  All env vars:', import.meta.env);

const cls = (...xs) => xs.filter(Boolean).join(" ");

const NumberInput = ({ label, value, onChange, min, step = 100 }) => (
  <label className="flex flex-col gap-1 text-sm">
    <span className="text-gray-300">{label}</span>
    <input
      type="number"
      min={min}
      step={step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-3 py-2 text-gray-100 focus:outline-none focus:ring-2 focus:ring-lime-500"
    />
  </label>
);

const Chip = ({ children }) => (
  <span className="px-2 py-0.5 rounded-full text-xs bg-zinc-800 border border-zinc-700">{children}</span>
);

function MarketChips({ m }) {
  if (!m || !m.realPrice?.sampleSize24h) return null;
  const perHour = m.liquidity?.salesPerHour24h;
  const div = m.divergencePct24h;
  const snipe = m.snipeIndex24h;
  return (
    <>
      {perHour != null && (
        <Chip>
          {perHour}/hr liquidity
        </Chip>
      )}
      {div !== null && div !== undefined && (
        <Chip>{div > 0 ? "+" : ""}{div}% vs BIN</Chip>
      )}
      {snipe !== null && snipe !== undefined && snipe > 0 && (
        <Chip>Snipe {Math.round(snipe * 100)}%</Chip>
      )}
    </>
  );
}

function DealCard({ deal, metrics, onExplain, onQuickAdd }) {
  return (
    <div className="rounded-2xl bg-zinc-900/70 border border-zinc-800 p-4 flex gap-4 items-center shadow-sm">
      <img
        src={deal.image || deal.image_url || "/img/card-placeholder.png"}
        alt="card"
        className="w-14 h-20 object-cover rounded-lg"
      />
      <div className="flex-1">
        <div className="flex items-center gap-2">
          <div className="text-lg text-gray-100 font-semibold">
            {deal.name} {deal.rating ? <span className="text-gray-400">{deal.rating}</span> : null}
          </div>
          <div className="text-xs text-gray-400">{[deal.position, deal.league].filter(Boolean).join(" • ")}</div>
        </div>
        <div className="mt-1 grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
          <Tile label="Current" val={deal.current_price} />
          <Tile label="Target Sell" val={deal.expected_sell} />
          <Tile label="Profit (net)" val={deal.est_profit_after_tax} accent />
          <div className="bg-zinc-950/50 rounded-xl p-2 border border-zinc-800">
            <div className="text-gray-400">Margin</div>
            <div className="text-gray-100 font-medium">
              {deal.margin_pct != null ? `${Number(deal.margin_pct).toFixed(2)}%` : "—"}
            </div>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {deal.tags?.map((t) => (
            <Chip key={t}>{t}</Chip>
          ))}
          <MarketChips m={metrics?.[String(deal.card_id ?? deal.pid ?? deal.player_id)]} />
          {deal.vol_score != null && <Chip>Vol {Number(deal.vol_score).toFixed(3)}</Chip>}
          {deal.timeframe_hours ? (
            <Chip>
              {deal.timeframe_hours}h {deal.change_pct_window >= 0 ? "▲" : "▼"}{" "}
              {Number(deal.change_pct_window ?? 0).toFixed(1)}%
            </Chip>
          ) : null}
          {typeof deal.seasonal_shift === "number" && Math.abs(deal.seasonal_shift) >= 0.5 && (
            <Chip>Seasonal {deal.seasonal_shift > 0 ? "+" : ""}
              {Number(deal.seasonal_shift).toFixed(1)}%</Chip>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <button
          onClick={onExplain}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-sm"
        >
          <Info size={16} /> Why this deal?
        </button>
        <button
          onClick={onQuickAdd}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-lime-600 bg-lime-600/10 hover:bg-lime-600/20 text-sm text-lime-400"
        >
          <PlusCircle size={16} /> Quick Add
        </button>
      </div>
    </div>
  );
}

function Tile({ label, val, accent = false }) {
  return (
    <div className="bg-zinc-950/50 rounded-xl p-2 border border-zinc-800">
      <div className="text-gray-400">{label}</div>
      <div className={cls("font-medium", accent ? "text-lime-400" : "text-gray-100")}>
        {val != null ? `${Number(val).toLocaleString()}c` : "—"}
      </div>
    </div>
  );
}

export default function TradeFinder() {
  const [platform, setPlatform] = useState("console");
  const [timeframe, setTimeframe] = useState(24);
  const [budgetMax, setBudgetMax] = useState(150000);
  const [minProfit, setMinProfit] = useState(1500);
  const [minMargin, setMinMargin] = useState(8);
  const [ratingMin, setRatingMin] = useState(75);
  const [ratingMax, setRatingMax] = useState(93);
  const [leagues, setLeagues] = useState("");
  const [nations, setNations] = useState("");
  const [positions, setPositions] = useState("");

  const [loading, setLoading] = useState(false);
  const [deals, setDeals] = useState([]);
  const [metrics, setMetrics] = useState({});
  const [error, setError] = useState("");
  const [relaxed, setRelaxed] = useState(false);

  const params = useMemo(
    () => ({
      platform,
      timeframe,
      budget_max: budgetMax,
      min_profit: minProfit,
      min_margin_pct: minMargin,
      rating_min: ratingMin,
      rating_max: ratingMax,
      leagues,
      nations,
      positions,
    }),
    [platform, timeframe, budgetMax, minProfit, minMargin, ratingMin, ratingMax, leagues, nations, positions]
  );

  async function load() {
    setLoading(true);
    setError("");
    setRelaxed(false);
    
    console.log('🔍 TradeFinder loading with params:', params);
    
    try {
      // 1) strict
      let data = await fetchDeals(params);
      console.log('🔍 TradeFinder received data:', data);
      
      // 2) auto-relax once if empty
      if (!data.length) {
        const relaxedParams = {
          ...params,
          min_margin_pct: Math.max(0, Number(params.min_margin_pct || 0) - 5),
          min_profit: 0,
          budget_max: params.budget_max && params.budget_max < 2_000_000 ? 2_000_000 : params.budget_max,
        };
        console.log('🔍 Trying relaxed params:', relaxedParams);
        data = await fetchDeals(relaxedParams);
        if (data.length) setRelaxed(true);
      }
      setDeals(data);
      setMetrics(await fetchMarketMetrics(data.map((d) => d.card_id ?? d.pid ?? d.player_id)));
    } catch (e) {
      console.error('🚨 TradeFinder error:', e);
      setError(e?.message || "Failed to load deals");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // load on first mount + whenever filters change
    load(); // eslint-disable-next-line
  }, [JSON.stringify(params)]);

  const onExplain = async (deal) => {
    try {
      const r = await explainDeal(deal);
      alert(r?.explanation || "Candidate fits your filters.");
    } catch {
      alert("Could not fetch explanation.");
    }
  };

  const onQuickAdd = async (deal) => {
    try {
      await addToWatchlist({
        player_name: deal.name,
        card_id: deal.card_id ?? deal.pid ?? deal.player_id,
        version: deal.version,
        platform: platform.toUpperCase(),
        notes: `TradeFinder target: buy ~${Number(deal.current_price).toLocaleString()}c, sell ~${Number(
          deal.expected_sell
        ).toLocaleString()}c`,
      });
      alert(`${deal.name} added to watchlist.`);
    } catch {
      alert("Could not add to watchlist.");
    }
  };

  return (
    <div className="p-4 sm:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-gray-100">Trade Finder</h1>
        <button
          onClick={load}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-sm"
        >
          <RefreshCcw size={16} /> Refresh
        </button>
      </div>

      {/* Debug Info - Remove this after fixing */}
      <div className="bg-yellow-900/20 border border-yellow-600/30 rounded-lg p-3 text-sm">
        <strong>🔍 Debug Info:</strong><br />
        API Base: {import.meta.env.VITE_API_URL || 'FALLBACK'}<br />
        Should be: https://api.futhub.co.uk
      </div>

      <div className="grid lg:grid-cols-6 md:grid-cols-3 grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-300">Platform</span>
          <select
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-3 py-2 text-gray-100"
          >
            <option value="console">Console</option>
            <option value="pc">PC</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-300">Timeframe</span>
          <select
            value={timeframe}
            onChange={(e) => setTimeframe(Number(e.target.value))}
            className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-3 py-2 text-gray-100"
          >
            <option value={4}>4h</option>
            <option value={24}>24h</option>
          </select>
        </label>
        <NumberInput label="Budget Max" value={budgetMax} onChange={setBudgetMax} min={0} />
        <NumberInput label="Min Profit (net)" value={minProfit} onChange={setMinProfit} min={0} />
        <NumberInput label="Min Margin %" value={minMargin} onChange={setMinMargin} min={0} step={0.5} />
        <div className="flex gap-3">
          <NumberInput label="Rating Min" value={ratingMin} onChange={setRatingMin} min={40} step={1} />
          <NumberInput label="Rating Max" value={ratingMax} onChange={setRatingMax} min={40} step={1} />
        </div>
        <label className="flex flex-col gap-1 text-sm lg:col-span-2">
          <span className="text-gray-300">Leagues (comma separated)</span>
          <input
            value={leagues}
            onChange={(e) => setLeagues(e.target.value)}
            className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-3 py-2 text-gray-100"
            placeholder="Premier League, LaLiga"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm lg:col-span-2">
          <span className="text-gray-300">Nations</span>
          <input
            value={nations}
            onChange={(e) => setNations(e.target.value)}
            className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-3 py-2 text-gray-100"
            placeholder="England, Brazil"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm lg:col-span-2">
          <span className="text-gray-300">Positions</span>
          <input
            value={positions}
            onChange={(e) => setPositions(e.target.value)}
            className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-3 py-2 text-gray-100"
            placeholder="ST, CAM, CB"
          />
        </label>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-amber-400 text-sm">
          <AlertTriangle size={16} /> {error}
        </div>
      )}
      {relaxed && !error && (
        <div className="text-xs text-gray-400">
          No matches with strict filters — results shown with relaxed filters (lower margin, wider budget).
        </div>
      )}

      <div className="mt-2 grid gap-3">
        {loading && <div className="text-gray-400">Loading deals…</div>}
        {!loading && deals.length === 0 && (
          <div className="text-gray-400">
            No deals match your filters right now. Try dropping Min Profit / Margin or increasing Budget Max.
          </div>
        )}
        {deals.map((d) => (
          <DealCard
            key={`${d.card_id || d.pid || d.player_id}-${d.platform}-${d.timeframe_hours || "t"}`}
            deal={d}
            metrics={metrics}
            onExplain={() => onExplain(d)}
            onQuickAdd={() => onQuickAdd(d)}
          />
        ))}
      </div>
    </div>
  );
}
