// src/pages/Dashboard.jsx - COMPLETE WITH WIDGET SIZING FIXES

import React, { useState, useMemo, useCallback, useEffect } from "react";
import { Link } from "react-router-dom";
import { useDashboard } from "../context/DashboardContext";
import { useSettings, ALL_WIDGET_KEYS } from "../context/SettingsContext";
import PremiumGate from "../components/PremiumGate";
import OnboardingBanner from "../components/OnboardingBanner";
import {
  LineChart, PencilLine, RotateCcw, Plus, X, CalendarClock, TrendingUp, TrendingDown,
  Bell, Settings as Cog, Target, Zap, Trophy, Activity, BarChart3, Timer, Download
} from "lucide-react";

const ACCENT = "#91db32";

/* ---------- choose which widgets are premium ---------- */
const PREMIUM_WIDGETS = new Set([
  "trending",
  "profit_trend",
  "performance",
  "promo",
  "quick_actions", // Chrome Extension card is premium
]);

/* tiny helper so usage stays clean */
const Gate = ({ name, children, fallback }) => (
  <PremiumGate feature={name} fallback={fallback}>
    {children}
  </PremiumGate>
);

/* ---------------- Layout persistence ---------------- */
const LAYOUT_STORAGE_KEY = "dashboard_layout_v2";
const WIDGET_SETTINGS_KEY = "dashboard_widgets_v2";

