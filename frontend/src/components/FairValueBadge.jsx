// src/components/FairValueBadge.jsx
// The "is this price actually good?" chip - shown anywhere a price is.
// Grounded in real completed sales (fair_value_mv), not vibes.
//
// verdicts: steal | under | fair | overpriced | falling | unknown
// Locked (free tier) rows show the verdict but blur the exact numbers -
// prove the edge exists, gate the precision.
import React from "react";
import { useFairValue } from "../hooks/useFairValue";

const STYLES = {
  steal: { bg: "rgba(145,219,50,0.15)", fg: "#91db32", label: "STEAL" },
  under: { bg: "rgba(145,219,50,0.10)", fg: "#b9e97c", label: "UNDER VALUE" },
  fair: { bg: "rgba(255,255,255,0.08)", fg: "rgba(255,255,255,0.75)", label: "FAIR" },
  overpriced: { bg: "rgba(248,113,113,0.12)", fg: "#f87171", label: "OVERPAY" },
  falling: { bg: "rgba(249,115,22,0.15)", fg: "#f97316", label: "⚠ FALLING" },
  unknown: { bg: "rgba(255,255,255,0.05)", fg: "rgba(255,255,255,0.4)", label: "NO DATA" },
  pending: { bg: "rgba(250,204,21,0.10)", fg: "#facc15", label: "VERIFYING" },
};

function verdictFrom(data) {
  if (!data) return "unknown";
  // Backend flags this when our own median is wildly inconsistent with
  // the live BIN (data_quality_suspect) - a resolved incident showed that
  // means a scraper bug attributed a different card's real sales to this
  // one, not a genuine deal. Show "verifying", not a wrong verdict.
  if (data.data_quality_suspect) return "pending";
  // A crashing card also shows a big discount_pct (current_bin has
  // already dropped, the 24h median hasn't caught up) - that's a falling
  // knife, not a steal. Overrides the number either way.
  if (data.trend_falling) return "falling";
  if (data.verdict) return data.verdict; // teaser shape (free tier)
  const d = data.discount_pct;
  if (d == null) return "unknown";
  if (d >= 8) return "steal";
  if (d >= 3) return "under";
  if (d <= -5) return "overpriced";
  return "fair";
}

export default function FairValueBadge({ cardId, className = "" }) {
  const { data, isLoading } = useFairValue(cardId);
  if (isLoading || !data) return null;

  const verdict = verdictFrom(data);
  const s = STYLES[verdict] || STYLES.unknown;
  const locked = !!data.locked;
  const pct = data.discount_pct;

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wide ${className}`}
      style={{ background: s.bg, color: s.fg }}
      title={
        verdict === "pending"
          ? "We're still verifying this card's market data"
          : verdict === "falling"
          ? "Price is trending down faster than the 24h median has caught up - wait for it to settle before buying"
          : locked
          ? "Real-sales fair value — go Pro to see the exact numbers"
          : `vs real 24h sold median (${data.sales_24h ?? 0} sales tracked)`
      }
    >
      {s.label}
      {verdict !== "pending" && verdict !== "falling" && !locked && pct != null && (
        <span className="tabular-nums">{pct > 0 ? `-${Math.abs(pct)}%` : `+${Math.abs(pct)}%`}</span>
      )}
      {locked && <span aria-hidden>🔒</span>}
    </span>
  );
}
