import SectionCard from "../../../components/SectionCard";
import { useAdminRoadmapReadiness } from "../../../hooks/useAdminRoadmapReadiness";

const TONES = {
  READY: "text-[var(--v2-positive)]",
  REVIEW: "text-[var(--v2-accent)]",
  WAITING: "text-[var(--v2-muted)]",
  BLOCKED: "text-[var(--v2-negative)]",
};

function StatusCard({ name, status, reason, next }) {
  return (
    <div className="rounded-lg border border-[var(--v2-border)] bg-black/20 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="font-medium text-sm">{name}</div>
        <div className={`text-[10px] font-semibold uppercase tracking-wide ${TONES[status] || TONES.WAITING}`}>{status}</div>
      </div>
      <div className="mt-2 text-xs text-[var(--v2-muted)]">{reason}</div>
      <div className="mt-2 text-[10px] text-[var(--v2-text)]"><span className="text-[var(--v2-muted)]">Next:</span> {next}</div>
    </div>
  );
}

function classify(data) {
  const intelligence = data?.intelligence || {};
  const cache = intelligence.dashboard_market_cache || {};
  const shadow = data?.salesShadow || {};
  const pickup = shadow.pickup_analysis || {};
  const calibration = data?.calibration || {};
  const investments = data?.investments || {};
  const sbc = data?.sbc || {};
  const trades = data?.openTrades?.trades || [];

  const coreReady = intelligence.status === "ok"
    && Number(intelligence.current_coverage_pct || 0) >= 90
    && cache.live_fallback_enabled === false;

  const analyzedTrades = trades.filter((t) => t.current_position_analysis && t.current_evaluated_at).length;
  const heldReady = trades.length === 0 || analyzedTrades === trades.length;

  const matureShadow = Number(pickup.measured_shadow_card_events || 0);
  const repeatedMisses = pickup.repeated_missed_cards || [];
  let salesStatus = "WAITING";
  let salesReason = `${matureShadow} mature shadow card-events collected.`;
  let salesNext = "Keep the 60/300s live scheduler unchanged while evidence accumulates.";
  if (shadow.status === "ok" && matureShadow >= 100) {
    if (repeatedMisses.length) {
      salesStatus = "REVIEW";
      salesReason = `${matureShadow} mature events with ${repeatedMisses.length} repeatedly missed card(s).`;
      salesNext = "Review reserving some existing slots for user-driven/held-position demand. Do not increase total budget.";
    } else {
      salesStatus = "READY";
      salesReason = `${matureShadow} mature events and no repeated misses requiring intervention.`;
      salesNext = "Leave the live scheduler unchanged; continue monitoring.";
    }
  }

  const calibrationBlockers = calibration.blockers || [];
  const calibrationCandidates = calibration.review_candidates || [];
  let calibrationStatus = "WAITING";
  let calibrationReason = calibrationBlockers.length
    ? `Blocked by: ${calibrationBlockers.map((b) => String(b).replaceAll("_", " ")).join(", ")}.`
    : "Outcome evidence is still accumulating.";
  let calibrationNext = "Do not change live thresholds yet.";
  if (calibration.version_integrity?.config_clean === false) {
    calibrationStatus = "BLOCKED";
    calibrationReason = "Engine-version/config contamination detected.";
    calibrationNext = "Resolve version integrity before reviewing any threshold move.";
  } else if (calibration.calibration_allowed && calibrationCandidates.length) {
    calibrationStatus = "REVIEW";
    calibrationReason = `${calibrationCandidates.length} statistically defensible threshold review candidate(s).`;
    calibrationNext = "Review manually, record a calibration decision, and bump engine version for any accepted change.";
  } else if (calibration.calibration_allowed && !calibrationCandidates.length) {
    calibrationStatus = "READY";
    calibrationReason = "Calibration is valid and currently justifies no threshold change.";
    calibrationNext = "Keep current thresholds and continue grading outcomes.";
  }

  const engine = investments.engine || {};
  const decisions = investments.decisions || [];
  const graded72 = decisions.reduce((sum, row) => sum + Number(row.graded_72h || 0), 0);
  let investmentStatus = "WAITING";
  let investmentReason = engine.mode === "SHADOW"
    ? `Shadow mode active; ${graded72} decision outcomes have matured to 72h.`
    : `Investment engine mode is ${engine.mode || "UNKNOWN"}.`;
  let investmentNext = "Wait for FC27 priced cohorts and graded shadow outcomes; keep publishing OFF.";
  if (!engine.enabled) {
    investmentStatus = "BLOCKED";
    investmentReason = "Investment Engine is disabled.";
    investmentNext = "Enable calculation in SHADOW only; keep publishing OFF.";
  } else if (engine.publishing) {
    investmentStatus = "BLOCKED";
    investmentReason = "Investment publishing is enabled before the shadow evidence gate is complete.";
    investmentNext = "Disable publishing and return to SHADOW.";
  } else if (graded72 >= 30) {
    investmentStatus = "REVIEW";
    investmentReason = `${graded72} 72h shadow outcomes are available across decision groups.`;
    investmentNext = "Review recommended and suppressed outcomes before deciding whether publication is justified.";
  }

  const sbcCounts = sbc.status_counts || {};
  const sbcConfirmed = Number(sbcCounts.MARKET_CONFIRMED_CANDIDATE || 0);
  const sbcChecked = Number(sbc.demands_checked || 0);
  let sbcStatus = "WAITING";
  let sbcReason = `${sbcChecked} SBC resource-demand hypotheses checked; ${sbcConfirmed} confirmation candidate(s).`;
  let sbcNext = "Keep reinforcement shadow-only until enough confirmed and false-positive cases have been reviewed.";
  if (sbc.mode !== "SHADOW_ONLY" || sbc.writes_market_status || sbc.changes_priority_weight) {
    sbcStatus = "BLOCKED";
    sbcReason = "SBC reinforcement guardrails are not fail-closed.";
    sbcNext = "Restore SHADOW_ONLY with no market-status or priority writes.";
  } else if (sbcConfirmed >= 10) {
    sbcStatus = "REVIEW";
    sbcReason = `${sbcConfirmed} market-confirmed candidates are available for false-positive review.`;
    sbcNext = "Audit candidate quality before allowing any live reinforcement effect.";
  }

  return [
    {
      name: "Core Intelligence",
      status: coreReady ? "READY" : "BLOCKED",
      reason: coreReady
        ? `${Number(intelligence.current_coverage_pct || 0).toFixed(1)}% persisted coverage; Dashboard live fallback OFF.`
        : `Scanner/cache health is not fully green (${intelligence.status || "unknown"}, ${Number(intelligence.current_coverage_pct || 0).toFixed(1)}% coverage).`,
      next: coreReady ? "No action. This is the production source of truth." : "Fix scanner/current-state health before changing product logic.",
    },
    {
      name: "Held Positions",
      status: heldReady ? "READY" : "BLOCKED",
      reason: trades.length
        ? `${analyzedTrades}/${trades.length} open position(s) have current purchase-aware analysis.`
        : "No open positions require analysis.",
      next: heldReady ? "Use the stored break-even/original-target analysis; discovery targets cannot force a loss-making SELL." : "Wait for/fix the held-position refresher before trusting HOLD/SELL.",
    },
    { name: "SalesSync Reallocation", status: salesStatus, reason: salesReason, next: salesNext },
    { name: "Recommendation Calibration", status: calibrationStatus, reason: calibrationReason, next: calibrationNext },
    { name: "Investment Engine", status: investmentStatus, reason: investmentReason, next: investmentNext },
    { name: "SBC Reinforcement", status: sbcStatus, reason: sbcReason, next: sbcNext },
  ];
}

