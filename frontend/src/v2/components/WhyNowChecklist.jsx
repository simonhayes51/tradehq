// src/v2/components/WhyNowChecklist.jsx
//
// Maps recommendation_engine.py's real market_drivers output to a
// labeled checklist row with a check/x icon based on whether that factor
// supports or opposes the given recommendation - a presentation change
// over data that already exists (RuleV1Strategy.generate()'s
// market_drivers/reasoning), not a new backend feature.
const FACTOR_LABEL = {
  discount_vs_fair_value: (v) => `Trading ${Number(v).toFixed(1)}% below fair value`,
  liquidity_sales_per_hour: (v) => `${Number(v).toFixed(1)} sales/hour liquidity`,
  trend_falling: () => "Price is in a confirmed downward trend",
};

function factorSupportsRecommendation(factor, recommendation) {
  // A falling trend is itself the reason to avoid - it's a negative
  // signal in every other context, but here it's exactly what "avoid"
  // is agreeing with.
  if (factor === "trend_falling") return recommendation === "avoid";
  if (factor.startsWith("market_event_")) return recommendation !== "avoid";
  return recommendation === "buy";
}

export default function WhyNowChecklist({ marketDrivers, recommendation }) {
  const drivers = marketDrivers || [];
  if (drivers.length === 0) return null;

  return (
    <ul className="flex flex-col gap-1.5">
      {drivers.map((d, i) => {
        const positive = factorSupportsRecommendation(d.factor, recommendation);
        const label = d.factor?.startsWith("market_event_")
          ? `Related market event: ${d.title || d.event_id}`
          : FACTOR_LABEL[d.factor]?.(d.value) ?? d.factor;
        return (
          <li key={i} className="flex items-center gap-2 text-xs">
            <span
              className={positive ? "text-[var(--v2-positive)]" : "text-[var(--v2-negative)]"}
              aria-hidden="true"
            >
              {positive ? "✓" : "✗"}
            </span>
            <span className="text-[var(--v2-text)]">{label}</span>
          </li>
        );
      })}
    </ul>
  );
}
