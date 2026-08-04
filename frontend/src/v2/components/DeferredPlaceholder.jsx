// src/v2/components/DeferredPlaceholder.jsx
//
// For Player Page / Home Dashboard sections the v2 plan names but that
// have no real data source yet (Investment Score, Risk Rating, Expected
// ROI/Holding Period, Market Regime, Suggested Alternatives, AI-generated
// summaries, etc. - all Phase 2/3 work: the Analytics/Recommendation
// Engines and the Market Events pipeline). Visibly labeled "coming soon"
// rather than faked with placeholder numbers or silently omitted.
export default function DeferredPlaceholder({ title, note }) {
  return (
    <div className="rounded-[var(--v2-radius)] border border-dashed border-[var(--v2-border)] p-5 opacity-60">
      <h3 className="text-sm font-semibold text-[var(--v2-text)]">{title}</h3>
      <p className="text-xs text-[var(--v2-muted)] mt-1">
        {note || "Coming in a future release."}
      </p>
    </div>
  );
}
