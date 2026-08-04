// src/components/SalesLineChart.jsx
//
// Real sold-price trend built from actual completed sales
// (bin_sales_history_sync.py's sales_history table) - deliberately a plain
// line/area chart, not a candlestick chart. Most users of this app are
// 13-30 and have never read a candlestick before; a big price number, a
// plain up/down %, and a simple line is the same choice apps like
// Robinhood/Cash App make for non-trader audiences. No wicks, no open/
// close jargon, no third-party chart-library branding.
import React, { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_URL || "";

// Same green/red used elsewhere in this app (Watchlist's Change badge,
// PlayerSearch's existing Trend tile) - consistency over introducing a
// new palette.
const GREEN = "#22c55e";
const RED = "#ef4444";
const GRID = "#292d3e";
const AXIS = "#1b1e29";
const TICK = "#878c9c";

const abbreviate = (n) => {
  if (typeof n !== "number") return "";
  if (n >= 1_000_000) return `${Math.round(n / 100_000) / 10}M`.replace(".0M", "M");
  if (n >= 1_000) return `${Math.round(n / 100) / 10}K`.replace(".0K", "K");
  return n.toLocaleString();
};

const formatTickTime = (iso) => {
  const d = new Date(iso);
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
};

export default function SalesLineChart({ cardId, days = 1, height = 260, className = "" }) {
  const [points, setPoints] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!cardId) return;
    let aborted = false;

    (async () => {
      setLoading(true);
      setError("");
      try {
        const url = `${API_BASE}/api/players/${cardId}/sales-candles?bucket_hours=1&days=${days}`;
        const r = await fetch(url, { credentials: "include" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (aborted) return;
        const candles = Array.isArray(data?.candles) ? data.candles : [];
        // Real closing (last-sold) price per hour - a smoother, honest trend
        // line instead of every individual jagged sale.
        setPoints(candles.filter((c) => c.close != null).map((c) => ({ time: c.time * 1000, price: c.close })));
      } catch (e) {
        if (!aborted) {
          setError(e?.message || "Failed to load price trend");
          setPoints([]);
        }
      } finally {
        if (!aborted) setLoading(false);
      }
    })();

    return () => {
      aborted = true;
    };
  }, [cardId, days]);

  const { current, changePct } = useMemo(() => {
    if (points.length === 0) return { current: null, changePct: null };
    if (points.length === 1) return { current: points[0].price, changePct: null };
    const first = points[0].price;
    const last = points[points.length - 1].price;
    return { current: last, changePct: first ? ((last - first) / first) * 100 : null };
  }, [points]);

  const up = changePct == null ? true : changePct >= 0;
  const lineColor = up ? GREEN : RED;
  const chartData = points.map((p) => ({ x: new Date(p.time).toISOString(), y: p.price }));

  return (
    <div className={`w-full ${className}`}>
      <h3 className="font-semibold text-lg mb-1">What People Are Actually Paying</h3>
      <p className="text-xs text-white/60 mb-3">Based on real completed sales, not the asking price.</p>

      {current != null && (
        <div className="flex items-center gap-3 mb-3">
          <span className="text-3xl font-bold text-yellow-300">{Math.round(current).toLocaleString()}</span>
          {changePct != null && (
            <span
              className={`inline-flex items-center gap-1 text-sm font-semibold ${
                changePct > 0 ? "text-green-400" : changePct < 0 ? "text-red-400" : "text-gray-300"
              }`}
            >
              {changePct > 0 ? (
                <TrendingUp className="w-4 h-4" />
              ) : changePct < 0 ? (
                <TrendingDown className="w-4 h-4" />
              ) : (
                <Minus className="w-4 h-4" />
              )}
              {changePct > 0 ? "+" : ""}
              {changePct.toFixed(1)}%
            </span>
          )}
        </div>
      )}

      <div style={{ width: "100%", height }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ left: 55, right: 0, top: 0, bottom: 25 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="x"
              tickFormatter={formatTickTime}
              stroke={AXIS}
              tick={{ fill: TICK, fontSize: 12, fontWeight: 500 }}
              axisLine={{ stroke: AXIS }}
              tickLine={{ stroke: GRID }}
              interval="preserveStartEnd"
              minTickGap={40}
            />
            <YAxis
              dataKey="y"
              tickFormatter={abbreviate}
              stroke={AXIS}
              tick={{ fill: TICK, fontSize: 12, fontWeight: 500 }}
              axisLine={{ stroke: AXIS }}
              tickLine={{ stroke: GRID }}
              width={55}
              domain={["auto", "auto"]}
            />
            <Tooltip
              contentStyle={{
                background: "#0e1016",
                border: "1px solid #1b1e29",
                borderRadius: 8,
                boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
                padding: "8px 10px",
              }}
              labelFormatter={formatTickTime}
              formatter={(value) => [value?.toLocaleString?.() ?? value, "Coins"]}
            />
            <defs>
              <linearGradient id="salesLineFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={lineColor} stopOpacity={0.5} />
                <stop offset="95%" stopColor={lineColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="y"
              stroke={lineColor}
              fill="url(#salesLineFill)"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {loading && <div className="text-center text-sm text-slate-400 py-2">Loading…</div>}
      {!loading && error && <div className="text-center text-sm text-red-400 py-2">{error}</div>}
      {!loading && !error && points.length === 0 && (
        <div className="text-center text-sm text-slate-400 py-2">
          Not enough sales tracked yet to show a trend for this card.
        </div>
      )}
    </div>
  );
}
