// src/v2/pages/PlayerPage/sections/FairValueSection.jsx
//
// summary.fair_value is GET /api/market/fair-value/{card_id}'s own
// response, called server-side by /api/v2/players/{id}/summary - it
// already decides teaser vs full row (row.locked) and the
// data_quality_suspect special case itself. This never re-derives
// access client-side.
import SectionCard from "../../../components/SectionCard";
import StatTile from "../../../components/StatTile";
import PremiumGate from "../../../components/PremiumGate";
import { formatCoins, formatPct } from "../../../lib/format";

const VERDICT_LABEL = {
  steal: "Steal",
  under: "Undervalued",
  fair: "Fair value",
  overpriced: "Overpriced",
  falling: "Falling knife",
  unknown: "Unknown",
};

export default function FairValueSection({ fairValue }) {
  if (!fairValue || fairValue.error) {
    return (
      <SectionCard title="Fair Value">
        <p className="text-xs text-[var(--v2-muted)]">No fair-value data for this card yet.</p>
      </SectionCard>
    );
  }

  if (fairValue.data_quality_suspect) {
    return (
      <SectionCard title="Fair Value">
        <p className="text-xs text-[var(--v2-muted)]">{fairValue.message}</p>
      </SectionCard>
    );
  }

  return (
    <SectionCard title="Fair Value">
      <PremiumGate locked={fairValue.locked} featureName="Fair Value">
        {fairValue.locked ? (
          <div>
            <p className="text-sm font-medium text-[var(--v2-text)]">
              {VERDICT_LABEL[fairValue.verdict] || fairValue.verdict}
            </p>
            <p className="text-xs text-[var(--v2-muted)] mt-1">
              {fairValue.sales_24h} sales in the last 24h
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <StatTile label="Fair value (24h)" value={formatCoins(fairValue.fair_value_24h)} />
            <StatTile label="Discount" value={formatPct(fairValue.discount_pct, { withSign: true })} />
            <StatTile label="Current BIN" value={formatCoins(fairValue.current_bin)} />
            <StatTile label="BIN z-score" value={fairValue.bin_zscore_24h?.toFixed?.(2) ?? "—"} />
          </div>
        )}
      </PremiumGate>
    </SectionCard>
  );
}