export default function RoadmapReadinessTab() {
  const query = useAdminRoadmapReadiness();
  if (query.isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading readiness...</p>;
  if (query.error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load roadmap readiness.</p>;

  const items = classify(query.data || {});
  const blocked = items.filter((item) => item.status === "BLOCKED").length;
  const review = items.filter((item) => item.status === "REVIEW").length;
  const waiting = items.filter((item) => item.status === "WAITING").length;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">Roadmap Readiness</div>
          <div className="text-xs text-[var(--v2-muted)]">One answer for what is live, what needs review, and what is correctly waiting for evidence.</div>
        </div>
        <button onClick={() => query.refetch()} disabled={query.isFetching} className="rounded-md border border-[var(--v2-border)] px-2 py-1 text-[10px] text-[var(--v2-muted)] disabled:opacity-50">
          {query.isFetching ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      <SectionCard title="Overall" subtitle="WAITING is not a defect when a system is deliberately evidence-gated">
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-lg border border-[var(--v2-border)] p-3"><div className="text-[10px] uppercase text-[var(--v2-muted)]">Blocked</div><div className={`text-xl font-semibold ${blocked ? TONES.BLOCKED : TONES.READY}`}>{blocked}</div></div>
          <div className="rounded-lg border border-[var(--v2-border)] p-3"><div className="text-[10px] uppercase text-[var(--v2-muted)]">Review</div><div className={`text-xl font-semibold ${TONES.REVIEW}`}>{review}</div></div>
          <div className="rounded-lg border border-[var(--v2-border)] p-3"><div className="text-[10px] uppercase text-[var(--v2-muted)]">Waiting</div><div className={`text-xl font-semibold ${TONES.WAITING}`}>{waiting}</div></div>
        </div>
      </SectionCard>

      <div className="grid gap-3 md:grid-cols-2">
        {items.map((item) => <StatusCard key={item.name} {...item} />)}
      </div>

      <p className="text-[10px] leading-relaxed text-[var(--v2-muted)]">This page is advisory and read-only. READY means the current architecture/guardrail is healthy, not that an evidence-gated feature should automatically be enabled. REVIEW means enough evidence exists for a human decision. WAITING means the correct action is to collect more evidence.</p>
    </div>
  );
}

export { classify as classifyRoadmapReadiness };
