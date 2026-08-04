// src/v2/pages/SbcEventDetail/sections/ImpactSection.jsx
//
// Gated behind "sbc_impact_predictions" as of Phase 4. The hook uses
// react-query, so a 401/402 lands in the query's `error`, not `data` -
// SbcEventDetail.jsx passes that status through as a prop rather than
// this section re-deriving it, matching FairValueSection's principle
// of trusting a server-decided signal over a client-side guess.
import { Link } from "react-router-dom";
import SectionCard from "../../../components/SectionCard";
import PremiumGate from "../../../components/PremiumGate";
import CardArtThumb from "../../../components/CardArtThumb";
import { formatCoins, formatPct } from "../../../lib/format";

const RELATION_LABEL = {
  fodder_demand: "Fodder demand",
  reward_supply: "Reward supply",
  meta_shift: "Meta shift",
  requirement_target: "Requirement target",
};

export default function ImpactSection({ impact, status }) {
  if (status === 401 || status === 402) {
    return (
      <SectionCard title="Market Impact">
        <PremiumGate locked featureName="SBC Impact Predictions" />
      </SectionCard>
    );
  }

  const items = impact?.items || [];

  if (items.length === 0) {
    return (
      <SectionCard title="Market Impact">
        <p className="text-xs text-[var(--v2-muted)]">
          No measured impact yet - this is computed after the event has been live for a while.
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard title="Market Impact">
      <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
        {items.map((it, i) => (
          <li key={i} className="flex items-center gap-3 py-2 text-xs">
            <CardArtThumb card={it} widthClass="w-9" />
            <div className="flex-1 min-w-0">
              <Link to={`/v2/players/${it.card_id}`} className="font-medium hover:text-[var(--v2-accent)]">
                {it.name || it.card_id}
              </Link>
              <p className="text-[var(--v2-muted)]">{RELATION_LABEL[it.relation] || it.relation}</p>
            </div>
            <div className="text-right">
              <p>
                {formatCoins(it.price_before)} → {formatCoins(it.price_after)}
              </p>
              {it.price_change_pct !== null && it.price_change_pct !== undefined && (
                <p className={it.price_change_pct >= 0 ? "text-[var(--v2-positive)]" : "text-[var(--v2-negative)]"}>
                  {formatPct(it.price_change_pct, { withSign: true })}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
