// src/pages/BestBuys.jsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Search,
  RefreshCw,
  ArrowRight,
  TrendingUp,
  SlidersHorizontal,
  ChevronDown,
  Check,
  Copy,
  ExternalLink,
  Flame,
} from "lucide-react";

const API_BASE =
  (import.meta?.env?.VITE_API_URL?.replace(/\/$/, "")) || "https://api.futhub.co.uk";

const PLATFORM = "ps"; // console only (used internally for API)

function formatCoins(n) {
  const x = Number(n || 0);
  return Number.isFinite(x) ? `${x.toLocaleString()} c` : "—";
}

function cx(...xs) {
  return xs.filter(Boolean).join(" ");
}

// small debounce (no deps)
function useDebounced(fn, wait = 200) {
  const t = useRef();
  return (...args) => {
    clearTimeout(t.current);
    t.current = setTimeout(() => fn(...args), wait);
  };
}

async function api(path) {
  const r = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  return r.json();
}

function RiskPill({ label = "Unknown" }) {
  const map = {
    Low: "bg-emerald-500/20 text-emerald-200 border-emerald-400/30",
    Medium: "bg-amber-500/20 text-amber-200 border-amber-400/30",
    High: "bg-rose-600/20 text-rose-200 border-rose-400/30",
    Unknown: "bg-white/10 text-zinc-200 border-white/10",
  };
  return (
    <span className={cx("px-2.5 py-1 rounded-full border text-xs font-medium", map[label] || map.Unknown)}>
      {label}
    </span>
  );
}

function SkeletonCard() {
  return (
    <div className="p-4 rounded-3xl bg-white/5 border border-white/10 animate-pulse">
      <div className="flex items-center gap-3">
        <div className="w-12 h-12 rounded-lg bg-white/10" />
        <div className="flex-1">
          <div className="h-4 w-40 bg-white/10 rounded mb-2" />
          <div className="h-3 w-28 bg-white/10 rounded" />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-4 bg-white/10 rounded" />
        ))}
      </div>
      <div className="mt-4 h-8 bg-white/10 rounded" />
    </div>
  );
}

