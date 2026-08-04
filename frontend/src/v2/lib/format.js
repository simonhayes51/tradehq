// src/v2/lib/format.js
export function formatCoins(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return `${Math.round(v)}`;
}

export function formatPct(n, { withSign = false } = {}) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const v = Number(n);
  const sign = withSign && v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

// Past dates only (recent sales, activity feed, pipeline health) - a
// future `iso` would produce a negative diffSec, which the "just now"
// branch below would wrongly swallow. Use formatCountdown for future
// dates (e.g. an SBC's ends_at) instead of this function.
export function formatRelativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

// Future dates - "ends in 3d", "ends in 4h". Returns "expired" for a
// date already in the past rather than a nonsensical negative duration.
export function formatCountdown(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffSec = Math.round((then - Date.now()) / 1000);
  if (diffSec <= 0) return "expired";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d`;
}

// Recommendation Engine V1.2 (backend recommendation_engine_v2.py)
// never populates the legacy holding_period_days/reasoning columns -
// those belonged to rule_v1's now-deprecated shape - but every V1.2 row
// does carry qualified_strategies/failed_gate_reasons/
// held_decision_reasons (see migration 024). Mirrors dashboard.py's
// _holding_period_label()/_reasoning_text() so the Player Page (which
// reads the raw recommendations_latest row, not dashboard.py's already-
// reshaped payload) shows the same real info instead of a permanent "—".
const STRATEGY_HOLDING_LABEL = {
  quick_flip: "~24h", swing_trade: "~48h", low_risk: "Flexible",
  lazy_buyer: "Flexible", sbc: "Flexible", long_hold: "~7d",
};

export function holdingPeriodFromStrategies(qualifiedStrategies) {
  for (const name of Object.keys(STRATEGY_HOLDING_LABEL)) {
    if ((qualifiedStrategies || []).includes(name)) return STRATEGY_HOLDING_LABEL[name];
  }
  return null;
}

export function reasoningFromV1_2(rec) {
  if (!rec) return "";
  const status = rec.status;
  const qualified = rec.qualified_strategies || [];
  const failedGates = rec.failed_gate_reasons || [];
  const heldReasons = rec.held_decision_reasons || [];
  if (status === "BUY") {
    const names = qualified.map((s) => s.replace(/_/g, " ")).join(", ") || "a strategy";
    return `Qualifies for: ${names}.`;
  }
  if (status === "SELL") {
    const names = heldReasons.map((r) => r.replace(/_/g, " ").toLowerCase()).join("; ");
    return names || "Selling now looks better than continuing to hold.";
  }
  if (status === "AVOID") return "The likely outcome is a net loss after EA's sale tax.";
  if (status === "INSUFFICIENT_DATA") {
    return failedGates.length
      ? `Missing: ${failedGates.map((r) => r.replace(/_/g, " ").toLowerCase()).join(", ")}.`
      : "Not enough live market data yet.";
  }
  if (status === "WAIT") return "Doesn't clear any strategy's threshold yet.";
  return "";
}
