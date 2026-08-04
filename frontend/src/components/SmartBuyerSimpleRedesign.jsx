import React, { useMemo, useState } from "react";
import {
  ArrowDownCircle,
  ArrowUpCircle,
  PauseCircle,
  Sparkles,
  Info,
  ChevronDown,
  RefreshCw,
} from "lucide-react";

/**
 * Kid-friendly Smart Buyer panel (no external animation deps)
 * Props:
 *  - player, platform, timeframe
 *  - latestPrice, avgPrice, cheapZone [lo,hi], expensiveZone [lo,hi]
 *  - rsi, atr
 *  - onReload: () => void
 *  - onTradePlan: () => void   // <= NEW NAME (was onQuickBuy)
 *  - hideHeaderReload: boolean (default true)
 *  - children: chart node
 */
export default function SmartBuyerSimpleRedesign({
  player,
  platform = "ps",
  timeframe = "15m",
  latestPrice,
  avgPrice,
  cheapZone,
  expensiveZone,
  rsi = null,
  atr = null,
  onReload,
  onTradePlan,              // <— NEW
  children,
  hideHeaderReload = true,
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showTradePlan, setShowTradePlan] = useState(false);
  const [reloading, setReloading] = useState(false);

  const status = useMemo(() => {
    if (latestPrice == null)
      return { key: "hold", label: "No data yet", color: "bg-zinc-700", icon: PauseCircle };
    if (cheapZone && latestPrice <= Math.max(cheapZone[0], cheapZone[1])) {
      return { key: "buy", label: "Good time to buy!", color: "bg-emerald-600", icon: ArrowDownCircle };
    }
    if (expensiveZone && latestPrice >= Math.min(expensiveZone[0], expensiveZone[1])) {
      return { key: "sell", label: "Price is high — think about selling", color: "bg-rose-600", icon: ArrowUpCircle };
    }
    return { key: "hold", label: "Don’t buy yet — wait for a better deal", color: "bg-amber-500", icon: PauseCircle };
  }, [latestPrice, cheapZone, expensiveZone]);

  const StatusIcon = status.icon;

  const meter = useMemo(() => {
    const low = cheapZone ? Math.min(...cheapZone) : (avgPrice ?? 0);
    const high = expensiveZone ? Math.max(...expensiveZone) : (avgPrice ?? 1);
    let min = Math.min(low, avgPrice ?? low);
    let max = Math.max(high, avgPrice ?? high);
    if (min === max) max = min + 1;
    const value = latestPrice != null ? (latestPrice - min) / (max - min) : 0.5;
    return { min, max, value: Math.max(0, Math.min(1, value)) };
  }, [cheapZone, expensiveZone, avgPrice, latestPrice]);

  async function handleReload() {
    if (!onReload) return;
    setReloading(true);
    try {
      await onReload();
    } finally {
      setReloading(false);
    }
  }

  return (
    <div className="w-full mx-auto max-w-6xl px-4 pb-16">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        {player?.imageUrl && (
          <img
            src={player.imageUrl}
            alt={player?.name ?? "Player"}
            className="w-10 h-10 rounded-lg object-cover"
          />
        )}
        <div className="flex-1">
          <h1 className="text-xl md:text-2xl font-bold text-white leading-tight">
            {player?.name || "Select a player"}{" "}
            {player?.rating ? <span className="opacity-75">— {player.rating}</span> : null}
          </h1>
          <p className="text-sm text-zinc-300">
            {timeframe.toUpperCase()} · {platform.toUpperCase()}
          </p>
        </div>

        {!hideHeaderReload && (
          <button
            onClick={handleReload}
            disabled={!onReload || reloading}
            className="inline-flex items-center gap-2 rounded-2xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-900/30"
          >
            <RefreshCw className={`w-4 h-4 ${reloading ? "animate-spin" : ""}`} /> Reload
          </button>
        )}
      </div>

      {/* Status banner */}
      <div
        className={`rounded-3xl ${status.color} p-5 md:p-6 shadow-xl shadow-black/30 border border-white/10 transition-colors`}
      >
        <div className="flex items-start md:items-center gap-4">
          <div className="shrink-0">
            <StatusIcon className="w-8 h-8 text-white" />
          </div>
          <div className="flex-1">
            <p className="text-lg md:text-xl font-extrabold text-white tracking-tight flex items-center gap-2">
              <Sparkles className="w-5 h-5" /> {status.label}
            </p>
            <p className="text-sm md:text-base text-white/90 mt-1">
              {status.key === "buy" && "Tip: Set a max price and move fast — these deals go quickly!"}
              {status.key === "sell" && "Tip: Take profit while the price is hot. You can always buy back cheaper."}
              {status.key === "hold" && "Tip: Keep an eye on the chart — we’ll tell you when the price looks great."}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs uppercase text-white/70">Latest sale</p>
            <p className="text-2xl font-black text-white tabular-nums">{formatCoins(latestPrice)}</p>
          </div>
        </div>
      </div>

      {/* Cheap ↔ Expensive meter */}
      <div className="mt-6 rounded-3xl bg-white/5 border border-white/10 p-5">
        <div className="flex items-center justify-between text-xs uppercase tracking-wide text-zinc-300 mb-2">
          <span>Cheaper</span>
          <span>Expensive</span>
        </div>
        <div className="relative h-4 rounded-full bg-gradient-to-r from-emerald-500/40 via-yellow-400/40 to-rose-500/40 overflow-hidden">
          <div
            className="absolute top-0 bottom-0 w-1.5 rounded-full bg-white shadow-[0_0_0_3px_rgba(255,255,255,0.35)]"
            style={{ left: `calc(${(meter.value * 100).toFixed(2)}% - 3px)` }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-xs text-zinc-400">
          <span>Min: {formatCoins(meter.min)}</span>
          <span>Avg: {formatCoins(avgPrice)}</span>
          <span>Max: {formatCoins(meter.max)}</span>
        </div>
      </div>

      {/* Chart + tips */}
      <div className="mt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 rounded-3xl bg-white/5 border border-white/10 p-3 md:p-4">
          <div className="h-[360px] w-full rounded-2xl bg-black/30 overflow-hidden">
            {children || (
              <div className="flex h-full items-center justify-center text-zinc-400">
                <span className="text-sm">Chart goes here</span>
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-2 mt-3">
            <Badge>Average price</Badge>
            <Badge>Good price area</Badge>
            <Badge>Bad price area</Badge>
            <Badge>Player #{player?.cardId ?? "—"}</Badge>
          </div>
        </div>

        <div className="rounded-3xl bg-white/5 border border-white/10 p-5 flex flex-col gap-4">
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Info className="w-4 h-4" /> Quick Tips
          </h3>
          <ul className="text-sm text-zinc-300 list-disc pl-5 space-y-2">
            <li>Green area = good price. Red area = risky price.</li>
            <li>
              Use <span className="font-semibold text-white">Trade Plan</span> to get buy/list targets.
            </li>
            <li>Set coin limits so you don’t overspend.</li>
          </ul>

          <button
            onClick={() => {
              setShowTradePlan((v) => !v);
              onTradePlan && onTradePlan();
            }}
            title="Create buy/list targets — does not place any orders"
            aria-label="Open trade plan"
            disabled={!cheapZone && !expensiveZone && avgPrice == null}
            className="mt-2 inline-flex items-center justify-center rounded-2xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-emerald-900/30"
          >
            🧮 {showTradePlan ? "Hide Trade Plan" : "Trade Plan"}
          </button>
          <p className="text-xs text-white/60 -mt-2">
            Helper only — we never buy or sell for you.
          </p>

          {showTradePlan && (
            <div className="rounded-2xl bg-black/30 border border-emerald-500/30 p-4 space-y-2">
              <p className="text-sm font-semibold text-emerald-300">Suggested targets</p>
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-300">Buy at or below</span>
                <span className="font-semibold text-white tabular-nums">
                  {cheapZone ? formatCoins(Math.max(...cheapZone)) : "—"}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-300">List between</span>
                <span className="font-semibold text-white tabular-nums">
                  {formatRange(expensiveZone)}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-300">Reference average</span>
                <span className="font-semibold text-white tabular-nums">{formatCoins(avgPrice)}</span>
              </div>
              {latestPrice != null && avgPrice != null && (
                <p className="text-xs text-zinc-400 pt-1">
                  Currently {latestPrice <= avgPrice ? "below" : "above"} average by{" "}
                  {Math.abs(Math.round(((latestPrice - avgPrice) / avgPrice) * 100))}% — based on recent sale history,
                  not a live order book.
                </p>
              )}
            </div>
          )}

          {/* Advanced Stats (no animation lib) */}
          <div className="mt-1">
            <button
              onClick={() => setShowAdvanced((v) => !v)}
              className="w-full flex items-center justify-between rounded-xl bg-black/30 border border-white/10 px-4 py-2 text-sm text-white/90"
            >
              <span>Advanced Stats</span>
              <ChevronDown className={`w-4 h-4 transition-transform ${showAdvanced ? "rotate-180" : ""}`} />
            </button>
            <div
              className={`overflow-hidden transition-all duration-300 ${
                showAdvanced ? "max-h-96 opacity-100" : "max-h-0 opacity-0"
              }`}
            >
              <div className="px-4 py-3 grid grid-cols-2 gap-3 text-sm">
                <Stat label="RSI" value={rsi != null ? rsi.toFixed(1) : "—"} />
                <Stat label="ATR" value={atr != null ? atr.toFixed(2) : "—"} />
                <Stat label="Cheap Zone" value={formatRange(cheapZone)} />
                <Stat label="Expensive Zone" value={formatRange(expensiveZone)} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl bg-white/5 border border-white/10 p-3">
      <p className="text-xs uppercase tracking-wide text-zinc-400">{label}</p>
      <p className="mt-0.5 text-base font-semibold text-white tabular-nums">{value}</p>
    </div>
  );
}

function Badge({ children }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-white/10 border border-white/10 px-3 py-1 text-xs text-zinc-200">
      {children}
    </span>
  );
}

function formatCoins(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const x = Math.round(Number(n));
  return x.toLocaleString() + " c";
}

function formatRange(r) {
  if (!r || r[0] == null || r[1] == null) return "—";
  const [a, b] = r;
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  return `${formatCoins(lo)} – ${formatCoins(hi)}`;
}