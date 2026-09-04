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
import { useMemo } from "react";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";
import SectionCard from "../../../components/SectionCard";
import CoinValue from "../../../components/CoinValue";
import { formatAge } from "../../../components/MarketFreshness";
import { compactCoins, evenTicks, humaniseSpan, tickFormatterForSpan } from "../../../lib/chartFormat";
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
  // 404 (not built yet, or no FUT.GG row for this card) - either way an
  // honest empty state, never invented data.
  const notDeployedOrMissing = playerQuery.isError;

  const priceData = pricesQuery.data;
  // The BIN chart was drawing an ordinal (category) X axis whose every
  // value was a full `toLocaleString()` datetime - "04/09/2026, 14:32:11"
  // - one label per snapshot. recharts renders every category label on
  // an ordinal axis, so a few dozen points overprinted into one
  // continuous string of digits along the bottom, which is what made the
  // chart look empty: the plot was there, the axis had eaten it.
  //
  // It also plotted the rows in the order the API returned them, which
  // is newest-first (get_price_history ORDERs DESC), so the series ran
  // right-to-left - i.e. a rise read as a fall.
  const chartData = useMemo(() => (
    (Array.isArray(priceData) ? priceData : priceData?.items || [])
      .map((p) => ({
        time: p.captured_at ? new Date(p.captured_at).getTime() : NaN,
        bin: Number(p.lowest_bin),
      }))
      .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.bin) && point.bin > 0)
      .sort((a, b) => a.time - b.time)
  ), [priceData]);

  // A fixed, small number of evenly spaced ticks cannot overcrowd,
  // however the snapshots happen to be distributed in time.
  const priceTicks = useMemo(() => evenTicks(chartData), [chartData]);

  const priceSpanMs = chartData.length > 1 ? chartData[chartData.length - 1].time - chartData[0].time : 0;
  // Same rule as the sales chart: label by the span the data actually
  // covers, not by a nominal range. A whole window inside one day drawn
  // with day labels prints the same string five times.
  const formatPriceTick = tickFormatterForSpan(priceSpanMs);

  const allSales = Array.isArray(salesQuery.data) ? salesQuery.data : salesQuery.data?.items || [];
  // The endpoint serves up to MAX_CHART_SALES (500) rows because the chart
  // needs them. A table does not: past a couple of dozen rows it is an
  // unreadable wall that pushes the rest of the page off-screen, and every
  // row beyond the first few tells the user nothing new.
  const sales = allSales.slice(0, RECENT_SALES_ROWS);

  // Early returns AFTER every hook - the useMemo calls above must run on
  // every render or React's hook order breaks between the loading and
  // loaded states.
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

  return (
    <div className="grid grid-cols-1 gap-4">
      {/* The "Live market" card that used to sit here is gone. Every
          part of it was a second copy of something the analysis panel
          directly above renders from the same fields: the price
          freshness line, the risk badge, the signal badge (which is
          where the second "WATCH" came from, beside the verdict pill),
          and a second "Add to watchlist" button. Duplication removed
          structurally - the elements no longer exist here - rather than
          hidden with CSS, so there is one recommendation-and-action
          area on this page and one place each fact is stated. */}
      <SectionCard title="BIN price history" subtitle="Live lowest-BIN snapshots">
        {pricesQuery.isLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading…</p>
        ) : pricesQuery.isError ? (
          <p className="text-xs text-[var(--v2-muted)]">Price history could not be loaded right now.</p>
        ) : chartData.length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">
            We haven&apos;t recorded any BIN snapshots for this card yet. Prices are collected on a
            per-card schedule, so a quiet card can go a while between readings.
          </p>
        ) : chartData.length === 1 ? (
          // A single point is not a history. Drawing it as an area chart
          // produced a flat line across the full width, which reads as
          // "this price has not moved in the window" - a claim we cannot
          // make from one reading.
          <p className="text-xs text-[var(--v2-muted)]">
            One BIN snapshot so far, <CoinValue value={chartData[0].bin} /> at{" "}
            {new Date(chartData[0].time).toLocaleString("en-GB")}. A trend needs at least two.
          </p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="futggBinFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--v2-accent)" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="var(--v2-accent)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="time"
                  type="number"
                  scale="time"
                  domain={["dataMin", "dataMax"]}
                  ticks={priceTicks}
                  tickFormatter={formatPriceTick}
                  tick={{ fontSize: 10 }}
                  stroke="var(--v2-muted)"
                  tickMargin={8}
                />
                <YAxis
                  tick={{ fontSize: 10 }}
                  stroke="var(--v2-muted)"
                  width={58}
                  domain={["auto", "auto"]}
                  tickFormatter={compactCoins}
                />
                <Tooltip
                  contentStyle={{ background: "var(--v2-elevated)", border: "1px solid var(--v2-border)" }}
                  labelFormatter={(value) => new Date(value).toLocaleString("en-GB")}
                  formatter={(value) => [Number(value).toLocaleString("en-GB"), "Lowest BIN"]}
                />
                <Area type="monotone" dataKey="bin" stroke="var(--v2-accent)" strokeWidth={2} fill="url(#futggBinFill)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
            <p className="pt-2 text-[11px] text-[var(--v2-muted)]">
              {chartData.length} snapshots over {humaniseSpan(priceSpanMs)}.
            </p>
          </>
        )}
      </SectionCard>

      <SectionCard
        title={
          <span className="inline-flex items-center gap-1.5">
            Recent completed sales
            <span
              className="inline-flex h-[15px] w-[15px] items-center justify-center rounded-full border border-[var(--v2-border)] text-[9px] text-[var(--v2-muted)] cursor-help"
              tabIndex={0}
              role="img"
              aria-label="Where this data comes from"
              title="Sourced from FUT.GG's public sale listings, not EA's own trade history. Sale times aren't exact - they're recorded as a relative age (e.g. '2h ago') at the moment we captured them, then converted to an approximate timestamp. Treat times as roughly right, not to-the-minute."
            >
              i
            </span>
          </span>
        }
        subtitle="Real completed sales, but sale times are approximate — see the (i) for why"
      >
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
