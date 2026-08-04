// src/v2/pages/PlayerPage/sections/ScoresSection.jsx
//
// Prop-driven from the aggregated /summary response (data.card_scores),
// same pattern as its sibling sections - avoids a duplicate fetch of
// data the summary endpoint already includes.
import SectionCard from "../../../components/SectionCard";
import StatTile from "../../../components/StatTile";

const SCORE_LABELS = {
  investment: "Investment",
  risk: "Risk",
  confidence: "Confidence",
  recovery_probability: "Recovery Probability",
  crash_probability: "Crash Probability",
  market_regime: "Market Regime",
  momentum: "Momentum",
  supply_pressure: "Supply Pressure",
  demand_pressure: "Demand Pressure",
  opportunity: "Opportunity",
};

// investment/confidence/momentum/demand/opportunity: higher = better.
// risk/crash_probability: higher = worse (tone flips).
const NEGATIVE_TONE_SCORES = new Set(["risk", "crash_probability"]);

function toneFor(scoreType, value) {
  const bad = NEGATIVE_TONE_SCORES.has(scoreType) ? value >= 60 : value <= 35;
  const good = NEGATIVE_TONE_SCORES.has(scoreType) ? value <= 30 : value >= 65;
  if (bad) return "negative";
  if (good) return "positive";
  return "neutral";
}

export default function ScoresSection({ cardScores }) {
  if (!cardScores || cardScores.error || !cardScores.scores) {
    return (
      <SectionCard title="Analytics">
        <p className="text-xs text-[var(--v2-muted)]">Not scored yet - check back shortly.</p>
      </SectionCard>
    );
  }

  const { scores } = cardScores;

  return (
    <SectionCard title="Analytics" subtitle="rule_v1 engine">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        {Object.entries(SCORE_LABELS).map(([key, label]) => (
          <StatTile
            key={key}
            label={label}
            value={scores[key] !== undefined ? Math.round(scores[key]) : "—"}
            valueTone={scores[key] !== undefined ? toneFor(key, scores[key]) : "neutral"}
          />
        ))}
      </div>
    </SectionCard>
  );
}
