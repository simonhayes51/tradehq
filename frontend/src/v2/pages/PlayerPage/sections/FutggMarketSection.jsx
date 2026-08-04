// src/v2/pages/PlayerPage/sections/FutggMarketSection.jsx
//
// FUT.GG migration: additive section for the Player Page - wires the new
// GET /api/v2/players/{card_id} (+ /prices, /sales) contract, backed by
// FUT.GG data (backend built in a sibling repo, in parallel; see the
// FUTBIN -> FUT.GG migration plan). Deliberately kept separate from the
// existing AnalysisModal/toAnalysisItem machinery in PlayerPage.jsx
// (still reads the older /summary endpoint, FUTBIN-derived) rather than
// rewriting it - additive/wiring work, not a rewrite, so a legacy card
// with no FUT.GG-side row yet still gets its existing analysis modal
// untouched, and this section just shows its own honest empty state.
//
// approximate_sold_at is APPROXIMATE - age_text is the primary display
// for each sale row; approximate_sold_at is only ever shown as a
// secondary/title-attribute detail, explicitly labeled "approximate".
import { useState } from "react";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";
import { ShieldCheck, Star } from "lucide-react";
import SectionCard from "../../../components/SectionCard";
import CoinValue from "../../../components/CoinValue";
import MarketFreshness from "../../../components/MarketFreshness";
import { addWatch } from "../../../../api/watchlist";
import {
  useFutggPlayer,
  useFutggPlayerPrices,
  useFutggPlayerSales,
} from "../../../hooks/useFutggMarket";

