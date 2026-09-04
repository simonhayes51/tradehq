// src/v2/lib/chartFormat.js
//
// Axis and label formatting shared by the two time-series charts on the
// player surfaces (completed sales, BIN history). They had drifted: the
// sales chart already thinned its ticks and picked a clock/day format
// from the span the data actually covers, while the BIN chart printed a
// full `toLocaleString()` datetime per data point on an ordinal axis and
// overprinted them into one continuous string.

export function formatDay(value) {
  return new Date(value).toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
}

export function formatClock(value) {
  return new Date(value).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export function compactCoins(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}m`;
  if (Math.abs(n) >= 1000) return `${Math.round(n / 1000)}k`;
  return String(Math.round(n));
}

export function humaniseSpan(ms) {
  const hours = ms / 3600000;
  if (hours < 1) return `${Math.max(1, Math.round(ms / 60000))} minutes`;
  if (hours < 36) return `${Math.round(hours)} hours`;
  return `${Math.round(hours / 24)} days`;
}

/**
 * A fixed, small number of evenly spaced tick positions across a sorted
 * numeric series.
 *
 * Deliberately explicit positions rather than recharts'
 * interval="preserveStartEnd" + minTickGap: on a bursty, irregularly
 * spaced series (a cluster of forty completed sales inside ten minutes)
 * that combination still emits far more labels than minTickGap should
 * allow. A fixed count cannot overcrowd however the points fall.
 */
export function evenTicks(points, count = 5) {
  if (!points || points.length < 2) return undefined;
  const first = points[0].time;
  const last = points[points.length - 1].time;
  if (first === last) return [first];
  return Array.from({ length: count }, (_, i) => Math.round(first + ((last - first) * i) / (count - 1)));
}

/**
 * Label by the span the data ACTUALLY covers, not the range that was
 * asked for. A whole window sitting inside one day drawn with day
 * labels prints "Thu 6" five times.
 */
export function tickFormatterForSpan(spanMs) {
  return spanMs > 0 && spanMs < 36 * 3600 * 1000 ? formatClock : formatDay;
}

// --- Robust series statistics -----------------------------------------
//
// Completed-sales series routinely carry outliers: a panic sale far
// below the market, or a single listing snapped up at several times the
// going rate. Scaling a chart to raw min/max lets one such print
// flatten every real sale into a band a few pixels tall at the bottom of
// the frame - which is exactly what the sales chart was doing.

export function percentileOf(sortedValues, p) {
  if (!sortedValues.length) return null;
  if (sortedValues.length === 1) return sortedValues[0];
  const rank = (sortedValues.length - 1) * p;
  const low = Math.floor(rank);
  const high = Math.ceil(rank);
  if (low === high) return sortedValues[low];
  return sortedValues[low] + (rank - low) * (sortedValues[high] - sortedValues[low]);
}

/**
 * median plus a "typical range" (the interquartile range) and a Y domain
 * that keeps the typical range readable without hiding that outliers
 * exist.
 *
 * The domain is clamped to the 5th-95th percentile with 8% headroom,
 * but never tighter than the interquartile range, and never wider than
 * the real data. Points outside it are still drawn - recharts clips
 * them to the edge - and the caller says how many there are, so an
 * outlier is disclosed rather than silently cropped.
 *
 * @returns null when there is not enough data to say anything.
 */
export function salesStats(values) {
  const clean = values.map(Number).filter((v) => Number.isFinite(v) && v > 0).sort((a, b) => a - b);
  if (clean.length < 2) return null;
  const median = percentileOf(clean, 0.5);
  const q1 = percentileOf(clean, 0.25);
  const q3 = percentileOf(clean, 0.75);
  const p5 = percentileOf(clean, 0.05);
  const p95 = percentileOf(clean, 0.95);
  const min = clean[0];
  const max = clean[clean.length - 1];

  const padding = Math.max((p95 - p5) * 0.08, median * 0.01, 1);
  const lower = Math.max(min, Math.min(p5 - padding, q1));
  const upper = Math.min(max, Math.max(p95 + padding, q3));
  // Degenerate case: every sale at the same price. A zero-height domain
  // makes recharts draw nothing at all.
  const domain = upper > lower ? [Math.floor(lower), Math.ceil(upper)] : [Math.floor(min * 0.98), Math.ceil(max * 1.02)];
  const outliers = clean.filter((v) => v < domain[0] || v > domain[1]).length;

  return { median, q1, q3, min, max, domain, outliers, count: clean.length };
}
