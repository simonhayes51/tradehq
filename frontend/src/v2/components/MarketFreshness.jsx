// src/v2/components/MarketFreshness.jsx
//
// FUT.GG migration: a hard product requirement is that staleness is
// NEVER hidden - whenever price data is shown, this label renders in
// either a "live" or "stale" state, never omitted. Staleness threshold:
// simple flat 6 hours (21600s) by default, since the contract doesn't
// give us per-rarity expected refresh intervals to key off yet; a
// tighter per-rarity tier threshold can replace STALE_THRESHOLD_SECONDS
// once the backend exposes one. Anything with no age data at all (price
// missing/never captured) is treated as stale too - "unknown" is not
// "fine."
import { AlertTriangle, RadioTower } from "lucide-react";
import "./market-freshness.css";

export const STALE_THRESHOLD_SECONDS = 6 * 60 * 60; // 6h flat default, see header note

function formatAge(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return null;
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 60) return "moments ago";
  const mins = Math.round(s / 60);
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/**
 * MarketFreshness - renders the FUT.GG live-source/freshness label.
 *
 * Pass either:
 *  - priceAgeSeconds: a single card's `price_age_seconds`, or
 *  - capturedAt: an ISO timestamp (e.g. `current_bin_captured_at`) to
 *    derive the age from, if price_age_seconds isn't present, or
 *  - staleThresholdSeconds: override the default 6h cutoff.
 */
export default function MarketFreshness({ priceAgeSeconds, capturedAt, staleThresholdSeconds = STALE_THRESHOLD_SECONDS, compact = false, className = "" }) {
  let ageSeconds = priceAgeSeconds;
  if (ageSeconds == null && capturedAt) {
    const then = new Date(capturedAt).getTime();
    if (!Number.isNaN(then)) ageSeconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  }
  const known = ageSeconds != null && Number.isFinite(Number(ageSeconds));
  const isStale = !known || Number(ageSeconds) > staleThresholdSeconds;
  const ageText = known ? formatAge(ageSeconds) : "unknown";

  return (
    <div className={`v2-market-freshness ${isStale ? "is-stale" : "is-live"}${compact ? " is-compact" : ""} ${className}`.trim()} role="status">
      {isStale ? <AlertTriangle size={compact ? 12 : 14} /> : <RadioTower size={compact ? 12 : 14} />}
      <span>
        {isStale
          ? known
            ? `Last market update ${ageText} — live pricing may be unavailable`
            : "Market update time unknown — live pricing may be unavailable"
          : `Live market source: FUT.GG · Updated ${ageText}`}
      </span>
    </div>
  );
}
