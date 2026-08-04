// src/pages/Demo.jsx
//
// Read-only, investor-facing "Data Pipeline Dashboard" at /demo. Public
// route (see App.jsx - deliberately NOT nested inside the PrivateRoute
// shell), so every fetch here uses the browser's plain fetch() with
// credentials included but no dependency on being logged in, and never the
// shared src/axios.js instance (its response interceptor hard-redirects to
// /login on any 401, which would break this page for a logged-out visitor).
//
// Every number on this page comes from a live backend query
// (app/routers/dashboard.py's /api/dashboard/stats + /activity) or from
// the same public player endpoints the real app already uses
// (/api/players/*) - nothing here is hardcoded or simulated.
import React, { useEffect, useRef, useState } from "react";
import {
  Database, Activity, TrendingUp, Clock, Server, Zap, Search,
  CheckCircle2, XCircle, HelpCircle, Users, BarChart3, Gauge,
} from "lucide-react";
import SalesLineChart from "../components/SalesLineChart";
import PriceTrendChart from "../components/PriceTrendChart";

const API_BASE = import.meta.env.VITE_API_URL || "";
const ACCENT = "#91db32";

const cardBase =
  "bg-gray-900/70 rounded-2xl p-4 border border-gray-800 hover:border-gray-700 transition-colors flex flex-col justify-between min-h-[120px]";
const cardTitle = "text-[13px] font-semibold text-gray-200/90 leading-none";
const cardBig = "text-[clamp(20px,1.8vw,26px)] font-extrabold leading-tight tracking-tight tabular-nums";
const subText = "text-[12px] text-gray-400 leading-snug";
const sectionTitle = "text-lg font-bold text-white flex items-center gap-2 mb-3";

function fmtNum(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString();
}

function fmtCoins(n) {
  if (n === null || n === undefined) return "—";
  return `${Number(n).toLocaleString()} coins`;
}

