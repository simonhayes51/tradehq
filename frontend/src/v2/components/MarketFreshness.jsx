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

// Exported so callers that hold a timestamp rather than an age can render
// the same wording. Sale rows in particular MUST derive their age from
// approximate_sold_at at render time - see FutggMarketSection's
// "Recent sales" table for why reusing the scraper's own age string is
// wrong.
export function formatAge(seconds) {
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

export function formatCompactAge(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return null;
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 60) return "now";
  const mins = Math.round(s / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/**
 * MarketFreshness - renders the FUT.GG live-source/freshness label.
 *
 * Pass either:
 *  - priceAgeSeconds: a single card's `price_age_seconds`, or
 *  - checkedAt: an ISO timestamp for the last BIN feed confirmation, or
 *  - capturedAt: an older ISO timestamp fallback, or
 *  - staleThresholdSeconds: override the default 6h cutoff.
 */
export default function MarketFreshness({ priceAgeSeconds, checkedAt, capturedAt, staleThresholdSeconds = STALE_THRESHOLD_SECONDS, compact = false, className = "" }) {
  let ageSeconds = priceAgeSeconds;
  const observedAt = checkedAt || capturedAt;
  if (ageSeconds == null && observedAt) {
    const then = new Date(observedAt).getTime();
    if (!Number.isNaN(then)) ageSeconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  }
  const known = ageSeconds != null && Number.isFinite(Number(ageSeconds));
  const isStale = !known || Number(ageSeconds) > staleThresholdSeconds;
  const ageText = known ? (compact ? formatCompactAge(ageSeconds) : formatAge(ageSeconds)) : "unknown";

  return (
    <div className={`v2-market-freshness ${isStale ? "is-stale" : "is-live"}${compact ? " is-compact" : ""} ${className}`.trim()} role="status">
      {isStale ? <AlertTriangle size={compact ? 12 : 14} /> : <RadioTower size={compact ? 12 : 14} />}
      <span>
        {isStale
          ? known
            ? compact ? `Stale ${ageText}` : `Last market check ${ageText} - live pricing may be unavailable`
            : compact ? "Check unknown" : "Market check time unknown - live pricing may be unavailable"
          : compact ? `Checked ${ageText}` : `Live market data · Checked ${ageText}`}
      </span>
    </div>
  );
}
