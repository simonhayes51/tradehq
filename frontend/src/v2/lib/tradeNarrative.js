// Turns the engine's raw signals into the two layers of copy the UI shows:
// a one-line "selling speed" word for the simple layer, and a why-sentence
// that actually differs by scenario instead of one templated paragraph
// with the numbers swapped in.
//
// The scenario differentiation already exists server-side - see
// app/services/futgg_trend.py's STATE_DESCRIPTIONS (falling_knife,
// downtrend, stabilising, recovering, sideways, uptrend,
// insufficient_trend_data), each hand-written and gated on real trend
// features (slope, drawdown, velocity...). This module's job is just to
// surface that text instead of the generic first-signal-reason line the
// UI used before, never to invent a second narrative layer on top of it.

const GENERIC_TREND_TEXT = "There are not enough recent sales, spread over enough time, to tell which way this card is moving.";

export function sellingSpeedLabel(liquidityScore) {
  if (liquidityScore == null) return "Unknown";
  const value = Number(liquidityScore);
  if (!Number.isFinite(value)) return "Unknown";
  if (value >= 0.7) return "Good";
  if (value >= 0.4) return "Fair";
  if (value > 0) return "Slow";
  return "Very slow";
}

export function movementLabel(fraction) {
  if (fraction == null) return null;
  const value = Number(fraction);
  if (!Number.isFinite(value)) return null;
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

// item accepts either the raw API shape (trend_description, signal_reasons)
// or the normalised shapes used by HomeDashboard/PlayerPage
// (trendDescription, reasoning) - callers pass whichever they already have.
export function buildWhyText(item) {
  const trend = item?.trend_description ?? item?.trendDescription ?? null;
  const reasons = Array.isArray(item?.signal_reasons) ? item.signal_reasons
    : Array.isArray(item?.reasoning) ? item.reasoning
    : [];
  const topReason = typeof item?.reasoning === "string" ? item.reasoning : reasons[0];

  if (trend && trend !== GENERIC_TREND_TEXT) {
    // Only append the top signal reason when it adds something the trend
    // sentence doesn't already say - avoids "X. X." on cards where both
    // ultimately describe the same discount-to-median fact.
    if (topReason && !trend.toLowerCase().startsWith(topReason.toLowerCase().slice(0, 16))) {
      return `${trend} ${topReason}`;
    }
    return trend;
  }
  return topReason || "Not enough signal yet to explain this call.";
}