function timeAgo(iso) {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function KpiCard({ label, value, sub, icon: Icon }) {
  return (
    <div className={cardBase}>
      <div className="flex items-center justify-between">
        <div className={cardTitle}>{label}</div>
        {Icon && <Icon className="w-4 h-4 shrink-0" style={{ color: ACCENT }} />}
      </div>
      <div className={cardBig}>{value}</div>
      {sub && <div className={subText}>{sub}</div>}
    </div>
  );
}

function StatusDot({ status }) {
  if (status === "ok") return <CheckCircle2 className="w-4 h-4 text-green-400" />;
  if (status === "failing") return <XCircle className="w-4 h-4 text-red-400" />;
  return <HelpCircle className="w-4 h-4 text-gray-500" />;
}

const EVENT_ICON = { sale: "💰", bin: "🏷️", player_update: "🔄", sync: "⚙️" };

export default function Demo() {
  const [stats, setStats] = useState(null);
  const [activity, setActivity] = useState([]);
  const [apiMs, setApiMs] = useState(null);
  const [loadError, setLoadError] = useState("");

  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [recentSales, setRecentSales] = useState([]);
  const [recentBins, setRecentBins] = useState([]);
  const searchAbort = useRef(null);

  useEffect(() => {
    let cancelled = false;

    async function loadStats() {
      try {
        const t0 = performance.now();
        const r = await fetch(`${API_BASE}/api/dashboard/stats`, { credentials: "include" });
        const elapsed = Math.round(performance.now() - t0);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (cancelled) return;
        setStats(data);
        setApiMs(elapsed);
        setLoadError("");
      } catch (e) {
        if (!cancelled) setLoadError(e?.message || "Failed to load dashboard stats");
      }
    }

    async function loadActivity() {
      try {
        const r = await fetch(`${API_BASE}/api/dashboard/activity?limit=20`, { credentials: "include" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (cancelled) return;
        setActivity(data.events || []);
      } catch {
        // non-fatal - KPI cards are the primary content, activity feed can retry silently
      }
    }

    loadStats();
    loadActivity();
    const statsTimer = setInterval(loadStats, 30000);
    const activityTimer = setInterval(loadActivity, 15000);
    return () => {
      cancelled = true;
      clearInterval(statsTimer);
      clearInterval(activityTimer);
    };
  }, []);

  useEffect(() => {
    if (searchAbort.current) searchAbort.current.abort();
    if (!query || query.trim().length < 2) {
      setSuggestions([]);
      return;
    }
    const controller = new AbortController();
    searchAbort.current = controller;
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(
          `${API_BASE}/api/players/autocomplete?q=${encodeURIComponent(query)}`,
          { credentials: "include", signal: controller.signal }
        );
        if (!r.ok) return;
        const data = await r.json();
        setSuggestions(data.items || []);
      } catch {
        // aborted or transient - ignore
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  async function selectPlayer(player) {
    setSelected(player);
    setSuggestions([]);
    setQuery(`${player.name} (${player.rating})`);
    setMetrics(null);
    setRecentSales([]);
    setRecentBins([]);
    try {
      const [mRes, sRes, bRes] = await Promise.all([
        fetch(`${API_BASE}/api/players/${player.card_id}/market-metrics`, { credentials: "include" }),
        fetch(`${API_BASE}/api/players/${player.card_id}/sales-history?limit=25`, { credentials: "include" }),
        fetch(`${API_BASE}/api/players/${player.card_id}/bin-history?limit=25`, { credentials: "include" }),
      ]);
      if (mRes.ok) setMetrics(await mRes.json());
      if (sRes.ok) setRecentSales((await sRes.json()).sales || []);
      if (bRes.ok) setRecentBins((await bRes.json()).points || []);
    } catch {
      // leave whatever loaded successfully - non-fatal for a demo page
    }
  }

  const kpis = stats
    ? [
        { label: "Total Players in Database", value: fmtNum(stats.totals.total_players), icon: Users },
        { label: "Total Historical Sales Stored", value: fmtNum(stats.totals.total_sales), icon: BarChart3 },
        { label: "Total BIN Price Snapshots Stored", value: fmtNum(stats.totals.total_bin_snapshots), icon: Database },
        { label: "Sales Recorded (24h)", value: fmtNum(stats.last_24h.sales), icon: TrendingUp },
        { label: "BIN Prices Updated (24h)", value: fmtNum(stats.last_24h.bin_updates), icon: Zap },
        { label: "Avg Sales / Hour (24h)", value: fmtNum(stats.last_24h.avg_sales_per_hour), icon: Gauge },
        { label: "Last Successful Auto Sync", value: timeAgo(stats.sync_status.last_successful_auto_sync), sub: fmtDateTime(stats.sync_status.last_successful_auto_sync), icon: CheckCircle2 },
        { label: "Last Successful Sales Sync", value: timeAgo(stats.sync_status.last_successful_sales_sync), sub: fmtDateTime(stats.sync_status.last_successful_sales_sync), icon: CheckCircle2 },
        { label: "Database Uptime", value: timeAgo(stats.database.started_at), sub: `Tracking data since ${fmtDateTime(stats.totals.first_recorded_sale_at)}`, icon: Server },
        { label: "Cards Tracked Today", value: fmtNum(stats.cards_tracked_today), icon: Activity },
      ]
    : [];

  return (
    <div className="min-h-screen bg-black text-white">
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-5 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-extrabold">Data Pipeline Dashboard</h1>
            <p className="text-sm text-gray-400">Live production data - read-only</p>
          </div>
          <span
            className="inline-flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-full border"
            style={{ borderColor: ACCENT, color: ACCENT }}
          >
            <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: ACCENT }} />
            Live
          </span>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-5 py-6 space-y-8">
        {loadError && (
          <div className="bg-red-900/30 border border-red-800 text-red-300 rounded-xl p-4 text-sm">
            Couldn't load live stats ({loadError}). Retrying automatically…
          </div>
        )}

        {/* KPI grid */}
        <section>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            {stats
              ? kpis.map((k) => <KpiCard key={k.label} {...k} />)
              : Array.from({ length: 10 }).map((_, i) => (
                  <div key={i} className={`${cardBase} animate-pulse`}>
                    <div className="h-3 w-2/3 bg-gray-800 rounded" />
                    <div className="h-6 w-1/2 bg-gray-800 rounded" />
                  </div>
                ))}
          </div>
        </section>

        {/* Recent Activity */}
        <section>
          <h2 className={sectionTitle}><Activity className="w-5 h-5" style={{ color: ACCENT }} /> Recent Activity</h2>
          <div className={`${cardBase} h-auto max-h-96 overflow-y-auto`}>
            {activity.length === 0 && <div className={subText}>Loading recent activity…</div>}
            <ul className="divide-y divide-gray-800">
              {activity.map((e, i) => (
                <li key={i} className="py-2 flex items-center gap-3 text-sm">
                  <span>{EVENT_ICON[e.type] || "•"}</span>
                  <span className="flex-1 text-gray-200">{e.message}</span>
                  <span className="text-gray-500 text-xs whitespace-nowrap">{timeAgo(e.at)}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Pipeline Status */}
        <section>
          <h2 className={sectionTitle}><Server className="w-5 h-5" style={{ color: ACCENT }} /> Pipeline Status</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            {(stats?.pipeline_status || []).map((p) => (
              <div key={p.name} className={cardBase}>
                <div className="flex items-center justify-between">
                  <div className={cardTitle}>{p.name}</div>
                  <StatusDot status={p.status} />
                </div>
                <div className="text-sm text-gray-300 space-y-1 mt-2">
                  <div>Last run: <span className="text-white">{timeAgo(p.last_run_at)}</span></div>
                  <div>Records processed: <span className="text-white">{p.records_processed != null ? fmtNum(p.records_processed) : "—"}</span></div>
                  <div>Status: <span className={p.status === "ok" ? "text-green-400" : p.status === "failing" ? "text-red-400" : "text-gray-400"}>{p.status}</span></div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Database Statistics */}
        <section>
          <h2 className={sectionTitle}><Database className="w-5 h-5" style={{ color: ACCENT }} /> Database Statistics</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            <div className={cardBase}>
              <div className={cardTitle}>Largest 24h Mover (volatility)</div>
              <div className={cardBig}>{stats?.database_statistics?.largest_24h_mover?.name || "—"}</div>
              <div className={subText}>{stats?.database_statistics?.largest_24h_mover?.rating ? `${stats.database_statistics.largest_24h_mover.rating} OVR` : ""}</div>
            </div>
            <div className={cardBase}>
              <div className={cardTitle}>Most Traded Player Today</div>
              <div className={cardBig}>{stats?.database_statistics?.most_traded_player_today?.name || "—"}</div>
              <div className={subText}>{stats?.database_statistics?.most_traded_player_today?.sales_24h != null ? `${fmtNum(stats.database_statistics.most_traded_player_today.sales_24h)} sales in 24h` : ""}</div>
            </div>
            <div className={cardBase}>
              <div className={cardTitle}>Highest Liquidity Card</div>
              <div className={cardBig}>{stats?.database_statistics?.highest_liquidity_card?.name || "—"}</div>
              <div className={subText}>{stats?.database_statistics?.highest_liquidity_card?.sales_per_hour_24h != null ? `${stats.database_statistics.highest_liquidity_card.sales_per_hour_24h}/hr` : ""}</div>
            </div>
            <div className={cardBase}>
              <div className={cardTitle}>Unique Players With Sales</div>
              <div className={cardBig}>{fmtNum(stats?.database_statistics?.total_unique_players_with_sales)}</div>
            </div>
            <div className={cardBase}>
              <div className={cardTitle}>Unique Players With BIN History</div>
              <div className={cardBig}>{fmtNum(stats?.database_statistics?.total_unique_players_with_bin_history)}</div>
            </div>
          </div>
        </section>

        {/* Live Data Example */}
        <section>
          <h2 className={sectionTitle}><Search className="w-5 h-5" style={{ color: ACCENT }} /> Live Data Example</h2>
          <div className={`${cardBase} h-auto relative`}>
            <input
              value={query}
              onChange={(e) => { setQuery(e.target.value); setSelected(null); }}
              placeholder="Search any player (e.g. Mbappe)…"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-gray-500"
            />
            {suggestions.length > 0 && (
              <div className="absolute left-4 right-4 top-16 z-20 bg-gray-950 border border-gray-700 rounded-lg overflow-hidden shadow-xl">
                {suggestions.map((s) => (
                  <button
                    key={s.card_id}
                    onClick={() => selectPlayer(s)}
                    className="w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 flex items-center gap-2"
                  >
                    {s.image_url && <img src={s.image_url} alt="" className="w-6 h-6 object-contain" />}
                    <span>{s.name} ({s.rating}) {s.version} {s.position}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {selected && metrics && (
            <div className="mt-4 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                <KpiCard label="Current BIN (PS)" value={metrics.currentBin?.ps != null ? fmtCoins(metrics.currentBin.ps) : "—"} icon={Zap} />
                <KpiCard label="Median Sale (24h)" value={metrics.realPrice?.medianSold24h != null ? fmtCoins(metrics.realPrice.medianSold24h) : "—"} icon={TrendingUp} />
                <KpiCard label="Sales / Hour" value={metrics.liquidity?.salesPerHour24h ?? "—"} icon={Gauge} />
                <KpiCard label="Volatility (24h)" value={metrics.volatility?.stddev24h != null ? fmtNum(Math.round(metrics.volatility.stddev24h)) : "—"} icon={Activity} />
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className={cardBase}>
                  <SalesLineChart cardId={selected.card_id} days={1} height={220} />
                </div>
                <div className={cardBase}>
                  <PriceTrendChart playerId={selected.card_id} platform="ps" height={220} />
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className={`${cardBase} h-auto`}>
                  <div className={cardTitle}>Last 25 Completed Sales</div>
                  <div className="mt-2 max-h-72 overflow-y-auto">
                    <table className="w-full text-xs text-gray-300">
                      <thead className="text-gray-500 text-left">
                        <tr><th className="py-1">Sold At</th><th>Sold Price</th></tr>
                      </thead>
                      <tbody>
                        {recentSales.map((s, i) => (
                          <tr key={i} className="border-t border-gray-800">
                            <td className="py-1">{fmtDateTime(s.soldAt)}</td>
                            <td>{fmtCoins(s.soldPrice)}</td>
                          </tr>
                        ))}
                        {recentSales.length === 0 && (
                          <tr><td colSpan={2} className="py-2 text-gray-500">No sales recorded yet.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div className={`${cardBase} h-auto`}>
                  <div className={cardTitle}>Last 25 BIN Captures</div>
                  <div className="mt-2 max-h-72 overflow-y-auto">
                    <table className="w-full text-xs text-gray-300">
                      <thead className="text-gray-500 text-left">
                        <tr><th className="py-1">Captured At</th><th>Platform</th><th>BIN</th></tr>
                      </thead>
                      <tbody>
                        {recentBins.map((b, i) => (
                          <tr key={i} className="border-t border-gray-800">
                            <td className="py-1">{fmtDateTime(b.capturedAt)}</td>
                            <td>{b.platform}</td>
                            <td>{fmtCoins(b.lowestBin)}</td>
                          </tr>
                        ))}
                        {recentBins.length === 0 && (
                          <tr><td colSpan={3} className="py-2 text-gray-500">No BIN captures recorded yet.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          )}
        </section>
      </main>

      <footer className="border-t border-gray-800 mt-8">
        <div className="max-w-7xl mx-auto px-5 py-5 flex flex-wrap gap-x-8 gap-y-2 text-xs text-gray-400">
          <span>Environment: <span className="text-white">{stats?.footer?.environment || "—"}</span></span>
          <span>Build Version: <span className="text-white">{stats?.footer?.build_version || "—"}</span></span>
          <span>Database Size: <span className="text-white">{stats?.footer?.database_size || "—"}</span></span>
          <span>API Response Time: <span className="text-white">{apiMs != null ? `${apiMs} ms` : "—"}</span></span>
        </div>
      </footer>
    </div>
  );
}
