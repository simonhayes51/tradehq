// src/v2/pages/PlayerPage/sections/MarketMetricsSection.jsx
//
// Real, ungated numbers from GET /{card_id}/market-metrics (see
// app/routers/players.py) - confirmed shape via a real end-to-end test:
// currentBin, realPrice, liquidity, volatility, taxAwareMargin.
import SectionCard from "../../../components/SectionCard";
import StatTile from "../../../components/StatTile";
import { formatCoins, formatPct } from "../../../lib/format";

export default function MarketMetricsSection({ marketMetrics }) {
  if (!marketMetrics || marketMetrics.error) {
    return (
      <SectionCard title="Market Metrics">
        <p className="text-xs text-[var(--v2-muted)]">Not enough data for this card yet.</p>
      </SectionCard>
    );
  }

  const { currentBin, realPrice, liquidity, volatility, taxAwareMargin } = marketMetrics;

  return (
    <SectionCard title="Market Metrics" subtitle="Real numbers, free tier">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
        <StatTile label="Current BIN (PS)" value={formatCoins(currentBin?.ps)} />
        <StatTile label="Median sold (24h)" value={formatCoins(realPrice?.medianSold24h)} />
        <StatTile label="Sales / hour (24h)" value={liquidity?.salesPerHour24h ?? "—"} />
        <StatTile
          label="Volatility (24h)"
          value={formatPct(volatility?.coefficientOfVariation24h * 100)}
        />
        <StatTile
          label="Est. margin"
          value={formatCoins(taxAwareMargin?.estMarginCoins)}
          delta={formatPct(taxAwareMargin?.estMarginPct, { withSign: true })}
          deltaTone={taxAwareMargin?.estMarginPct >= 0 ? "positive" : "negative"}
        />
        <StatTile label="Sample size (24h)" value={realPrice?.sampleSize24h ?? 0} />
      </div>
    </SectionCard>
  );
}