// simple sparkline (no deps)
function Sparkline({ data }) {
  if (!Array.isArray(data) || data.length < 2) return null;
  const w = 120, h = 36;
  const min = Math.min(...data), max = Math.max(...data), span = Math.max(max - min, 1);
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => `${i * step},${h - ((v - min) / span) * h}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} className="block">
      <polyline
        fill="none"
        stroke={up ? "rgb(110, 231, 183)" : "rgb(252, 165, 165)"}
        strokeWidth="2"
        points={pts}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* ---------- Custom Glass Dropdown ---------- */
function GlassDropdown({ value, onChange, options, label = "Sort" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  const current = options.find((o) => o.value === value) || options[0];

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-2xl bg-white/5 border border-white/10 px-3 py-2 text-sm"
        aria-haspopup="listbox"
        aria-expanded={open}
        title={label}
      >
        <SlidersHorizontal className="w-4 h-4 text-zinc-300" />
        <span className="text-white">{current.label}</span>
        <ChevronDown className={cx("w-4 h-4 text-zinc-300 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute z-30 mt-2 w-64 rounded-2xl bg-[#1b1030] border border-white/10 shadow-xl overflow-hidden"
        >
          {options.map((o) => {
            const active = o.value === value;
            return (
              <button
                key={o.value}
                role="option"
                aria-selected={active}
                onClick={() => { onChange(o.value); setOpen(false); }}
                className={cx(
                  "w-full text-left px-3 py-2 flex items-center gap-2 text-sm",
                  active ? "bg-white/10 text-white" : "text-zinc-200 hover:bg-white/5"
                )}
              >
                <Check className={cx("w-4 h-4", active ? "opacity-100" : "opacity-0")} />
                <span>{o.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
/* ------------------------------------------ */

export default function BestBuys() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Autocomplete state (global players)
  const [term, setTerm] = useState("");
  const [sug, setSug] = useState([]);
  const [showSug, setShowSug] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);

  // Filters / sort (unchanged)
  const [risk, setRisk] = useState("All"); // All | Low | Medium | High
  const [sort, setSort] = useState("cheap"); // cheap | current | volume | rating | name

  const navigate = useNavigate();

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(
        `${API_BASE}/api/ai/top-buys?platform=${encodeURIComponent(PLATFORM)}&limit=48`,
        { credentials: "include" }
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const data = await r.json();
      const list = Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : [];
      setRows(list);
    } catch (e) {
      setError(e.message || "Failed to load Best Buys");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  // -------- AUTOCOMPLETE (global) --------
  const debouncedFetch = useDebounced(async (q) => {
    if (!q?.trim()) { setSug([]); return; }
    try {
      const res = await api(`/api/players/autocomplete?q=${encodeURIComponent(q)}`);
      setSug(res?.items || []);
      setShowSug(true);
      setActiveIdx(-1);
    } catch {
      setSug([]);
      setShowSug(false);
    }
  }, 160);

  useEffect(() => { debouncedFetch(term); }, [term]);

  function goTo(name) {
    if (!name?.trim()) return;
    navigate(`/smart-buyer-ai?name=${encodeURIComponent(name.trim())}`);
  }

  function selectSuggestion(item) {
    setShowSug(false);
    setTerm(item?.name || "");
    if (item?.name) goTo(item.name);
  }

  function onKeyDown(e) {
    if (!showSug || sug.length === 0) {
      if (e.key === "Enter" && term.trim()) goTo(term);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % sug.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + sug.length) % sug.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = sug[activeIdx] || sug[0];
      if (item) selectSuggestion(item);
    } else if (e.key === "Escape") {
      setShowSug(false);
    }
  }
  // ---------------------------------------

  // filter + sort (NOTE: no search-term filtering here anymore)
  const filteredSorted = useMemo(() => {
    let arr = Array.isArray(rows) ? rows.slice() : [];
    if (["Low", "Medium", "High"].includes(risk)) {
      arr = arr.filter((r) => (r?.risk_label || "").toLowerCase() === risk.toLowerCase());
    }
    arr.sort((a, b) => {
      const cheapA = Number(a.cheap_pct || 0), cheapB = Number(b.cheap_pct || 0);
      const curA = Number(a.current || 0),   curB = Number(b.current || 0);
      const volA = Number(a.vol24 || 0),     volB = Number(b.vol24 || 0);
      const ratA = Number(a?.player?.rating || 0), ratB = Number(b?.player?.rating || 0);
      const nameA = (a?.player?.name || "").localeCompare(b?.player?.name || "");
      switch (sort) {
        case "cheap":   return cheapA === cheapB ? volB - volA : cheapA - cheapB;
        case "current": return curA - curB;
        case "volume":  return volB - volA;
        case "rating":  return ratB - ratA;
        case "name":    return nameA;
        default:        return cheapA - cheapB;
      }
    });
    return arr;
  }, [rows, risk, sort]);

  // KPIs
  const kpi = useMemo(() => {
    const list = filteredSorted.slice(0, 8);
    const avgCheap = list.length ? list.reduce((a, r) => a + Number(r.cheap_pct || 0), 0) / list.length : 0;
    const avgVol = list.length ? Math.round(list.reduce((a, r) => a + Number(r.vol24 || 0), 0) / list.length) : 0;
    const medianPrice = list.length
      ? list.map((r) => Number(r.current || 0)).sort((a, b) => a - b)[Math.floor(list.length / 2)]
      : 0;
    return { avgCheap, avgVol, medianPrice };
  }, [filteredSorted]);

  function copyLink(name) {
    const url = `${location.origin}${location.pathname}#/smart-buyer-ai?name=${encodeURIComponent(name || "")}`;
    navigator.clipboard?.writeText(url);
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#180a2e] to-[#0e0a19] text-white">
      <div className="mx-auto max-w-6xl px-4 py-6 space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="rounded-2xl bg-white/10 border border-white/10 p-2">
            <TrendingUp className="w-5 h-5 text-white" />
          </div>
          <div className="flex-1">
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight">Best Buys — Live Picks</h1>
            <p className="text-sm text-white/70">
              Underpriced cards right now. Use the search to jump to Smart Buyer for any player.
            </p>
          </div>
        </div>

        {/* Controls */}
        <div className="flex flex-col md:flex-row gap-3">
          {/* Autocomplete search (global) */}
          <div className="relative flex-1">
            <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-2xl px-3">
              <Search className="w-4 h-4 text-zinc-300" />
              <input
                className="w-full bg-transparent py-3 text-white placeholder:text-zinc-400 focus:outline-none"
                placeholder="Search any player (e.g., Mohamed Salah)"
                value={term}
                onChange={(e) => setTerm(e.target.value)}
                onFocus={() => sug.length && setShowSug(true)}
                onKeyDown={onKeyDown}
              />
              <button
                onClick={() => term.trim() && goTo(term)}
                className="rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-3 py-2"
              >
                Search
              </button>
            </div>

            {showSug && sug.length > 0 && (
              <div className="absolute z-30 mt-2 w-full rounded-2xl bg-[#1b1030] border border-white/10 shadow-xl overflow-hidden">
                {sug.map((s, i) => (
                  <button
                    key={`${s.card_id}-${s.name}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => selectSuggestion(s)}
                    className={cx(
                      "w-full text-left px-3 py-2 flex items-center gap-2 text-sm",
                      i === activeIdx ? "bg-white/10 text-white" : "text-zinc-200 hover:bg-white/5"
                    )}
                  >
                    <img src={s.image_url} alt="" className="w-6 h-6 rounded border border-white/10" />
                    <span className="flex-1 truncate">{s.label || s.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* risk filter */}
          <div className="flex items-center gap-1 bg-white/5 border border-white/10 rounded-2xl p-1">
            {["All", "Low", "Medium", "High"].map((r) => (
              <button
                key={r}
                onClick={() => setRisk(r)}
                className={cx("px-3 py-2 rounded-xl text-sm", risk === r ? "bg-white/15" : "hover:bg-white/10")}
                title={`Show ${r.toLowerCase()} risk picks`}
              >
                {r}
              </button>
            ))}
          </div>

          {/* custom sort dropdown */}
          <GlassDropdown
            value={sort}
            onChange={setSort}
            options={[
              { value: "cheap", label: "Cheapest vs usual" },
              { value: "volume", label: "Highest volume" },
              { value: "current", label: "Lowest current price" },
              { value: "rating", label: "Highest rating" },
              { value: "name", label: "Name A→Z" },
            ]}
            label="Sort"
          />

          {/* reload */}
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-2xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 px-4 py-2 text-sm font-medium"
            title="Reload"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Reload
          </button>
        </div>

        {/* KPIs */}
        <div className="grid md:grid-cols-3 gap-3">
          <div className="rounded-2xl bg-white/5 border border-white/10 p-4">
            <div className="text-xs uppercase text-white/70">Avg. Cheap vs Usual (top picks)</div>
            <div className="mt-1 text-2xl font-bold tabular-nums text-emerald-300">
              {(kpi.avgCheap * 100).toFixed(1)}%
            </div>
          </div>
          <div className="rounded-2xl bg-white/5 border border-white/10 p-4">
            <div className="text-xs uppercase text-white/70">Avg. 24h Volume (top picks)</div>
            <div className="mt-1 text-2xl font-bold tabular-nums">{kpi.avgVol.toLocaleString()}</div>
          </div>
          <div className="rounded-2xl bg-white/5 border border-white/10 p-4">
            <div className="text-xs uppercase text-white/70">Median Current Price</div>
            <div className="mt-1 text-2xl font-bold tabular-nums">{formatCoins(kpi.medianPrice)}</div>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-3xl bg-rose-600/20 border border-rose-400/30 p-4">
            <p className="font-semibold">Error loading Best Buys</p>
            <p className="text-sm text-rose-100/80 mt-1">{error}</p>
            <button
              onClick={load}
              className="mt-3 rounded-xl bg-rose-600/30 hover:bg-rose-600/40 px-3 py-2 text-sm"
            >
              Try again
            </button>
          </div>
        )}

        {/* Grid */}
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {loading && Array.from({ length: 9 }).map((_, i) => <SkeletonCard key={`s-${i}`} />)}

          {!loading &&
            filteredSorted.map((r, i) => {
              const name = r.player?.name || r.player_card_id || "Unknown Player";
              const img = r.player?.image_url;
              const meta =
                [r.player?.position, r.player?.version, r.player?.rating].filter(Boolean).join(" • ") || "\u00A0";
              const cheapPct = Number(r.cheap_pct || 0);
              const sparkData =
                Array.isArray(r.spark) && r.spark.length >= 2
                  ? r.spark
                  : [Number(r.median7 || r.current || 0), Number(r.current || r.median7 || 0)];

              return (
                <div
                  key={r.player_card_id || i}
                  className="group p-4 rounded-3xl bg-white/5 border border-white/10 hover:bg-white/10 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    {img ? (
                      <img
                        src={img}
                        alt=""
                        className="w-12 h-12 rounded-lg border border-white/10 bg-black/20 object-cover"
                        onError={(e) => (e.currentTarget.style.display = "none")}
                      />
                    ) : (
                      <div className="w-12 h-12 rounded-lg bg-black/20 border border-white/10" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="font-semibold truncate">{name}</div>
                      <div className="text-sm text-white/70 truncate">{meta}</div>
                    </div>
                    <RiskPill label={r.risk_label || "Unknown"} />
                  </div>

                  <div className="mt-3 rounded-2xl border border-white/10 bg-black/20 p-2">
                    <Sparkline data={sparkData.map((x) => Number(x || 0))} />
                  </div>

                  <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                    <div className="text-white/70">Current</div>
                    <div className="text-right tabular-nums font-semibold">{formatCoins(r.current)}</div>

                    <div className="text-white/70">Usual</div>
                    <div className="text-right tabular-nums font-semibold">{formatCoins(r.median7)}</div>

                    <div className="text-white/70">Cheap vs usual</div>
                    <div className={cx("text-right tabular-nums font-semibold", cheapPct < 0 ? "text-emerald-300" : "text-rose-300")}>
                      {(cheapPct * 100).toFixed(1)}%
                    </div>

                    <div className="text-white/70">24h volume</div>
                    <div className="text-right tabular-nums">{Number(r.vol24 || 0).toLocaleString()}</div>
                  </div>

                  <div className="mt-4 flex items-center justify-between">
                    <div className="flex gap-2">
                      {Math.abs(cheapPct) >= 0.08 ? (
                        <span className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full bg-emerald-500/20 border border-emerald-400/30 text-emerald-200">
                          <Flame className="w-3.5 h-3.5" />
                          Hot
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full bg-white/10 border border-white/10">
                          Console
                        </span>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <button
                        className="inline-flex items-center gap-2 rounded-2xl bg-white/10 hover:bg-white/15 px-3 py-2 text-xs"
                        onClick={(e) => { e.stopPropagation(); copyLink(name); }}
                        title="Copy Smart Buyer link"
                      >
                        <Copy className="w-3.5 h-3.5" /> Copy Link
                      </button>
                      <a
                        href={`#/smart-buyer-ai?name=${encodeURIComponent(name)}`}
                        className="inline-flex items-center gap-2 rounded-2xl bg-indigo-600 hover:bg-indigo-500 px-3 py-2 text-sm font-medium"
                        onClick={(e) => e.stopPropagation()}
                      >
                        View <ArrowRight className="w-4 h-4" />
                      </a>
                    </div>
                  </div>
                </div>
              );
            })}
        </div>

        {!loading && !error && !filteredSorted.length && (
          <div className="text-center text-white/70 py-10">
            No signals right now — try different filters or check back soon.
          </div>
        )}

        <div className="pt-2 text-xs text-white/60 flex items-center gap-2">
          <ExternalLink className="w-4 h-4" />
          Tip: Click any card to open Smart Buyer. Sort by <em>Cheapest vs usual</em> for bargains, or by <em>Volume</em> for liquid flips.
        </div>
      </div>
    </div>
  );
}
