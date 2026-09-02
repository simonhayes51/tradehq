// src/components/shareCardFormat.js
//
// Number formatting shared by the two share-card art components. Kept
// separate from lib/coinFormat's site-wide CoinValue/coinText: those
// compact-format to 1 fraction digit (fine for a KPI tile - "1.5M" is
// plenty precise for a dashboard stat), which is too lossy for a card
// whose entire point is to show one exact trade result. Two fraction
// digits keeps "1.45M" recognisable as 1,450,000 rather than rounding
// it to the same "1.5M" a 1,500,000 trade would also show.
//
// DIMENSIONS is the shared source of the two export sizes - mirrored on
// the Python side (trade_share_render.py / trade_summary_render.py's own
// DIMENSIONS_FOR_FORMAT) since a Python module can't import a JS one.
// The two must stay numerically identical: the backend's Playwright
// viewport has to match what this component renders at, or the
// screenshot crops or letterboxes the card.
export const DIMENSIONS = {
  wide: { width: 900, height: 470 },
  portrait: { width: 1080, height: 1350 },
};

export function formatCoinsExact(n) {
  return Number(n || 0).toLocaleString();
}

// Deliberately not Intl's { notation: "compact" }: its K/M casing turned
// out to depend on the runtime's ICU data (full ICU in the Chromium that
// actually renders the card vs. the reduced "small-icu" build Node/
// Vitest ships with, which lower-cases the unit) - the same number would
// render "997K" in production and "997k" under test. A fixed, manual
// suffix is deterministic everywhere.
function trimTrailingZeros(fixed) {
  return fixed.replace(/\.?0+$/, "");
}

export function formatCoinsCompact(n) {
  const value = Number(n || 0);
  const abs = Math.abs(value);
  if (abs < 1000) return formatCoinsExact(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}${trimTrailingZeros((abs / 1_000_000).toFixed(2))}M`;
  return `${sign}${trimTrailingZeros((abs / 1000).toFixed(2))}K`;
}

// A hero profit figure is rendered at a fixed base size for a "typical"
// result, but total profit (especially the All-Time card, summed across
// a power user's whole history) can run to 8-9 digits - caught in visual
// QA when "+24,650,000" clipped off the right edge of the portrait card
// at a flat 150px. Scales down past `threshold` characters instead of
// clipping, rather than picking one flat size that only some totals fit.
export function scaledFontSize(baseSize, text, threshold = 9, minScale = 0.4) {
  const len = String(text).length;
  if (len <= threshold) return baseSize;
  return Math.round(baseSize * Math.max(threshold / len, minScale));
}

export function formatRoi(roi) {
  if (roi === null || roi === undefined || !Number.isFinite(Number(roi))) return null;
  const value = Number(roi);
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}
