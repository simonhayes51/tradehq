// src/v2/pages/PlayerPage/sections/RecommendationSection.jsx
//
// Prop-driven from the aggregated /summary response (data.recommendation).
// The badge/gauge/ROI verdict itself is already shown once, prominently,
// in HeaderSection's hero - this section is deliberately just the "WHY
// NOW?" reasoning breakdown so it doesn't repeat the same three numbers
// a second time, matching the reference design's separate WHY NOW panel.
import SectionCard from "../../../components/SectionCard";
import PremiumGate from "../../../components/PremiumGate";
import WhyNowChecklist from "../../../components/WhyNowChecklist";
import { reasoningFromV1_2 } from "../../../lib/format";

export default function RecommendationSection({ recommendation }) {
  // _safe() on the backend turns a require_feature() 401/402 into
  // {error, status} instead of letting it 500 the whole summary - that
  // shape means "locked," distinct from a genuine "not scored yet."
  if (recommendation?.status === 401 || recommendation?.status === 402) {
    return (
      <SectionCard title="Why now?">
        <PremiumGate locked featureName="AI Recommendations" />
      </SectionCard>
    );
  }

  if (!recommendation || recommendation.error) {
    return (
      <SectionCard title="Why now?">
        <p className="text-xs text-[var(--v2-muted)]">No recommendation for this card yet.</p>
      </SectionCard>
    );
  }

  const data = recommendation;
  // reasoning/market_drivers are rule_v1-only columns V1.2 never
  // populates (see recommendation_engine_v2.py) - fall back to
  // deriving real text from the V1.2 status/qualified_strategies/
  // failed_gate_reasons/held_decision_reasons fields the row does
  // carry, rather than silently rendering an empty panel.
  const reasoningText = data.reasoning || reasoningFromV1_2(data);

  return (
    <SectionCard title="Why now?" subtitle={data.engine_version}>
      {reasoningText && <p className="text-xs text-[var(--v2-muted)] mb-3">{reasoningText}</p>}
      <WhyNowChecklist marketDrivers={data.market_drivers} recommendation={data.recommendation} />
    </SectionCard>
  );
}
