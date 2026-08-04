// src/v2/components/ConfidenceGauge.jsx
//
// Circular percentage gauge via recharts' RadialBarChart - recharts is
// already a dependency used elsewhere (SalesChartSection.jsx), so this
// needs no new package. tone colors the ring to match the signal it's
// attached to (green for buy, red for avoid, amber for hold/neutral) -
// matching the reference design's per-signal gauge coloring - rather
// than always using the generic accent, which read as flat/disconnected
// from the badge sitting right next to it.
import { RadialBarChart, RadialBar, PolarAngleAxis } from "recharts";

const TONE_COLOR = {
  positive: "var(--v2-positive)",
  negative: "var(--v2-negative)",
  warning: "var(--v2-warning)",
  accent: "var(--v2-accent)",
};

export default function ConfidenceGauge({ value, size = 44, tone = "accent" }) {
  if (value === null || value === undefined) return null;
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  const color = TONE_COLOR[tone] || TONE_COLOR.accent;
  const data = [{ value: pct, fill: color }];
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <RadialBarChart
        width={size}
        height={size}
        innerRadius="70%"
        outerRadius="100%"
        data={data}
        startAngle={90}
        endAngle={-270}
      >
        <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
        <RadialBar dataKey="value" cornerRadius={size} background={{ fill: "var(--v2-border)" }} />
      </RadialBarChart>
      <span
        className="absolute inset-0 flex items-center justify-center font-semibold"
        style={{ fontSize: Math.max(8, size * 0.24), color }}
      >
        {pct}%
      </span>
    </div>
  );
}
