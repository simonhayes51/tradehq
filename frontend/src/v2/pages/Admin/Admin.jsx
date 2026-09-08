// src/v2/pages/Admin/Admin.jsx
//
// Admin-only. Auth is checked once here (not per-tab): every tab's own
// hook still hits a require_admin-gated endpoint independently (defense
// in depth, matching the plan's principle of never trusting a
// client-side guess over what the server would actually decide), but
// this page avoids separate "Admin access required" flashes by gating on
// the same /api/admin/pipeline/health call the first tab needs anyway.
//
// User Management and API key tier changes are NOT duplicated here -
// src/pages/AdminUsers.jsx (v1, at /admin/users) already owns that
// live, working flow. This area only covers the v2-specific views that
// don't exist anywhere yet.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAdminPipelineHealth } from "../../hooks/useAdminPipelineHealth";
import PipelineHealthTab from "./tabs/PipelineHealthTab";
import CollectorStatusTab from "./tabs/CollectorStatusTab";
import DataQualityTab from "./tabs/DataQualityTab";
import CalibrationTab from "./tabs/CalibrationTab";
import InvestmentShadowTab from "./tabs/InvestmentShadowTab";
import SbcReinforcementTab from "./tabs/SbcReinforcementTab";
import MarketEventsTab from "./tabs/MarketEventsTab";
import NextPromoTab from "./tabs/NextPromoTab";
import SubscriptionsTab from "./tabs/SubscriptionsTab";
import ApiUsageTab from "./tabs/ApiUsageTab";
import ConversionTab from "./tabs/ConversionTab";
import PlayerCardsTab from "./tabs/PlayerCardsTab";
import AdminSbcImports from "../AdminSbcImports/AdminSbcImports";

const TABS = [
  // First: it is the question the launch is actually judged on.
  { key: "conversion", label: "Conversion", Component: ConversionTab },
  { key: "pipeline", label: "Pipeline Health", Component: PipelineHealthTab },
  { key: "data-quality", label: "Data Quality", Component: DataQualityTab },
  { key: "calibration", label: "Calibration", Component: CalibrationTab },
  { key: "investment-shadow", label: "Investments", Component: InvestmentShadowTab },
  { key: "collectors", label: "Collector Status", Component: CollectorStatusTab },
  { key: "sbc-imports", label: "SBC Imports", Component: AdminSbcImports },
  { key: "sbc-market", label: "SBC Market", Component: SbcReinforcementTab },
  { key: "player-cards", label: "Player Cards", Component: PlayerCardsTab },
  { key: "market-events", label: "Market Events", Component: MarketEventsTab },
  { key: "next-promo", label: "Next Promo", Component: NextPromoTab },
  { key: "subscriptions", label: "Subscriptions", Component: SubscriptionsTab },
  { key: "api-usage", label: "API Usage", Component: ApiUsageTab },
];

export default function Admin() {
  const [tab, setTab] = useState("conversion");
  const { isLoading, error } = useAdminPipelineHealth();

  if (isLoading) {
    return <div role="status" className="p-6 text-sm text-[var(--v2-muted)]">Loading...</div>;
  }

  if (error) {
    const status = error?.response?.status;
    return (
      <div role="alert" className="p-6 text-sm text-[var(--v2-negative)]">
        {status === 403 ? "Admin access required." : "Couldn't load the admin area."}
      </div>
    );
  }

  const Active = TABS.find((t) => t.key === tab)?.Component || PipelineHealthTab;

  return (
    <div className="p-6 max-w-4xl mx-auto flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Admin</h1>
        <Link to="/admin/users" className="text-xs text-[var(--v2-muted)] hover:text-[var(--v2-accent)]">
          User Management (v1) &rarr;
        </Link>
      </div>

      <div role="tablist" aria-label="Admin sections" className="flex items-center gap-1 border-b border-[var(--v2-border)] overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            id={`admin-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`admin-tabpanel-${t.key}`}
            onClick={() => setTab(t.key)}
            className={`px-3 py-2 text-xs font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${
              tab === t.key
                ? "border-[var(--v2-accent)] text-[var(--v2-text)]"
                : "border-transparent text-[var(--v2-muted)] hover:text-[var(--v2-text)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`admin-tabpanel-${tab}`} aria-labelledby={`admin-tab-${tab}`}>
        <Active />
      </div>
    </div>
  );
}
