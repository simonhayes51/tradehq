// src/v2/components/MarketStateGauge.jsx
//
// FutHub's real market_states taxonomy (bullish|bearish|illiquid|normal -
// analytics_engine.py::compute_market_regime), not the reference mockup's
// invented "Accumulation" wording. Ungated - GET /api/v2/market/regime
// mirrors the same free-teaser-numbers precedent as GET
// /api/v2/cards/{id}/scores.
import SectionCard from "./SectionCard";
import ConfidenceGauge from "./ConfidenceGauge";
import { useMarketRegime } from "../hooks/useMarketRegime";

// Plain, FUT-trader-facing labels rather than stock-market jargon - this
// product's audience trades FUT cards, not stocks, and isn't assumed to
// know terms like "bullish"/"bearish"/"illiquid".
const STATE_LABEL = { bullish: "Good Time to Buy", bearish: "Prices Dropping", illiquid: "Slow Trading", normal: "Steady Market" };
const STATE_TONE = {
  bullish: "text-[var(--v2-positive)]",
  bearish: "text-[var(--v2-negative)]",
  illiquid: "text-[var(--v2-warning)]",
  normal: "text-[var(--v2-muted)]",
};

export default function MarketStateGauge() {
  const { data, isLoading, error } = useMarketRegime();

  if (isLoading || error || !data) {
    return (
      <SectionCard title="Market State">
        <p className="text-xs text-[var(--v2-muted)]">
          {isLoading ? "Loading..." : "No market regime computed yet."}
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard title="Market State">
      <div className="flex items-center gap-4">
        <ConfidenceGauge value={data.confidence_score} size={72} />
        <div>
          <p className={`text-sm font-semibold ${STATE_TONE[data.state] || "text-[var(--v2-text)]"}`}>
            {STATE_LABEL[data.state] || data.state}
          </p>
          <p className="text-xs text-[var(--v2-muted)] mt-0.5">
            {data.indicators?.total_cards ?? 0} cards tracked
          </p>
        </div>
      </div>
    </SectionCard>
  );
}
