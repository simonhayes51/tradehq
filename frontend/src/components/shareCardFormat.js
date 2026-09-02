// src/components/shareCardFormat.js
//
// Number formatting shared by the two share-card art components. Kept
// separate from lib/coinFormat's site-wide CoinValue/coinText: those
// compact-format to 1 fraction digit (fine for a KPI tile), which is
// too lossy here - "1.45M" is recognisable as 1,450,000 in a way "1.5M"
// (indistinguishable from 1,500,000) is not.
//
// This is DISPLAY formatting only - every calculation (profit, ROI,
// aggregation) runs on the full-precision number from trade_economics.py
// well before it reaches this module. Nothing here ever feeds back into
// a computation.
//
// The rule, applied everywhere a coin figure appears on a card (hero
// profit included - an early version showed the hero as a full exact
// integer, which is exactly the string long enough to risk clipping the
// canvas): below 1,000,000, round to the nearest thousand with no
// decimal ("997K", "230K"); at or above, always exactly two decimal
// places, even when that means a trailing zero ("1.52M", "2.20M") -
// unlike the trim-to-shortest style CoinValue's compact mode uses, a
// fixed two decimals means every hero figure occupies a predictable,
// pre-measured width.
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
export function formatCoinsCompact(n) {
  const value = Number(n || 0);
  const abs = Math.abs(value);
  if (abs < 1000) return formatCoinsExact(value);
  const sign = value < 0 ? "-" : "";
  if (abs < 1_000_000) {
    const thousands = Math.round(abs / 1000);
    // A value that rounds up to a full 1000K (999,500-999,999) belongs
    // in the M tier, not displayed as "1000K".
    if (thousands < 1000) return `${sign}${thousands}K`;
  }
  return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
}

// A hero profit figure (or a long player name) is rendered at a fixed
// base size for a "typical" result, but total profit - especially the
// All-Time card, summed across a power user's whole history - or an
// unusually long real player name can still run past what that size was
// measured for. Scales down past `threshold` characters instead of
// clipping, rather than picking one flat size that only some cases fit.
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
