// src/pages/AddTrade.jsx
import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { motion } from "framer-motion";
import { TrendingUp, TrendingDown, DollarSign, Package, Tag, FileText, Sparkles } from "lucide-react";
import { useDashboard } from "../context/DashboardContext";
import { useSettings } from "../context/SettingsContext";
import toast from "react-hot-toast";

const API_BASE = import.meta.env.VITE_API_URL || "";

const toNum = (v, d = 0) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
};

const searchPlayers = async (query) => {
  if (!query.trim()) return [];
  try {
    const r = await fetch(
      `${API_BASE}/api/search-players?q=${encodeURIComponent(query)}`,
      { credentials: "include" }
    );
    if (!r.ok) return [];
    const data = await r.json();
    return data.players || [];
  } catch (e) {
    console.error("Search failed:", e);
    return [];
  }
};

export default function AddTrade() {
  const { addTrade } = useDashboard();
  const {
    default_platform = "Console",
    custom_tags = [],
    isLoading: settingsLoading,
  } = useSettings();

  const [form, setForm] = useState({
    player: "",
    version: "",
    buy: "",
    sell: "",
    quantity: 1,
    platform: "Console",
    tag: "",
    notes: "",
  });
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const [searching, setSearching] = useState(false);
  const acRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    const onDocClick = (e) => {
      if (!acRef.current) return;
      if (!acRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  useEffect(() => {
    const q = form.player;
    if (!q || q.trim().length < 2) {
      setSuggestions([]);
      setOpen(false);
      setSearching(false);
      return;
    }
    setSearching(true);
    setOpen(true);
    setHighlight(-1);

    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;

    const t = setTimeout(async () => {
      try {
        const players = await searchPlayers(q);
        const mapped = players.map((p) => ({
          id: p.card_id ?? p.id ?? `${p.name}-${p.rating ?? ""}`,
          name: p.name || p.player_name || "",
          rating: p.rating ?? null,
          version: p.version || p.card_type || "",
          image_url: p.image_url,
        }));
        setSuggestions(mapped.slice(0, 10));
      } catch {
        setSuggestions([]);
      } finally {
        setSearching(false);
      }
    }, 250);

    return () => {
      clearTimeout(t);
      controller.abort();
    };
  }, [form.player]);

  useEffect(() => {
    if (!settingsLoading && default_platform) {
      setForm((s) => ({ ...s, platform: default_platform }));
    }
  }, [settingsLoading, default_platform]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setForm((s) => ({ ...s, [name]: value }));
  };

  const chooseSuggestion = useCallback((s) => {
    setForm((prev) => ({
      ...prev,
      player: s.name,
      version: s.version || prev.version,
    }));
    setOpen(false);
    setHighlight(-1);
  }, []);

  const handleNameKeyDown = (e) => {
    if (!open || !suggestions.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      if (highlight >= 0) {
        e.preventDefault();
        chooseSuggestion(suggestions[highlight]);
      } else if (suggestions.length === 1) {
        e.preventDefault();
        chooseSuggestion(suggestions[0]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);

    const payload = {
      player: form.player.trim(),
      version: form.version.trim(),
      buy: toNum(form.buy),
      sell: toNum(form.sell),
      quantity: toNum(form.quantity, 1),
      platform: form.platform || "Console",
      tag: form.tag.trim(),
      notes: form.notes.trim(),
    };

    try {
      const result = await addTrade(payload);
      if (result?.success) {
        toast.success("Trade logged successfully!");
        setForm((s) => ({
          ...s,
          player: "",
          version: "",
          buy: "",
          sell: "",
          tag: "",
          notes: "",
        }));
        setSuggestions([]);
        setOpen(false);
      } else {
        toast.error("Failed to log trade: " + (result?.message || "Unknown error"));
      }
    } catch (err) {
      console.error(err);
      toast.error("Failed to log trade.");
    } finally {
      setLoading(false);
    }
  };

  const allTags = useMemo(() => {
    const common = ["Snipe", "Investment", "Flip", "Pack Pull", "SBC", "Risky"];
    return [...new Set([...(custom_tags || []), ...common])];
  }, [custom_tags]);

  const qty = toNum(form.quantity, 1);
  const buy = toNum(form.buy, 0);
  const sell = toNum(form.sell, 0);
  const gross = (sell - buy) * qty;
  const tax = Math.floor(sell * qty * 0.05);
  const net = gross - tax;
  const roi = buy > 0 ? ((net / (buy * qty)) * 100).toFixed(1) : 0;

  return (
    <div className="min-h-screen bg-[#0e1320] text-white p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }}>
          <div className="flex items-center gap-3 mb-2">
            <Sparkles className="w-8 h-8 text-green-400" />
            <h1 className="text-4xl font-black">Add Trade</h1>
          </div>
          <p className="text-slate-400">Log your latest flip, investment, or snipe</p>
        </motion.div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Main Card */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.1 }}
            className="bg-gradient-to-br from-slate-900/80 to-slate-900/40 border border-white/10 rounded-2xl p-6 space-y-5"
          >
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {/* Player */}
              <Field label="Player Name" icon={<Users className="w-4 h-4" />}>
                <div className="relative" ref={acRef}>
                  <input
                    name="player"
                    placeholder="e.g. Cristiano Ronaldo"
                    autoComplete="off"
                    value={form.player}
                    onChange={handleChange}
                    onKeyDown={handleNameKeyDown}
                    onFocus={() => {
                      if (form.player.trim().length >= 2 && suggestions.length) setOpen(true);
                    }}
                    className="w-full p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                    required
                  />
                  {open && (
                    <div className="absolute z-20 mt-1 w-full max-h-64 overflow-auto rounded-xl border border-purple-500/30 bg-slate-900 shadow-2xl">
                      {searching && (
                        <div className="px-3 py-2 text-sm text-slate-400">Searching…</div>
                      )}
                      {!searching && suggestions.length === 0 && (
                        <div className="px-3 py-2 text-sm text-slate-400">No matches</div>
                      )}
                      {!searching &&
                        suggestions.map((s, idx) => (
                          <button
                            type="button"
                            key={s.id || idx}
                            onMouseDown={(e) => {
                              e.preventDefault();
                              chooseSuggestion(s);
                            }}
                            onMouseEnter={() => setHighlight(idx)}
                            className={`w-full text-left px-3 py-2 text-sm hover:bg-purple-600/20 transition-colors ${
                              idx === highlight ? "bg-purple-600/20" : ""
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <div className="font-medium truncate">
                                {s.name} {s.rating != null ? `(${s.rating})` : ""}
                              </div>
                            </div>
                            {s.version && (
                              <div className="text-xs text-slate-400">{s.version}</div>
                            )}
                          </button>
                        ))}
                    </div>
                  )}
                </div>
              </Field>

              {/* Version */}
              <Field label="Version/Type" icon={<Tag className="w-4 h-4" />}>
                <input
                  name="version"
                  placeholder="e.g. Gold Rare, TOTW, Icon"
                  value={form.version}
                  onChange={handleChange}
                  className="w-full p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                  required
                />
              </Field>

              {/* Buy */}
              <Field label="Buy Price" icon={<TrendingDown className="w-4 h-4 text-red-400" />}>
                <input
                  name="buy"
                  type="number"
                  inputMode="numeric"
                  placeholder="Purchase price"
                  value={form.buy}
                  onChange={handleChange}
                  className="w-full p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                  required
                />
              </Field>

              {/* Sell */}
              <Field label="Sell Price" icon={<TrendingUp className="w-4 h-4 text-green-400" />}>
                <input
                  name="sell"
                  type="number"
                  inputMode="numeric"
                  placeholder="Sale price"
                  value={form.sell}
                  onChange={handleChange}
                  className="w-full p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                  required
                />
              </Field>

              {/* Quantity */}
              <Field label="Quantity" icon={<Package className="w-4 h-4" />}>
                <input
                  name="quantity"
                  type="number"
                  min="1"
                  step="1"
                  placeholder="Number of cards"
                  value={form.quantity}
                  onChange={handleChange}
                  className="w-full p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                  required
                />
              </Field>

              {/* Platform */}
              <Field label="Platform" icon={<DollarSign className="w-4 h-4" />}>
                <select
                  name="platform"
                  value={form.platform}
                  onChange={handleChange}
                  className="w-full p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                >
                  <option value="Console">Console</option>
                  <option value="PC">PC</option>
                  <option value="PS">PS</option>
                  <option value="Xbox">Xbox</option>
                </select>
              </Field>
            </div>

            {/* Tag */}
            <Field label="Tag (Optional)" icon={<Tag className="w-4 h-4" />}>
              <div className="flex gap-2">
                <input
                  name="tag"
                  placeholder="Custom tag or select from dropdown"
                  value={form.tag}
                  onChange={handleChange}
                  className="flex-1 p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                />
                <select
                  onChange={(e) => setForm((s) => ({ ...s, tag: e.target.value }))}
                  className="p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all"
                  value=""
                >
                  <option value="">Quick Tags</option>
                  {allTags.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
            </Field>

            {/* Notes */}
            <Field label="Notes (Optional)" icon={<FileText className="w-4 h-4" />}>
              <textarea
                name="notes"
                placeholder="Add any additional details about this trade..."
                value={form.notes}
                onChange={handleChange}
                rows={3}
                className="w-full p-3 bg-slate-950/80 border border-white/10 rounded-xl text-sm focus:border-purple-500/50 focus:ring-2 focus:ring-purple-500/20 transition-all resize-none"
              />
            </Field>
          </motion.div>

          {/* Preview */}
          {(buy > 0 || sell > 0) && qty > 0 && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="bg-gradient-to-br from-purple-900/30 to-indigo-900/20 border border-purple-500/30 rounded-2xl p-6"
            >
              <h3 className="text-lg font-bold mb-4 flex items-center gap-2">
                <DollarSign className="w-5 h-5 text-purple-400" />
                Trade Preview
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <PreviewItem label="Gross Profit" value={`${gross.toLocaleString()} coins`} />
                <PreviewItem label="EA Tax (5%)" value={`-${tax.toLocaleString()} coins`} className="text-red-400" />
                <PreviewItem
                  label="Net Profit"
                  value={`${net.toLocaleString()} coins`}
                  className={net >= 0 ? "text-green-400" : "text-red-400"}
                />
                <PreviewItem
                  label="ROI"
                  value={`${roi}%`}
                  className={Number(roi) >= 0 ? "text-green-400" : "text-red-400"}
                />
              </div>
            </motion.div>
          )}

          {/* Submit */}
          <motion.button
            disabled={loading}
            type="submit"
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.99 }}
            className="w-full bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-500 hover:to-emerald-500 text-white py-4 px-6 rounded-xl font-bold text-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-green-600/20"
          >
            {loading ? "Logging Trade..." : "Log Trade"}
          </motion.button>
        </form>
      </div>
    </div>
  );
}

function Field({ label, icon, children }) {
  return (
    <label className="block">
      <div className="flex items-center gap-2 mb-2 text-sm font-semibold text-slate-300">
        {icon}
        <span>{label}</span>
      </div>
      {children}
    </label>
  );
}

function PreviewItem({ label, value, className = "text-white" }) {
  return (
    <div className="bg-slate-900/40 rounded-xl p-3">
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      <p className={`text-lg font-bold ${className}`}>{value}</p>
    </div>
  );
}

function Users({ className }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"
      />
    </svg>
  );
}