const saveLayoutToStorage = (layout) => {
  try { localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout)); } catch {}
};
const loadLayoutFromStorage = () => {
  try { const s = localStorage.getItem(LAYOUT_STORAGE_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
};
const saveWidgetSettingsToStorage = (settings) => {
  try { localStorage.setItem(WIDGET_SETTINGS_KEY, JSON.stringify(settings)); } catch {}
};
const loadWidgetSettingsFromStorage = () => {
  try { const s = localStorage.getItem(WIDGET_SETTINGS_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
};

/* ---------------- UI bits ---------------- */
const cardBase =
  "bg-gray-900/70 rounded-2xl p-4 border border-gray-800 hover:border-gray-700 transition-colors h-[150px] flex flex-col justify-between";
const cardTitle = "text-[13px] font-semibold text-gray-200/90 leading-none";
const cardBig = "text-[clamp(20px,1.8vw,26px)] font-extrabold leading-tight tracking-tight tabular-nums whitespace-nowrap";
const cardHuge = "text-[clamp(24px,2.4vw,36px)] font-extrabold leading-tight tracking-tight tabular-nums whitespace-nowrap";
const subText = "text-[12px] text-gray-400 leading-snug";
const chip = "inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-300";

const ALL_WIDGET_LABELS = {
  profit: "Net Profit",
  tax: "EA Tax Paid",
  trades: "Total Trades",
  profit_trend: "Profit Trend",
  winrate: "Win Rate",
  avg_profit: "Average Profit / Trade",
  best_trade: "Best Trade",
  volume: "Coin Volume",
  latest_trade: "Latest Trade",
  top_earner: "Top Earner",
  balance: "Starting Balance",
  roi: "ROI",
  promo: "Next Promo",
  trending: "Trending (6h)",
  alerts: "Watchlist Alerts",
  performance: "Performance Score",
  quick_actions: "Quick Actions",
  daily_target: "Daily Target",
  streak: "Current Streak",
  market_summary: "Market Summary",
};

export default function Dashboard() {
  const { netProfit, taxPaid, startingBalance, trades: rawTrades, isLoading, error } = useDashboard();
  const {
    formatCurrency, formatDate,
    visible_widgets, widget_order,
    include_tax_in_profit, alerts, saveSettings, toggleWidget,
    recent_trades_limit, isLoading: settingsLoading,
    daily_target,
  } = useSettings();

  const [tf, setTf] = useState("7D");
  const [editLayout, setEditLayout] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  // Load layout from localStorage on startup
  const [localWidgetOrder, setLocalWidgetOrder] = useState(() => {
    const saved = loadLayoutFromStorage();
    return saved?.widget_order || widget_order || [...ALL_WIDGET_KEYS];
  });

  const [localVisibleWidgets, setLocalVisibleWidgets] = useState(() => {
    const saved = loadWidgetSettingsFromStorage();
    return saved?.visible_widgets || visible_widgets || ALL_WIDGET_KEYS.slice(0, 8);
  });

  // Auto-save layout changes
  useEffect(() => { saveLayoutToStorage({ widget_order: localWidgetOrder }); }, [localWidgetOrder]);
  useEffect(() => { saveWidgetSettingsToStorage({ visible_widgets: localVisibleWidgets }); }, [localVisibleWidgets]);

  // Sync with context when it changes (but prioritize local state)
  useEffect(() => {
    if (widget_order && widget_order.length > 0) {
      const saved = loadLayoutFromStorage();
      if (!saved) setLocalWidgetOrder(widget_order);
    }
  }, [widget_order]);

  useEffect(() => {
    if (visible_widgets && visible_widgets.length > 0) {
      const saved = loadWidgetSettingsFromStorage();
      if (!saved) setLocalVisibleWidgets(visible_widgets);
    }
  }, [visible_widgets]);

  const trades = Array.isArray(rawTrades) ? rawTrades : [];
  const vis = Array.isArray(localVisibleWidgets) ? localVisibleWidgets : [];
  const order = Array.isArray(localWidgetOrder) ? localWidgetOrder : [];
  const previewLimit = Number.isFinite(recent_trades_limit) && recent_trades_limit > 0 ? recent_trades_limit : 5;

  const hiddenWidgets = ALL_WIDGET_KEYS.filter((k) => !vis.includes(k));

  const filterByTimeframe = useCallback((all, tfKey) => {
    if (tfKey === "ALL") return all;
    const days = tfKey === "7D" ? 7 : 30;
    const cutoff = Date.now() - days * 86400_000;
    return all.filter((t) => new Date(t?.timestamp || 0).getTime() >= cutoff);
  }, []);
  const filteredTrades = useMemo(() => filterByTimeframe(trades, tf), [trades, tf, filterByTimeframe]);

  const totals = useMemo(() => {
    const totalProfit = netProfit ?? 0;
    const totalTax = taxPaid ?? 0;
    const gross = totalProfit + totalTax;
    const taxPct = gross > 0 ? (totalTax / gross) * 100 : 0;

    const wins = filteredTrades.filter((t) => (t?.profit ?? 0) > 0).length;
    const losses = filteredTrades.filter((t) => (t?.profit ?? 0) < 0).length;
    const winRate = filteredTrades.length ? (wins / filteredTrades.length) * 100 : 0;
    const sumProfit = filteredTrades.reduce((s, t) => s + (t?.profit ?? 0), 0);
    const avgProfit = filteredTrades.length ? sumProfit / filteredTrades.length : 0;

    const best = filteredTrades.reduce((acc, t) => {
      const p = t?.profit ?? -Infinity;
      return p > acc.value ? { value: p, trade: t } : acc;
    }, { value: -Infinity, trade: null });

    const volume = filteredTrades.reduce((s, t) => {
      const qty = t?.quantity ?? 1;
      const buy = (t?.buy ?? 0) * qty;
      const sell = (t?.sell ?? 0) * qty;
      return { buy: s.buy + buy, sell: s.sell + sell, total: s.total + buy + sell };
    }, { buy: 0, sell: 0, total: 0 });

    const latest = [...filteredTrades].sort((a,b)=>new Date(b?.timestamp||0)-new Date(a?.timestamp||0))[0] || null;

    const byPlayer = new Map();
    for (const t of filteredTrades) {
      const adj = (t?.profit ?? 0) - (include_tax_in_profit ? (t?.ea_tax ?? 0) : 0);
      byPlayer.set(t?.player ?? "Unknown", (byPlayer.get(t?.player ?? "Unknown") ?? 0) + adj);
    }
    let topEarner = { player: null, total: 0 };
    for (const [player, total] of byPlayer.entries()) if (total > topEarner.total) topEarner = { player, total };

    return { totalProfit, totalTax, gross, taxPct, wins, losses, winRate, avgProfit, best, volume, latest, topEarner };
  }, [filteredTrades, netProfit, taxPaid, include_tax_in_profit, startingBalance]);

  const performanceScore = useMemo(() => {
    if (filteredTrades.length === 0) return 0;
    const winRateScore = Math.min(totals.winRate / 80 * 40, 40);
    const profitTrendScore = totals.totalProfit > 0 ? 30 : 0;
    const activityScore = Math.min(filteredTrades.length / 30 * 30, 30);
    return Math.round(winRateScore + profitTrendScore + activityScore);
  }, [totals, filteredTrades.length]);

  const todayStats = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const todayTrades = trades.filter(t => {
      const d = new Date(t?.timestamp || 0); d.setHours(0, 0, 0, 0);
      return d.getTime() === today.getTime();
    });
    const todayProfit = todayTrades.reduce((sum, t) => sum + (t?.profit ?? 0), 0);
    const target = Number(daily_target) || 50000;
    const progress = Math.min((todayProfit / target) * 100, 100);
    return { profit: todayProfit, target, progress, trades: todayTrades.length };
  }, [trades, daily_target]);

  const currentStreak = useMemo(() => {
    if (trades.length === 0) return { type: 'none', count: 0 };
    const sortedTrades = [...trades].sort((a, b) => new Date(b?.timestamp || 0) - new Date(a?.timestamp || 0));
    let streakCount = 0;
    let streakType = 'none';
    for (const trade of sortedTrades) {
      const profit = trade?.profit ?? 0;
      if (streakCount === 0) {
        streakType = profit > 0 ? 'win' : 'loss';
        streakCount = 1;
      } else {
        const isWin = profit > 0;
        if ((streakType === 'win' && isWin) || (streakType === 'loss' && !isWin)) streakCount++;
        else break;
      }
    }
    return { type: streakType, count: streakCount };
  }, [trades]);

  const spark = useMemo(() => {
    const dayKey = (d) => { const dt = new Date(d); return new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime(); };
    const map = new Map();
    for (const t of trades) {
      if (!t?.timestamp) continue;
      const key = dayKey(t.timestamp);
      const p = (t?.profit ?? 0) - (include_tax_in_profit ? (t?.ea_tax ?? 0) : 0);
      map.set(key, (map.get(key) ?? 0) + p);
    }
    const series = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date(); d.setDate(d.getDate() - i);
      series.push({ x: i, y: map.get(dayKey(d)) ?? 0 });
    }
    const w = 140, h = 42;
    const xs = series.map((_, i) => 1 + (i / (series.length - 1)) * (w - 2));
    const ysRaw = series.map((d) => d.y);
    const minY = Math.min(0, ...ysRaw), maxY = Math.max(1, ...ysRaw);
    const scaleY = (v) => (maxY === minY ? h / 2 : 1 + (h - 2) * (1 - (v - minY) / (maxY - minY)));
    let dAttr = `M ${xs[0]} ${scaleY(ysRaw[0])}`;
    for (let i = 1; i < xs.length; i++) dAttr += ` L ${xs[i]} ${scaleY(ysRaw[i])}`;
    return { dAttr, w, h, last: ysRaw[ysRaw.length - 1] };
  }, [trades, include_tax_in_profit]);

  const orderedKeys = useMemo(() => {
    const set = new Set(vis);
    const primary = order.filter((k) => set.has(k));
    for (const k of vis) if (!primary.includes(k)) primary.push(k);
    return primary;
  }, [vis, order]);

  const enhancedToggleWidget = useCallback((key, show) => {
    if (show) setLocalVisibleWidgets(prev => [...prev, key]);
    else setLocalVisibleWidgets(prev => prev.filter(k => k !== key));
    toggleWidget(key, show);
  }, [toggleWidget]);

  const onDragStart = useCallback((idx) => (e) => {
    if (!editLayout) return;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(idx));
    e.currentTarget.style.opacity = "0.5";
  }, [editLayout]);
  const onDragEnd = useCallback((e) => { e.currentTarget.style.opacity = "1"; }, []);
  const onDragOver = useCallback((e) => { if (editLayout) { e.preventDefault(); e.currentTarget.style.transform = "scale(1.02)"; } }, [editLayout]);
  const onDragLeave = useCallback((e) => { e.currentTarget.style.transform = "scale(1)"; }, []);
  const onDrop = useCallback((toIdx) => (e) => {
    if (!editLayout) return;
    e.preventDefault();
    e.currentTarget.style.transform = "scale(1)";
    const fromIdx = Number(e.dataTransfer.getData("text/plain"));
    if (!Number.isInteger(fromIdx) || fromIdx === toIdx) return;
    const next = [...orderedKeys];
    next.splice(toIdx, 0, next.splice(fromIdx, 1)[0]);
    const hidden = localWidgetOrder.filter((k) => !vis.includes(k));
    const newOrder = [...next, ...hidden];
    setLocalWidgetOrder(newOrder);
    saveSettings({ widget_order: newOrder });
  }, [editLayout, orderedKeys, localWidgetOrder, vis, saveSettings]);

  const resetLayout = useCallback(() => {
    const defaultOrder = [...ALL_WIDGET_KEYS];
    setLocalWidgetOrder(defaultOrder);
    saveSettings({ widget_order: defaultOrder });
  }, [saveSettings]);

  if (isLoading || settingsLoading) {
    return (
      <div className="p-4 max-w-6xl mx-auto">
        <div className="animate-pulse space-y-4">
          <div className="h-6 bg-gray-800 rounded w-40" />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {[...Array(8)].map((_, i) => <div key={i} className="bg-gray-900/70 rounded-2xl h-[150px] border border-gray-800" />)}
          </div>
        </div>
      </div>
    );
  }
  if (error) return <div className="text-red-500 p-4">{String(error)}</div>;

  /* -------------------- Widgets -------------------- */
  const PerformanceScoreCard = () => {
    const getScoreLabel = (score) => score >= 90 ? "Excellent" : score >= 75 ? "Good" : score >= 60 ? "Average" : score >= 40 ? "Below Average" : "Poor";
    const getScoreColor = (score) => score >= 90 ? "text-green-400" : score >= 75 ? "text-blue-400" : score >= 60 ? "text-yellow-400" : score >= 40 ? "text-orange-400" : "text-red-400";
    return (
      <div className={`${cardBase} bg-gradient-to-br from-purple-900/20 to-pink-900/20`}>
        <div className="flex items-center justify-between">
          <div className={cardTitle}>Performance Score</div>
          <span className={chip}><Trophy size={10} /> {tf}</span>
        </div>
        <div className="flex items-end gap-4">
          <div className={`${cardHuge} ${getScoreColor(performanceScore)}`}>{performanceScore}</div>
          <div className="pb-1">
            <div className="text-[10px] text-gray-400">/ 100</div>
            <div className={`text-[11px] ${getScoreColor(performanceScore)}`}>{getScoreLabel(performanceScore)}</div>
          </div>
        </div>
        <div className={subText}>Win rate: {totals.winRate.toFixed(1)}% • Trades: {filteredTrades.length}</div>
      </div>
    );
  };

  // === Chrome Extension (Premium) ===
  const QuickActionsCard = () => {
    const body = (
      <div className={`${cardBase} bg-gradient-to-br from-[#1A1130]/60 to-[#241142]/60`}>
        <div className="flex items-center justify-between mb-2">
          <div className={cardTitle}>Chrome Extension</div>
          <span className="text-[9px] bg-gray-800 text-gray-300 px-1.5 py-0.5 rounded-full">Premium</span>
        </div>

        <div className="flex items-center gap-3 flex-1">
          {/* Tiny Chrome logo (inline SVG) */}
          <svg viewBox="0 0 24 24" className="w-7 h-7 shrink-0" aria-hidden>
            <circle cx="12" cy="12" r="10" fill="#DB4437"/>
            <path d="M12 2a10 10 0 0 1 8.66 5H12a5 5 0 0 0-4.33 2.5L4.1 6.5A10 10 0 0 1 12 2z" fill="#FFCD40"/>
            <path d="M21.9 12a10 10 0 0 1-3.58 7.66L15.5 14A5 5 0 0 0 12 7h8.66c.22.96.34 1.97.24 3z" fill="#0F9D58"/>
            <circle cx="12" cy="12" r="4.2" fill="#fff"/>
            <circle cx="12" cy="12" r="2.6" fill="#4285F4"/>
          </svg>

          <div className="flex-1">
            <div className="text-[13px] text-gray-200/90 font-semibold">Chrome Extension</div>
            <div className="text-[11px] text-gray-400">One click trade logging</div>
          </div>

          <a
            href="https://chromewebstore.google.com/detail/hodeeeeoldpdhhjpnkpblajifahcdkep?utm_source=item-share-cb" /* swap to your real link */
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm
                       bg-white/10 hover:bg-white/20 text-white border border-white/10"
          >
            <Download size={14} />
            Download
          </a>
        </div>
      </div>
    );

    return <Gate name="quick_actions" fallback={body}>{body}</Gate>;
  };

  const DailyTargetCard = () => (
    <div className={`${cardBase} bg-gradient-to-br from-green-900/20 to-emerald-900/20`}>
      <div className="flex items-center justify-between">
        <div className={cardTitle}>Daily Target</div>
        <div className="flex items-center gap-2">
          <span className={chip}><Target size={10} /> today</span>
          <button
            onClick={() => {
              const v = prompt("Set daily target (coins):", String(todayStats.target));
              if (v !== null) saveSettings({ daily_target: Number(v) || 0 });
            }}
            className="text-[10px] underline text-gray-400 hover:text-gray-200"
          >
            edit
          </button>
        </div>
      </div>
      <div className="flex items-end gap-4">
        <div className={`${cardBig} ${todayStats.profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {formatCurrency(todayStats.profit)}
        </div>
        <div className="pb-1">
          <div className="text-[10px] text-gray-400">/ {formatCurrency(todayStats.target)}</div>
          <div className="text-[11px] text-green-400">{todayStats.progress.toFixed(0)}%</div>
        </div>
      </div>
      <div className="mt-2">
        <div className="w-full bg-gray-800 rounded-full h-1.5">
          <div className="bg-gradient-to-r from-green-500 to-emerald-400 h-1.5 rounded-full transition-all duration-500" style={{ width: `${Math.min(todayStats.progress, 100)}%` }} />
        </div>
        <div className={`${subText} mt-1`}>{todayStats.trades} trades today</div>
      </div>
    </div>
  );

  const CurrentStreakCard = () => {
    const getStreakEmoji = (type, count) =>
      type === 'none' ? "🎯" : type === 'win' ? (count >= 10 ? "🔥" : count >= 5 ? "⚡" : "✅") : (count >= 5 ? "💀" : "❌");
    const getStreakMessage = (type, count) =>
      type === 'none' ? "Start trading!" : type === 'win' ? (count >= 10 ? "On fire!" : count >= 5 ? "Great streak!" : "Keep it up!") : (count >= 5 ? "Time to bounce back" : "Next one's a winner");

    return (
      <div className={`${cardBase} bg-gradient-to-br from-orange-900/20 to-red-900/20`}>
        <div className="flex items-center justify-between">
          <div className={cardTitle}>Current Streak</div>
          <span className="text-2xl">{getStreakEmoji(currentStreak.type, currentStreak.count)}</span>
        </div>
        <div className="flex items-end gap-3">
          <div className={`${cardHuge} ${currentStreak.type === 'win' ? 'text-green-400' : currentStreak.type === 'loss' ? 'text-red-400' : 'text-gray-400'}`}>
            {currentStreak.count}
          </div>
          <div className="pb-1">
            <div className="text-[11px] text-gray-400 capitalize">{currentStreak.type}s</div>
          </div>
        </div>
        <div className={subText}>{getStreakMessage(currentStreak.type, currentStreak.count)}</div>
      </div>
    );
  };

  /* ---- Market Summary (FIXED VERSION) ---- */
  const MarketSummaryCard = () => {
    const API = import.meta.env.VITE_API_URL || "";
    const [tfLocal, setTfLocal] = useState("24");
    const [summary, setSummary] = useState({ trending: 0, falling: 0, stable: 0, sample: 0, tf: "24h" });
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState("");
    const [updated, setUpdated] = useState(null);

    const load = useCallback(async () => {
      setLoading(true); setErr("");
      try {
        const r = await fetch(`${API}/api/market/summary?tf=${tfLocal}`, { credentials: "include" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const js = await r.json();
        setSummary({
          trending: js?.trending ?? 0,
          falling: js?.falling ?? 0,
          stable: js?.stable ?? 0,
          sample: js?.sample ?? 0,
          tf: js?.tf ?? `${tfLocal}h`,
        });
        setUpdated(new Date());
      } catch (e) {
        setErr(e.message || "Failed to load");
        setSummary({ trending: 0, falling: 0, stable: 0, sample: 0, tf: `${tfLocal}h` });
      } finally {
        setLoading(false);
      }
    }, [API, tfLocal]);

    useEffect(() => { load(); }, [load]);

    const row = (label, value, cls) => (
      <div className="flex justify-between items-center">
        <span className={`text-[10px] ${cls}`}>{label}</span>
        <span className={`text-[11px] font-semibold ${cls}`}>{value}</span>
      </div>
    );

    const body = (
      <div className={`${cardBase} bg-gradient-to-br from-teal-900/20 to-blue-900/20`}>
        <div className="flex items-center justify-between mb-2">
          <div className={cardTitle}>Market Summary</div>
          <div className="flex items-center gap-0.5">
            {["6","12","24"].map(v => (
              <button
                key={v}
                onClick={() => setTfLocal(v)}
                className={`text-[9px] px-1.5 py-0.5 rounded ${tfLocal===v ? "bg-gray-800 text-gray-100" : "text-gray-400 hover:text-gray-200"}`}
                title={`${v}h`}
              >{v}h</button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="space-y-1.5 mt-1">
            {[...Array(3)].map((_,i)=><div key={i} className="h-3 bg-gray-800 rounded animate-pulse" />)}
          </div>
        ) : err ? (
          <div className="text-[10px] text-red-400 mt-1">Error loading</div>
        ) : (
          <div className="space-y-1.5 mt-1 flex-1">
            {row(<span className="flex items-center gap-1"><TrendingUp size={9}/> Up</span>, summary.trending, "text-green-400")}
            {row(<span className="flex items-center gap-1"><TrendingDown size={9}/> Down</span>, summary.falling, "text-red-400")}
            {row(<span className="flex items-center gap-1"><Timer size={9}/> Stable</span>, summary.stable, "text-gray-400")}
          </div>
        )}

        <div className="flex items-center justify-between mt-2 pt-1 border-t border-gray-800/50">
          <span className="text-[9px] bg-gray-800 text-gray-300 px-1.5 py-0.5 rounded-full">auto</span>
          {updated && <span className="text-[9px] text-gray-500">{updated.toLocaleTimeString()}</span>}
        </div>
      </div>
    );

    return <Gate name="market_summary" fallback={body}>{body}</Gate>;
  };

  const NextPromoCard = () => {
    const API = import.meta.env.VITE_API_URL || "";
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [countdown, setCountdown] = useState("");
    const fmtUK = (iso) => new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", weekday: "short", day: "2-digit", month: "short", timeZone: "Europe/London" }).format(new Date(iso));
    const tick = (iso) => {
      const t = new Date(iso).getTime() - Date.now();
      if (t <= 0) return "soon";
      const h = Math.floor(t / 3600000), m = Math.floor((t % 3600000)/60000), s = Math.floor((t % 60000)/1000);
      return `${h}h ${m}m ${s}s`;
    };
    useEffect(() => {
      (async () => {
        setLoading(true);
        try {
          const res = await fetch(`${API}/api/events/next`, { credentials: "include" });
          const js = await res.json();
          setData(js);
        } catch { setData(null); } finally { setLoading(false); }
      })();
    }, []); // eslint-disable-line
    useEffect(() => {
      if (!data?.start_at) return;
      setCountdown(tick(data.start_at));
      const id = setInterval(() => setCountdown(tick(data.start_at)), 1000);
      return () => clearInterval(id);
    }, [data?.start_at]);

    const body = (
      <div className={cardBase}>
        <div className="flex items-center justify-between">
          <div className={cardTitle}>Next Promo</div>
          <span className={chip}><CalendarClock size={10} /> {data?.confidence ?? "heuristic"}</span>
        </div>
        {loading ? (
          <>
            <div className="h-6 w-28 bg-gray-800 rounded animate-pulse" />
            <div className="h-4 w-40 bg-gray-800 rounded animate-pulse" />
          </>
        ) : data ? (
          <>
            <div className="text-[clamp(18px,1.5vw,22px)] font-extrabold" style={{ color: ACCENT }}>
              {data.name || "Daily Content Drop"}
            </div>
            <div className={subText}>
              Starts in {countdown} • {fmtUK(data.start_at)} UK
            </div>
          </>
        ) : (
          <div className={subText}>Couldn't load event.</div>
        )}
      </div>
    );

    return PREMIUM_WIDGETS.has("promo")
      ? <Gate name="promo" fallback={body}>{body}</Gate>
      : body;
  };

  const TrendingCard = () => {
    const API = import.meta.env.VITE_API_URL || "";
    const [type, setType] = useState("risers");
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
      (async () => {
        setLoading(true);
        try {
          const res = await fetch(`${API}/api/trending?type=${type}&tf=6`, { credentials: "include" });
          const js = await res.json();
          setItems(Array.isArray(js?.items) ? js.items.slice(0, 2) : []);
        } catch { setItems([]); } finally { setLoading(false); }
      })();
    }, [type]); // eslint-disable-line

    const body = (
      <div className={cardBase}>
        <div className="flex items-center justify-between">
          <div className={cardTitle}>Trending (6h)</div>
          <div className="flex items-center gap-2">
            <Gate
              name="smart_buy"
              fallback={
                <Link to="/trending" className="text-[10px] text-gray-400 hover:text-gray-200 underline">
                  See all →
                </Link>
              }
            >
              <Link to="/trending?tab=smart&tf=24" className="text-[10px] text-gray-400 hover:text-gray-200 underline">
                See all →
              </Link>
            </Gate>
            <div className="flex items-center gap-1">
              <button onClick={() => setType("risers")} className={`text-[10px] px-2 py-0.5 rounded ${type==="risers" ? "bg-gray-800 text-gray-100" : "text-gray-400 hover:text-gray-200"}`}><TrendingUp size={10}/>Risers</button>
              <button onClick={() => setType("fallers")} className={`text-[10px] px-2 py-0.5 rounded ${type==="fallers" ? "bg-gray-800 text-gray-100" : "text-gray-400 hover:text-gray-200"}`}><TrendingDown size={10}/>Fallers</button>
            </div>
          </div>
        </div>
        <div className="mt-1 space-y-1.5">
          {loading ? (
            [...Array(3)].map((_,i)=>(<div key={i} className="flex items-center gap-2">
              <div className="w-6 h-6 bg-gray-800 rounded" />
              <div className="h-3 w-36 bg-gray-800 rounded" />
              <div className="h-3 w-10 bg-gray-800 rounded ml-auto" />
            </div>))
          ) : items.length === 0 ? (
            <div className={subText}>No data.</div>
          ) : (
            items.map((it, idx) => {
              const pct = Number(it.percent ?? 0);
              const up = pct >= 0;
              return (
                <div key={`${it.pid}-${idx}`} className="flex items-center gap-2">
                  {it.image ? <img src={it.image} alt={it.name} className="w-6 h-6 rounded object-cover" /> : <div className="w-6 h-6 rounded bg-gray-800" />}
                  <div className="truncate">
                    <div className="text-[12px] text-gray-200 truncate">{it.name ?? `Card ${it.pid}`} {it.rating ? <span className="text-gray-400">({it.rating})</span> : null}</div>
                    <div className="text-[10px] text-gray-500 truncate">{it.version ?? it.league ?? ""}</div>
                  </div>
                  <div className={`ml-auto text-[12px] font-semibold ${up ? "text-green-400" : "text-red-400"}`}>{pct>0?"+":""}{pct.toFixed(2)}%</div>
                </div>
              );
            })
          )}
        </div>
      </div>
    );

    return PREMIUM_WIDGETS.has("trending")
      ? <Gate name="trending" fallback={body}>{body}</Gate>
      : body;
  };

  const AlertsCard = () => {
    const API = import.meta.env.VITE_API_URL || "";
    const [counts, setCounts] = useState({ watch: 0, alerts: 0 });
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    const load = async () => {
      setLoading(true);
      try {
        const res = await fetch(`${API}/api/watchlist`, { credentials: "include" });
        const js = await res.json();
        const items = Array.isArray(js?.items) ? js.items : [];
        const watch = items.length;
        const threshold = Number(alerts.thresholdPct) || 0;
        const active = alerts.enabled
          ? items.filter((it) => typeof it.change_pct === "number" && Math.abs(it.change_pct) >= threshold).length
          : 0;
        setCounts({ watch, alerts: active });
      } catch { setCounts({ watch: 0, alerts: 0 }); } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, [alerts.enabled, alerts.thresholdPct]); // eslint-disable-line

    return (
      <div className={cardBase}>
        <div className="flex items-center justify-between">
          <div className={cardTitle}>Watchlist Alerts</div>
          <button onClick={() => setOpen((v) => !v)} className={chip} title="Settings">
            <Cog size={10} /> {alerts.delivery === "discord" ? "Discord" : "In-app"}
          </button>
        </div>

        {!alerts.enabled ? (
          <>
            <div className="text-[13px] text-gray-300">Alerts are <span className="font-semibold">disabled</span>.</div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => saveSettings({ alerts: { enabled: true } })}
                className="text-xs px-2 py-1 rounded-lg bg-gray-900 border border-gray-800 hover:border-gray-700"
              >
                Enable
              </button>
              <Link to="/settings?section=alerts" className="text-xs underline text-gray-300 hover:text-white inline-flex items-center gap-1">
                Open Settings
              </Link>
            </div>
          </>
        ) : (
          <>
            <div className="flex items-end gap-6">
              <div>
                <div className="text-[11px] text-gray-400">Watchlist items</div>
                <div className={`${cardBig} text-gray-200`}>{loading ? "…" : counts.watch}</div>
              </div>
              <div>
                <div className="text-[11px] text-gray-400">Active alerts</div>
                <div className={`${cardBig} ${counts.alerts ? "text-green-400" : "text-gray-400"}`}>{loading ? "…" : counts.alerts}</div>
              </div>
            </div>
            <div className={subText}>Threshold: {alerts.thresholdPct}% • Cooldown: {alerts.cooldownMin}m</div>
          </>
        )}

        {open && (
          <div className="absolute z-10 mt-2 right-4 bottom-4 bg-gray-950 border border-gray-800 rounded-xl p-3 w-72">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[12px] font-semibold">Alert Settings</div>
              <button className="text-gray-400 hover:text-gray-200" onClick={() => setOpen(false)}><X size={14}/></button>
            </div>
            <label className="flex items-center gap-2 text-[12px] mb-2">
              <input type="checkbox" checked={alerts.enabled} onChange={(e)=>saveSettings({ alerts:{ enabled: e.target.checked }})}/>
              Enable alerts
            </label>
            <div className="grid grid-cols-2 gap-2 text-[12px]">
              <label className="flex flex-col">
                <span className="text-gray-400">Threshold %</span>
                <input type="number" min={1} max={50} step={0.5} className="bg-gray-900 border border-gray-800 rounded px-2 py-1"
                  value={alerts.thresholdPct}
                  onChange={(e)=>saveSettings({ alerts:{ thresholdPct: Math.max(1, Math.min(50, Number(e.target.value)||0)) }})}/>
              </label>
              <label className="flex flex-col">
                <span className="text-gray-400">Cooldown (m)</span>
                <input type="number" min={5} max={180} className="bg-gray-900 border border-gray-800 rounded px-2 py-1"
                  value={alerts.cooldownMin}
                  onChange={(e)=>saveSettings({ alerts:{ cooldownMin: Math.max(5, Math.min(180, Number(e.target.value)||0)) }})}/>
              </label>
              <label className="flex flex-col col-span-2">
                <span className="text-gray-400">Delivery</span>
                <select className="bg-gray-900 border border-gray-800 rounded px-2 py-1"
                  value={alerts.delivery}
                  onChange={(e)=>saveSettings({ alerts:{ delivery: e.target.value }})}>
                  <option value="inapp">In-app</option>
                  <option value="discord">Discord DM</option>
                </select>
              </label>
              <Link to="/settings?section=alerts" className="text-[12px] underline text-gray-300 hover:text-white mt-1">
                Open full settings →
              </Link>
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderWidget = (key) => {
    // Build the widget first
    let node = null;
    switch (key) {
      case "performance":   node = <PerformanceScoreCard />; break;
      case "quick_actions": node = <QuickActionsCard />; break;
      case "daily_target":  node = <DailyTargetCard />; break;
      case "streak":        node = <CurrentStreakCard />; break;
      case "market_summary": node = <MarketSummaryCard />; break;
      case "promo":         node = <NextPromoCard />; break;
      case "trending":      node = <TrendingCard />; break;
      case "alerts":        node = <AlertsCard />; break;

      case "profit": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Net Profit</div>
          <div className="text-green-400"><span className={cardBig}>{formatCurrency(totals.totalProfit)} coins</span></div>
          <div className={subText}></div>
        </div>
      ); break;

      case "tax": node = (
        <div className={cardBase}>
          <div className={cardTitle}>EA Tax Paid</div>
          <div className={`${cardBig} text-red-400`}>{formatCurrency(totals.totalTax)} coins</div>
          <div className={subText}>{totals.totalTax > 0 ? `${totals.taxPct.toFixed(1)}% of gross` : "No tax yet"}</div>
        </div>
      ); break;

      case "balance": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Starting Balance</div>
          <div className={`${cardBig} text-blue-400`}>{formatCurrency(startingBalance ?? 0)} coins</div>
          {(startingBalance ?? 0) > 0 && totals.totalProfit > 0 && <div className={subText}>ROI: {(((totals.totalProfit)/(startingBalance||1))*100).toFixed(1)}%</div>}
        </div>
      ); break;

      case "trades": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Total Trades ({tf})</div>
          <div className={`${cardBig} text-purple-400`}>{filteredTrades.length}</div>
          {filteredTrades.length > 0 && <div className={subText}>Avg profit: {formatCurrency(totals.avgProfit)} coins</div>}
        </div>
      ); break;

      case "roi": node = (
        <div className={cardBase}>
          <div className={cardTitle}>ROI</div>
          <div className={cardBig}>{(startingBalance ?? 0) > 0 ? (((totals.totalProfit)/(startingBalance||1))*100).toFixed(1) : 0}%</div>
          <div className={subText}>cumulative</div>
        </div>
      ); break;

      case "winrate": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Win Rate ({tf})</div>
          <div className={cardBig}>{filteredTrades.length ? totals.winRate.toFixed(1) : 0}%</div>
          <div className={subText}>{totals.wins} wins • {totals.losses} losses</div>
        </div>
      ); break;

      case "best_trade": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Best Trade ({tf})</div>
          <div className="text-green-400">
            <span className={cardBig}>{formatCurrency(totals.best.value === -Infinity ? 0 : totals.best.value)} coins</span>
          </div>
          <div className={subText}>{totals.best.trade ? `${totals.best.trade.player ?? "Unknown"} (${totals.best.trade.version ?? "—"})` : "—"}</div>
        </div>
      ); break;

      case "avg_profit": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Average Profit per Trade ({tf})</div>
          <div className="text-green-400"><span className={cardBig}>{formatCurrency(totals.avgProfit)} coins</span></div>
          <div className={subText}></div>
        </div>
      ); break;

      case "volume": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Coin Volume ({tf})</div>
          <div className={`${cardBig} text-gray-200`}>{formatCurrency(totals.volume.total)}</div>
          <div className={subText}>Buys: {formatCurrency(totals.volume.buy)} • Sells: {formatCurrency(totals.volume.sell)}</div>
        </div>
      ); break;

      case "profit_trend": {
        const body = (
          <div className={cardBase}>
            <div className="flex items-center justify-between">
              <div className={cardTitle}>Profit Trend (7D)</div>
              <span className={chip}><LineChart size={10} /> sparkline</span>
            </div>
            <div className="mt-1">
              <svg width={spark.w} height={spark.h}>
                <defs>
                  <linearGradient id="sparkGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor={ACCENT} stopOpacity="0.8" />
                    <stop offset="100%" stopColor={ACCENT} stopOpacity="1" />
                  </linearGradient>
                </defs>
                <path d={spark.dAttr} stroke="url(#sparkGradient)" fill="none" strokeWidth="2" />
              </svg>
              <div className={subText}>Last day: <span className={spark.last>=0?"text-green-400 font-medium":"text-red-400 font-medium"}>{formatCurrency(spark.last ?? 0)}</span></div>
            </div>
          </div>
        );
        node = PREMIUM_WIDGETS.has("profit_trend") ? <Gate name="profit_trend" fallback={body}>{body}</Gate> : body;
        break;
      }

      case "latest_trade": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Latest Trade</div>
          {totals.latest ? (
            <div className="mt-0.5">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-semibold">{totals.latest.player ?? "Unknown"}</span>
                <span className="text-[11px] text-gray-400">({totals.latest.version ?? "—"})</span>
                {totals.latest.tag && <span className="text-xs bg-gray-800 border border-gray-700 px-2 py-1 rounded-full">{totals.latest.tag}</span>}
              </div>
              <div className={`${subText} mt-0.5`}>
                {formatCurrency(totals.latest.buy ?? 0)} → {formatCurrency(totals.latest.sell ?? 0)}
                {totals.latest.quantity > 1 && ` (${totals.latest.quantity}x)`} • {totals.latest.platform ?? "Console"}
              </div>
              {(() => {
                const base = totals.latest?.profit ?? 0;
                const tax = totals.latest?.ea_tax ?? 0;
                const display = include_tax_in_profit ? base - tax : base;
                const pos = display >= 0;
                return <div className={`mt-0.5 ${cardBig} ${pos ? "text-green-400" : "text-red-400"}`}>{pos ? "+" : ""}{formatCurrency(display)} coins</div>;
              })()}
            </div>
          ) : <div className={`${subText} mt-1`}>No trades yet</div>}
        </div>
      ); break;

      case "top_earner": node = (
        <div className={cardBase}>
          <div className={cardTitle}>Top Earner ({tf})</div>
          <div className={`${cardBig} text-green-400`}>{formatCurrency(Math.max(0, totals.topEarner.total))} coins</div>
          <div className={subText}>{totals.topEarner.player ? totals.topEarner.player : "—"}</div>
        </div>
      ); break;

      default: node = null;
    }

    // finally: if the widget key is in PREMIUM_WIDGETS, gate the whole thing
    if (PREMIUM_WIDGETS.has(key) && node) {
      return <Gate name={key} fallback={node}>{node}</Gate>;
    }
    return node;
  };

  /* -------------------- Render -------------------- */
  return (
    <div className="p-4 max-w-6xl mx-auto">
      <OnboardingBanner />
      {/* Header */}
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mb-4">
        <h1 className="text-xl font-bold">Your Grind, Live 📈</h1>
        <div className="flex items-center gap-2">
          <div className="text-xs text-gray-400">Last updated: {formatDate(new Date())}</div>
          <div className="w-px h-4 bg-gray-700" />
          <div className="bg-gray-900/70 border border-gray-800 rounded-xl p-1 flex">
            {["7D", "30D", "ALL"].map((k) => (
              <button key={k} onClick={() => setTf(k)} className={`px-2.5 py-1 text-xs rounded-lg ${tf === k ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-200"}`} style={tf === k ? { outline: `1px solid ${ACCENT}` } : undefined}>{k}</button>
            ))}
          </div>
          <div className="w-px h-4 bg-gray-700" />
          <button onClick={() => setEditLayout((v) => !v)} className={`text-xs px-3 py-1 rounded-lg border ${editLayout ? "bg-gray-800 border-gray-700" : "bg-gray-900/70 border-gray-800 hover:border-gray-700"} flex items-center gap-1.5`} title="Reorder / show / hide widgets">
            <PencilLine size={12} /> {editLayout ? "Done" : "Edit"}
          </button>
          {editLayout && (
            <>
              <button onClick={resetLayout} className="text-xs px-3 py-1 rounded-lg bg-gray-900/70 border border-gray-800 hover:border-gray-700 flex items-center gap-1.5" title="Reset layout">
                <RotateCcw size={12} /> Reset
              </button>
              <button onClick={() => setPickerOpen(true)} className="text-xs px-3 py-1 rounded-lg bg-gray-900/70 border border-gray-800 hover:border-gray-700 flex items-center gap-1.5" title="Add hidden widgets">
                <Plus size={12} /> Add
              </button>
            </>
          )}
        </div>
      </div>

      {/* Add widget picker */}
      {editLayout && pickerOpen && (
        <div className="mb-3 p-3 bg-gray-950 border border-gray-800 rounded-xl">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-gray-300">Hidden widgets</div>
            <button onClick={() => setPickerOpen(false)} className="text-gray-400 hover:text-gray-200"><X size={16} /></button>
          </div>
          {hiddenWidgets.length === 0 ? (
            <div className="text-xs text-gray-500">All widgets are currently visible.</div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {hiddenWidgets.map((k) => (
                <button
                  key={k}
                  className="text-xs px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-800 hover:border-gray-700 hover:bg-gray-800 transition-colors flex items-center gap-1.5"
                  onClick={() => { enhancedToggleWidget(k, true); setPickerOpen(false); }}
                  title={PREMIUM_WIDGETS.has(k) ? "Premium" : ""}
                >
                  <Plus size={12} />
                  {ALL_WIDGET_LABELS[k] || k}
                  {PREMIUM_WIDGETS.has(k) && <span className="ml-1">👑</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 mb-6" onDragOver={onDragOver}>
        {orderedKeys.map((key, idx) => (
          <div
            key={key}
            draggable={editLayout}
            onDragStart={onDragStart(idx)}
            onDragEnd={onDragEnd}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop(idx)}
            className={`${editLayout ? "cursor-move" : ""} group relative transition-transform`}
            style={editLayout ? { outline: "2px dashed rgba(145,219,50,0.3)", borderRadius: "1rem", backgroundColor: "rgba(145,219,50,0.05)" } : undefined}
          >
            {editLayout && (
              <button
                onClick={() => enhancedToggleWidget(key, false)}
                className="absolute -top-2 -right-2 bg-red-600 hover:bg-red-700 border border-red-500 rounded-full p-1 text-white z-10 transition-colors"
                title="Hide widget"
              >
                <X size={14} />
              </button>
            )}
            {renderWidget(key)}
          </div>
        ))}
      </div>

      {/* Recent Trades */}
      <div className="bg-gray-900/70 rounded-2xl p-5 border border-gray-800">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-2 mb-3">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">Recent Trades</h2>
            <span className={chip}>Showing last {Math.min(filteredTrades.length, previewLimit)} ({tf})</span>
          </div>
        </div>

        {filteredTrades.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-gray-400 mb-2">No trades logged yet</div>
            <p className="text-sm text-gray-500">Start by adding your first trade to see your progress here</p>
          </div>
        ) : (
          <div className="space-y-3">
            {[...filteredTrades]
              .sort((a, b) => new Date(b?.timestamp || 0) - new Date(a?.timestamp || 0))
              .slice(0, previewLimit)
              .map((t, i) => {
                const base = t?.profit ?? 0;
                const tax = t?.ea_tax ?? 0;
                const display = include_tax_in_profit ? base - tax : base;
                const pos = display >= 0;
                return (
                  <div key={i} className="flex items-center justify-between p-4 bg-gray-900 rounded-xl border border-gray-800 hover:border-gray-700 transition-colors">
                    <div className="flex-1">
                      <div className="flex items-center gap-3">
                        <span className="font-semibold">{t?.player ?? "Unknown"}</span>
                        <span className="text-sm text-gray-400">({t?.version ?? "N/A"})</span>
                        {t?.tag && <span className="text-xs bg-gray-800 border border-gray-700 px-2 py-1 rounded-full">{t.tag}</span>}
                      </div>
                      <div className={`${subText} mt-1`}>
                        {formatCurrency(t?.buy ?? 0)} → {formatCurrency(t?.sell ?? 0)}
                        {t?.quantity > 1 && ` (${t.quantity}x)`} • {t?.platform ?? "Console"}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className={`font-semibold ${pos ? "text-green-400" : "text-red-400"}`}>{pos ? "+" : ""}{formatCurrency(display)} coins</div>
                      <div className={subText}>{t?.timestamp ? formatDate(t.timestamp) : "—"}</div>
                    </div>
                  </div>
                );
              })}
          </div>
        )}
      </div>
    </div>
  );
}
