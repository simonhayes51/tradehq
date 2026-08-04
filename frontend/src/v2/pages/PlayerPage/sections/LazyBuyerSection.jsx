// src/v2/pages/PlayerPage/sections/LazyBuyerSection.jsx
import SectionCard from "../../../components/SectionCard";
import StatTile from "../../../components/StatTile";
import { formatPct } from "../../../lib/format";

export default function LazyBuyerSection({ lazyBuyerScore }) {
  if (!lazyBuyerScore || lazyBuyerScore.error) {
    return (
      <SectionCard title="Lazy Buyer Odds">
        <p className="text-xs text-[var(--v2-muted)]">Not enough data for this card yet.</p>
      </SectionCard>
    );
  }

  if (!lazyBuyerScore.available) {
    return (
      <SectionCard title="Lazy Buyer Odds">
        <p className="text-xs text-[var(--v2-muted)]">
          Not enough sales history yet to compute this.
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="Lazy Buyer Odds"
      subtitle="How often sales clear above the live BIN at the moment of sale"
    >
      <div className="grid grid-cols-2 gap-4">
        {/* lboRate7d/poolAvgLboRate7d/confidenceScore are already 0-100
            percentages server-side (see players.py: round(x * 100, 1)) -
            not fractions, so formatPct is called directly on them. */}
        <StatTile label="This card (7d)" value={formatPct(lazyBuyerScore.lboRate7d)} />
        <StatTile label="Pool average (7d)" value={formatPct(lazyBuyerScore.poolAvgLboRate7d)} />
        <StatTile label="Confidence" value={formatPct(lazyBuyerScore.confidenceScore)} />
        <StatTile label="Sample size (7d)" value={lazyBuyerScore.sampleSize7d} />
      </div>
    </SectionCard>
  );
}
