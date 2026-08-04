// src/pages/UndervaluedBoard.jsx
// "Undervalued Right Now" — the board of cards whose live BIN sits below
// what they're ACTUALLY selling for (real completed sales, last 24h).
// Pro feature; free users get a 3-row teaser with verdicts only.
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Flame, RefreshCcw, TrendingDown } from "lucide-react";
import { useEntitlements } from "../context/EntitlementsContext";
import { useUndervalued, useUndervaluedTeaser } from "../hooks/useFairValue";

const LIME = "#91db32";

const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString());

function Row({ item }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-900/60 p-3 hover:border-gray-600 transition">
      {item.image_url ? (
        <img src={item.image_url} alt="" className="w-10 h-12 object-contain shrink-0" loading="lazy" />
      ) : (
        <div className="w-10 h-12 rounded bg-gray-800 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-white truncate">{item.name}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-300">
            {item.rating} · {item.version || "Standard"}
          </span>
        </div>
        <div className="text-[11px] text-gray-400 mt-0.5">
          {item.sales_24h} real sales tracked · {item.sales_per_hour_24h}/hr liquidity
        </div>
      </div>
      <div className="text-right shrink-0">
        <div className="text-sm font-extrabold tabular-nums" style={{ color: LIME }}>
          -{item.discount_pct}%
        </div>
        <div className="text-[11px] text-gray-400 tabular-nums">
          BIN {fmt(item.current_bin)} · worth {fmt(item.fair_value_24h)}
        </div>
      </div>
    </div>
  );
}

function TeaserRow({ item }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-900/60 p-3 opacity-90">
      {item.image_url ? (
        <img src={item.image_url} alt="" className="w-10 h-12 object-contain shrink-0" loading="lazy" />
      ) : (
        <div className="w-10 h-12 rounded bg-gray-800 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <span className="text-sm font-bold text-white truncate">{item.name}</span>
        <div className="text-[11px] text-gray-400">{item.sales_24h} real sales tracked today</div>
      </div>
      <span
        className="text-[10px] font-bold px-2 py-1 rounded-full"
        style={{ background: "rgba(145,219,50,0.15)", color: LIME }}
      >
        {item.verdict === "steal" ? "🔥 STEAL" : "UNDER VALUE"} 🔒
      </span>
    </div>
  );
}

export default function UndervaluedBoard() {
  const { features } = useEntitlements() || {};
  const unlocked = Array.isArray(features) && features.includes("undervalued_board");

  const [maxPrice, setMaxPrice] = useState("");
  const params = {
    limit: 30,
    ...(maxPrice ? { max_price: Number(maxPrice) } : {}),
  };

  const board = useUndervalued(params);
  const teaser = useUndervaluedTeaser();

  return (
    <div className="max-w-3xl mx-auto px-3 pb-24 pt-4">
      <div className="flex items-center gap-2">
        <Flame size={22} style={{ color: LIME }} />
        <h1 className="text-xl font-extrabold text-white">Undervalued Right Now</h1>
      </div>
      <p className="mt-1 text-sm text-gray-400">
        Cards listed <span className="text-white font-semibold">below what they're actually selling for</span> —
        straight from real completed sales, refreshed every few minutes. Not vibes. Receipts.
      </p>

      {unlocked ? (
        <>
          <div className="mt-4 flex items-center gap-2">
            <input
              inputMode="numeric"
              placeholder="Max budget (coins)"
              value={maxPrice}
              onChange={(e) => setMaxPrice(e.target.value.replace(/[^0-9]/g, ""))}
              className="flex-1 sm:flex-none sm:w-48 px-3 py-2 rounded-xl bg-gray-900 border border-gray-800 text-sm text-white placeholder-gray-500"
            />
            <button
              onClick={() => board.refetch()}
              className="px-3 py-2 rounded-xl border border-gray-800 text-gray-300 text-sm inline-flex items-center gap-1 active:scale-[0.98]"
            >
              <RefreshCcw size={14} /> Refresh
            </button>
          </div>

          <div className="mt-4 space-y-2">
            {board.isLoading && <p className="text-sm text-gray-500">Scanning the market…</p>}
            {board.isError && (
              <p className="text-sm text-red-400">Couldn't load the board — try again in a sec.</p>
            )}
            {board.data?.items?.length === 0 && (
              <p className="text-sm text-gray-500">
                Nothing juicy at these filters right now. Market's efficient today — check back after content drops.
              </p>
            )}
            {board.data?.items?.map((item) => (
              <Row key={item.card_id} item={item} />
            ))}
          </div>
        </>
      ) : (
        <div className="mt-4">
          <div className="space-y-2">
            {teaser.data?.items?.map((item) => (
              <TeaserRow key={item.card_id} item={item} />
            ))}
          </div>
          <div className="mt-4 rounded-2xl border border-gray-800 bg-gray-900/70 p-5 text-center">
            <TrendingDown size={22} className="mx-auto" style={{ color: LIME }} />
            <p className="mt-2 text-sm font-extrabold text-white">
              The full board is live. The discounts are real.
            </p>
            <p className="mt-1 text-xs text-gray-400">
              Exact prices, discount %, liquidity and 30 picks at a time — Pro only.
            </p>
            <Link
              to="/billing"
              className="mt-3 inline-block px-5 py-2.5 rounded-xl text-sm font-bold text-black active:scale-[0.98] transition"
              style={{ background: LIME }}
            >
              Go Pro — see every steal →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
