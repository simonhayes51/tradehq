// src/v2/pages/PlayerPage/sections/DealConfidenceSection.jsx
//
// Gated behind "deal_confidence" as of Phase 4 (see the v2 plan's
// Phase 1 decisions section, revisited there) -
// app/services/deal_confidence.py's compute_deal_confidence.
import SectionCard from "../../../components/SectionCard";
import StatTile from "../../../components/StatTile";
import PremiumGate from "../../../components/PremiumGate";

export default function DealConfidenceSection({ dealConfidence }) {
  if (dealConfidence?.status === 401 || dealConfidence?.status === 402) {
    return (
      <SectionCard title="Deal Confidence">
        <PremiumGate locked featureName="Deal Confidence" />
      </SectionCard>
    );
  }

  if (!dealConfidence || dealConfidence.error || dealConfidence.note === "no data") {
    return (
      <SectionCard title="Deal Confidence">
        <p className="text-xs text-[var(--v2-muted)]">Not enough data for this card yet.</p>
      </SectionCard>
    );
  }

  const c = dealConfidence.components || {};

  return (
    <SectionCard title="Deal Confidence" subtitle={`${dealConfidence.score} / 100`}>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs text-[var(--v2-muted)]">
        <StatTile label="Momentum" value={c.momentum4h} />
        <StatTile label="Regime agreement" value={c.regimeAgreement} />
        <StatTile label="Volatility risk" value={c.volRisk} />
        <StatTile label="Liquidity" value={c.liquidity} />
        <StatTile label="Spread proxy" value={c.spreadProxy} />
        <StatTile label="Support/resistance room" value={c.srRoom} />
      </div>
    </SectionCard>
  );
}
