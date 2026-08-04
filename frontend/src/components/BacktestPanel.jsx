// src/components/BacktestPanel.jsx
//
// Backtests a simple flip rule against real completed sales
// (sales_history) - "if I'd bought whenever this sold at or below X, and
// sold at the Nth sale after that, what would my P&L have been?" Only
// possible because sales_history retains real transaction history.
import React, { useState } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "";

export default function BacktestPanel({ cardId, className = "" }) {
  const [open, setOpen] = useState(false);
  const [buyBelow, setBuyBelow] = useState("");
  const [holdSales, setHoldSales] = useState(1);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const run = async (e) => {
    e.preventDefault();
    if (!cardId || !buyBelow) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const url = `${API_BASE}/api/players/${cardId}/backtest?buy_below=${encodeURIComponent(
        buyBelow
      )}&hold_sales=${holdSales}&days=${days}`;
      const r = await fetch(url, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setResult(data);
    } catch (err) {
      setError(err?.message || "Backtest failed");
    } finally {
      setLoading(false);
    }
  };

  const summary = result?.summary;

  return (
    <div className={`bg-white/5 border border-white/10 rounded-lg p-4 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between text-left"
      >
        <h3 className="font-semibold text-lg">Backtest a Flip Rule</h3>
        <span className="text-white/60 text-sm">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <div className="mt-3">
          <p className="text-xs text-white/60 mb-3">
            Simulates buying whenever this card sold at or below your price, then selling at the Nth
            sale after that - computed from real completed sales, net of EA's 5% tax.
          </p>

          <form onSubmit={run} className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            <label className="flex flex-col gap-1 text-xs text-white/70">
              Buy at or below
              <input
                type="number"
                min="1"
                required
                value={buyBelow}
                onChange={(e) => setBuyBelow(e.target.value)}
                placeholder="e.g. 180000"
                className="bg-black/40 border border-white/15 rounded-md px-2 py-1.5 text-white text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-white/70">
              Sell after N sales
              <input
                type="number"
                min="1"
                max="20"
                value={holdSales}
                onChange={(e) => setHoldSales(e.target.value)}
                className="bg-black/40 border border-white/15 rounded-md px-2 py-1.5 text-white text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-white/70">
              Lookback (days)
              <input
                type="number"
                min="1"
                max="30"
                value={days}
                onChange={(e) => setDays(e.target.value)}
                className="bg-black/40 border border-white/15 rounded-md px-2 py-1.5 text-white text-sm"
              />
            </label>
            <div className="flex items-end">
              <button
                type="submit"
                disabled={loading || !buyBelow}
                className="w-full px-3 py-1.5 rounded-md bg-lime-500/90 hover:bg-lime-500 text-black font-semibold text-sm"
              >
                {loading ? "Running…" : "Run"}
              </button>
            </div>
          </form>

          {error && <div className="text-sm text-red-400 mb-2">{error}</div>}

          {result && !summary && !error && (
            <div className="text-sm text-white/60">No qualifying trades in this window - try a higher buy price or a longer lookback.</div>
          )}

          {summary && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Stat label="Trades" value={summary.tradeCount} />
              <Stat label="Win Rate" value={`${summary.winRate}%`} accent={summary.winRate >= 50 ? "good" : "bad"} />
              <Stat
                label="Total P&L"
                value={summary.totalProfitCoins.toLocaleString()}
                accent={summary.totalProfitCoins >= 0 ? "good" : "bad"}
              />
              <Stat
                label="Avg P&L / trade"
                value={summary.avgProfitCoins.toLocaleString()}
                accent={summary.avgProfitCoins >= 0 ? "good" : "bad"}
              />
              <Stat label="Best Trade" value={`+${summary.bestTradeCoins.toLocaleString()}`} accent="good" />
              <Stat label="Worst Trade" value={summary.worstTradeCoins.toLocaleString()} accent="bad" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }) {
  const color = accent === "good" ? "text-green-300" : accent === "bad" ? "text-red-300" : "text-white";
  return (
    <div className="bg-white/5 rounded-lg p-3 border border-white/10">
      <div className="text-white/60 text-xs mb-1">{label}</div>
      <div className={`font-bold ${color}`}>{value}</div>
    </div>
  );
}
