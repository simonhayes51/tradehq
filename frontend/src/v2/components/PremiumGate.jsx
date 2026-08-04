// src/v2/components/PremiumGate.jsx
//
// Prop-driven, not context-driven: card_fair_value already decides
// locked/unlocked server-side and returns it on the row itself
// (row.locked), so this trusts that signal directly instead of
// re-deriving access from a second, possibly-stale client-side
// entitlements check - matching the v2 plan's gating principle (never
// show/hide a feature based on a guess the server itself would
// contradict).
import { Link } from "react-router-dom";

export default function PremiumGate({ locked, featureName, children, fallback }) {
  if (!locked) return <>{children}</>;
  if (fallback) return <>{fallback}</>;

  return (
    <div className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-5 flex flex-col items-center justify-center gap-3 text-center min-h-[140px]">
      <div>
        <p className="text-sm font-semibold text-[var(--v2-accent)]">Pro feature</p>
        <p className="text-xs text-[var(--v2-muted)] mt-1">
          {featureName || "This section"} is available on the Pro plan.
        </p>
      </div>
      <Link
        to="/billing"
        className="inline-flex items-center px-3 py-1.5 rounded-lg bg-[var(--v2-accent)] text-black text-xs font-semibold hover:opacity-90"
      >
        Upgrade
      </Link>
    </div>
  );
}
