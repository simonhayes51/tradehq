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
// approximate_sold_at is APPROXIMATE, but it is nonetheless the only
// field here that carries an absolute time, and the "Recent sales" table
// below MUST render from it.
//
// This file used to say the opposite - that age_text was the primary
// display - and that was wrong in a way that actively lied to the user.
// age_text ("2 minutes ago") is the relative string FUT.GG rendered at
// SCRAPE time; auto_sync/futgg_common.py stores it verbatim alongside
// approximate_sold_at = captured_at - age_seconds. The string is frozen
// the moment it is written, so a row scraped four days ago still reads
// "2 minutes ago" forever.
//
// Seen in production on card 218342893: this table showed eleven sales
// from "2 minutes ago" to "25 minutes ago" while the analysis panel on
// the same page correctly reported the newest completed sale as 92 hours
// old and the 24h chart correctly drew nothing. All three were reading
// the same rows; only this table was reading the frozen label.
//
// age_text is kept as a title-attribute detail (it is what the source
// actually said) but never as the displayed age.
import { useState } from "react";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";
import { ShieldCheck, Star } from "lucide-react";
import SectionCard from "../../../components/SectionCard";
import CoinValue from "../../../components/CoinValue";
import MarketFreshness, { formatAge } from "../../../components/MarketFreshness";
import { addWatch } from "../../../../api/watchlist";
import { riskLabel, versionLabel } from "../../../lib/format";
import {
  useFutggPlayer,
  useFutggPlayerPrices,
  useFutggPlayerSales,
} from "../../../hooks/useFutggMarket";

// Enough rows to read the recent shape of the market without turning the
// page into a scroll trap. The chart already covers depth.
export const RECENT_SALES_ROWS = 25;

// Age as of NOW, from the only absolute field on the row. Never age_text -
// see this file's header. Falls back to the frozen string only when there
// is no timestamp at all, and says so, rather than presenting a stale
// relative label as if it were current.
export function saleAgeLabel(sale) {
  if (sale.approximate_sold_at) {
    const soldAt = new Date(sale.approximate_sold_at).getTime();
    if (Number.isFinite(soldAt)) {
      return formatAge((Date.now() - soldAt) / 1000);
    }
  }
  return sale.age_text ? `${sale.age_text} (at scrape time)` : "approximate time unavailable";
}

export function saleTitle(sale) {
  const parts = [];
  if (sale.approximate_sold_at) {
    parts.push(`Approximate: ${new Date(sale.approximate_sold_at).toLocaleString()}`);
  }
  if (sale.age_text) parts.push(`Recorded as "${sale.age_text}" at the time of capture`);
  return parts.length ? parts.join(" · ") : undefined;
}

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
        version: card.rarity ? versionLabel(card.rarity) : null,
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
      <SectionCard title="Live market" subtitle="Loading price and sales data…">
        <p className="text-xs text-[var(--v2-muted)]">Loading…</p>
      </SectionCard>
    );
  }

  if (notDeployedOrMissing) {
    return (
      <SectionCard title="Live market" subtitle="Live pricing for this card">
        <p className="text-xs text-[var(--v2-muted)]">
          {playerQuery.error?.response?.status === 404
            ? "No market data is available for this card yet."
            : "Live market data could not be loaded right now."}
        </p>
      </SectionCard>
    );
  }

  const priceHistory = Array.isArray(pricesQuery.data) ? pricesQuery.data : pricesQuery.data?.items || [];
  const chartData = priceHistory.map((p) => ({
    time: p.captured_at ? new Date(p.captured_at).toLocaleString() : "",
    bin: p.lowest_bin,
  }));

  const allSales = Array.isArray(salesQuery.data) ? salesQuery.data : salesQuery.data?.items || [];
  // The endpoint serves up to MAX_CHART_SALES (500) rows because the chart
  // needs them. A table does not: past a couple of dozen rows it is an
  // unreadable wall that pushes the rest of the page off-screen, and every
  // row beyond the first few tells the user nothing new.
  const sales = allSales.slice(0, RECENT_SALES_ROWS);

  return (
    <div className="grid grid-cols-1 gap-4">
      <SectionCard title="Live market" subtitle="Card and price data">
        {/* The numbers behind this card (fair value, confidence, liquidity,
            sales median, recent movement...) live in the Advanced
            rationale panel above (AnalysisModal, via AdvancedRationale) -
            duplicating that same 8-stat grid here just repeated it a
            second time on the same page. This section keeps only what's
            unique to it: freshness, the risk/signal badges, watching,
            and the chart/table evidence below. */}
        <div className="mb-4"><MarketFreshness priceAgeSeconds={card.price_age_seconds} capturedAt={card.current_bin_captured_at} /></div>

        <div className="flex items-center gap-3 flex-wrap mb-4">
          <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold border ${riskBadgeClasses(card.risk_level)}`}>
            <ShieldCheck size={13} /> {riskLabel(card.risk_level)}
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
      </SectionCard>

      <SectionCard title="BIN price history" subtitle="Live lowest-BIN snapshots">
        {pricesQuery.isLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading…</p>
        ) : chartData.length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No price history for this card yet.</p>
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

      <SectionCard title="Recent sales" subtitle="Completed sales — sale times are approximate">
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
                      title={saleTitle(sale)}
                    >
                      {saleAgeLabel(sale)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {allSales.length > sales.length ? (
              <p className="pt-2 text-[11px] text-[var(--v2-muted)]">
                Showing the {sales.length} most recent of {allSales.length} completed sales.
              </p>
            ) : null}
          </div>
        )}
      </SectionCard>

      <p className="text-[11px] text-[var(--v2-muted)]">
        Sale timestamps shown above are approximate.
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
