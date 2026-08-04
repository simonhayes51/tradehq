// src/components/PlayerAutocomplete.jsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import { searchPlayers } from "../api/players";

export default function PlayerAutocomplete({
  value,                // string, the current text value
  onChange,             // (text: string) => void
  onSelect,             // (player: {name, rating, card_id}) => void
  placeholder = "Search player by name…",
  minChars = 2,
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState(value || "");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [hi, setHi] = useState(-1); // highlighted index for keyboard nav
  const boxRef = useRef(null);

  // keep input controlled by parent
  useEffect(() => setQ(value || ""), [value]);

  // click outside to close
  useEffect(() => {
    const onDoc = (e) => {
      if (!boxRef.current) return;
      if (!boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  // debounce search
  useEffect(() => {
    if (q.trim().length < minChars) {
      setResults([]);
      return;
    }
    let alive = true;
    const t = setTimeout(async () => {
      try {
        setLoading(true);
        const r = await searchPlayers(q.trim());
        if (!alive) return;
        setResults(r);
        setOpen(true);
        setHi(r.length ? 0 : -1);
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [q, minChars]);

  const handleInput = (e) => {
    onChange?.(e.target.value);
    setQ(e.target.value);
    if (!open) setOpen(true);
  };

  const choose = (p) => {
    onSelect?.(p);
    // fill the input with canonical player name
    onChange?.(p.name);
    setQ(p.name);
    setOpen(false);
  };

  const onKeyDown = (e) => {
    if (!open || !results.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((i) => (i + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (hi >= 0 && results[hi]) choose(results[hi]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <input
        value={q}
        onChange={handleInput}
        onKeyDown={onKeyDown}
        disabled={disabled}
        placeholder={placeholder}
        className="w-full px-3 py-2 rounded-md bg-black/40 border border-[#2A2F36] text-white"
        autoComplete="off"
      />
      {loading && (
        <div className="absolute right-2 top-2 text-xs text-gray-400">…</div>
      )}

      {open && (results.length > 0 || (q.trim().length >= minChars && !loading)) && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-[#2A2F36] bg-[#0d0f14] shadow-lg max-h-64 overflow-auto">
          {results.length === 0 ? (
            <div className="px-3 py-2 text-sm text-gray-400">No matches</div>
          ) : results.map((p, i) => (
            <button
              key={`${p.card_id}-${i}`}
              type="button"
              onClick={() => choose(p)}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-800/60 ${
                hi === i ? "bg-gray-800/80" : ""
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="text-white font-medium">{p.name}</div>
                <div className="text-xs text-gray-400">ID {p.card_id}</div>
              </div>
              <div className="text-xs text-gray-400">Rating {p.rating}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