export default function FutggMarketSection({ cardId }) {
  const playerQuery = useFutggPlayer(cardId);
  const pricesQuery = useFutggPlayerPrices(cardId);
  const salesQuery = useFutggPlayerSales(cardId);
  const [watchState, setWatchState] = useState("");

  const card = playerQuery.data;
  const notDeployedOrMissing = playerQuery.isError; // 404 (not built yet, or no FUT.GG row for this card) - either way, honest empty state, no fake data

  async function watchThisCard() {
    if (!card || watchState === "saving" || watchState === "saved") return;
    setWatchState("saving");
    try {
      // Canonical id for a FUT.GG card is source_card_id, per the
      // migration contract - falls back to card_id if the backend
      // response doesn't distinguish them. Existing legacy watchlist
      // rows (keyed off the old FUTBIN card_id) are untouched by this -
      // this only affects new watch entries created from here.
      await addWatch({
        player_name: card.name,
        card_id: String(card.source_card_id ?? card.card_id ?? cardId),
        version: card.rarity || null,
        platform: "ps",
        source: "futgg",
      });
      setWatchState("saved");
    } catch (err) {
      if (err?.response?.status === 409) setWatchState("saved");
      else setWatchState("error");
    }
  }

  if (playerQuery.isLoading) {
    return (
      <SectionCard title="FUT.GG live market" subtitle="Loading FUT.GG price and sales data…">
        <p className="text-xs text-[var(--v2-muted)]">Loading…</p>
      </SectionCard>
    );
  }

  if (notDeployedOrMissing) {
    return (
      <SectionCard title="FUT.GG live market" subtitle="FUT.GG-backed pricing for this card">
        <p className="text-xs text-[var(--v2-muted)]">
          {playerQuery.error?.response?.status === 404
            ? "No FUT.GG market data is available for this card yet."
            : "FUT.GG live market data could not be loaded right now."}
        </p>
      </SectionCard>
    );
  }

  const roi = card.expected_roi != null ? Number(card.expected_roi) * 100 : null;
  const confidence = card.confidence_score != null ? Math.round(Number(card.confidence_score) * 100) : null;
  const liquidity = card.liquidity_score != null ? Math.round(Number(card.liquidity_score) * 100) : null;

  const priceHistory = Array.isArray(pricesQuery.data) ? pricesQuery.data : pricesQuery.data?.items || [];
  const chartData = priceHistory.map((p) => ({
    time: p.captured_at ? new Date(p.captured_at).toLocaleString() : "",
    bin: p.lowest_bin,
  }));

  const sales = Array.isArray(salesQuery.data) ? salesQuery.data : salesQuery.data?.items || [];

  return (
    <div className="grid grid-cols-1 gap-4">
      <SectionCard title="FUT.GG live market" subtitle="Source: FUT.GG · card and price data">
        <div className="mb-4"><MarketFreshness priceAgeSeconds={card.price_age_seconds} capturedAt={card.current_bin_captured_at} /></div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          <Stat label="Current BIN" value={<CoinValue value={card.current_bin} />} />
          <Stat label="Fair value" value={<CoinValue value={card.fair_value} />} />
          <Stat label="Buy max" value={<CoinValue value={card.recommended_buy_max} />} />
          <Stat label="Sell target" value={<CoinValue value={card.recommended_sell_target} />} />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          <Stat label="Expected profit" value={<CoinValue value={card.expected_profit_after_tax} signed />} sub="After EA tax" />
          <Stat label="Expected ROI" value={roi == null ? "—" : `${roi > 0 ? "+" : ""}${roi.toFixed(1)}%`} />
          <Stat label="Confidence" value={confidence == null ? "—" : `${confidence}%`} />
          <Stat label="Liquidity" value={liquidity == null ? "—" : `${liquidity}%`} />
        </div>

        <div className="flex items-center gap-3 flex-wrap mb-4">
          <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold border ${riskBadgeClasses(card.risk_level)}`}>
            <ShieldCheck size={13} /> {card.risk_level || "unknown"} risk
          </span>
          {card.signal ? (
            <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold uppercase border ${signalBadgeClasses(card.signal)}`}>
              {String(card.signal).replace(/_/g, " ")}
            </span>
          ) : null}
          <button
            type="button"
            onClick={watchThisCard}
            disabled={watchState === "saving" || watchState === "saved"}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--v2-border)] px-3 py-1 text-xs font-bold text-[var(--v2-text)] hover:border-[var(--v2-accent)] hover:text-[var(--v2-accent)] disabled:opacity-60"
          >
            <Star size={13} fill={watchState === "saved" ? "currentColor" : "none"} />
            {watchState === "saved" ? "Watching" : watchState === "saving" ? "Saving…" : "Add to watchlist"}
          </button>
        </div>

        {Array.isArray(card.signal_reasons) && card.signal_reasons.length > 0 ? (
          <ul className="text-xs text-[var(--v2-muted)] grid gap-1 mb-1">
            {card.signal_reasons.map((reason, index) => (
              <li key={index} className="flex gap-2"><span className="text-[var(--v2-accent)]">•</span>{reason}</li>
            ))}
          </ul>
        ) : null}
      </SectionCard>

      <SectionCard title="BIN price history" subtitle="Live FUT.GG lowest-BIN snapshots">
        {pricesQuery.isLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading…</p>
        ) : chartData.length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No FUT.GG price history for this card yet.</p>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="futggBinFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--v2-accent)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--v2-accent)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="time" tick={{ fontSize: 10 }} stroke="var(--v2-muted)" />
              <YAxis tick={{ fontSize: 10 }} stroke="var(--v2-muted)" />
              <Tooltip contentStyle={{ background: "var(--v2-elevated)", border: "1px solid var(--v2-border)" }} />
              <Area type="monotone" dataKey="bin" stroke="var(--v2-accent)" strokeWidth={2} fill="url(#futggBinFill)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </SectionCard>

      <SectionCard title="Recent sales" subtitle="Completed FUT.GG sales — sale times are approximate">
        {salesQuery.isLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading…</p>
        ) : sales.length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No recent sales for this card.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[var(--v2-muted)]">
                  <th className="pb-2 font-semibold">Sold price</th>
                  <th className="pb-2 font-semibold">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--v2-border)]">
                {sales.map((sale, index) => (
                  <tr key={index}>
                    <td className="py-1.5"><CoinValue value={sale.sold_price} /></td>
                    <td
                      className="py-1.5 text-[var(--v2-muted)]"
                      title={sale.approximate_sold_at ? `Approximate: ${new Date(sale.approximate_sold_at).toLocaleString()}` : undefined}
                    >
                      {sale.age_text || (sale.approximate_sold_at ? "approximate time unavailable" : "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <p className="text-[11px] text-[var(--v2-muted)]">
        Data source: <a href="https://www.fut.gg" target="_blank" rel="noreferrer" className="underline">FUT.GG</a>. Sale timestamps shown above are approximate.
      </p>
    </div>
  );
}

// Plain rgba() literals rather than Tailwind's arbitrary-value opacity
// modifier (bg-[var(--x)]/10) - that modifier needs the referenced
// custom property to hold a raw color channel triple, not the hex
// strings tokens.css actually defines, so it wouldn't apply correctly.
function riskBadgeClasses(riskLevel) {
  const level = String(riskLevel || "unknown").toLowerCase();
  if (level === "low") return "border-[rgba(57,255,138,0.4)] bg-[rgba(57,255,138,0.1)] text-[var(--v2-accent)]";
  if (level === "medium") return "border-[rgba(242,201,76,0.4)] bg-[rgba(242,201,76,0.1)] text-[var(--v2-warning)]";
  if (level === "high") return "border-[rgba(255,92,92,0.4)] bg-[rgba(255,92,92,0.1)] text-[var(--v2-negative)]";
  return "border-[var(--v2-border)] bg-[var(--v2-elevated)] text-[var(--v2-muted)]";
}

function signalBadgeClasses(signal) {
  const s = String(signal || "").toLowerCase();
  if (s === "strong_buy" || s === "buy") return "border-[rgba(57,255,138,0.4)] bg-[rgba(57,255,138,0.1)] text-[var(--v2-accent)]";
  if (s === "sell" || s === "avoid") return "border-[rgba(255,92,92,0.4)] bg-[rgba(255,92,92,0.1)] text-[var(--v2-negative)]";
  return "border-[var(--v2-border)] bg-[var(--v2-elevated)] text-[var(--v2-muted)]";
}

function Stat({ label, value, sub }) {
  return (
    <div className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-3">
      <div className="text-[9px] font-bold tracking-wider uppercase text-[var(--v2-muted)] mb-1.5">{label}</div>
      <div className="text-sm font-bold text-[var(--v2-text)]">{value}</div>
      {sub ? <div className="text-[10px] text-[var(--v2-muted)] mt-0.5">{sub}</div> : null}
    </div>
  );
}
