// src/v2/components/StatTile.jsx
function toneClass(tone, fallback) {
  if (tone === "positive") return "text-[var(--v2-positive)]";
  if (tone === "negative") return "text-[var(--v2-negative)]";
  return fallback;
}

export default function StatTile({ label, value, delta, deltaTone = "neutral", valueTone = "neutral" }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-[var(--v2-muted)]">{label}</span>
      <span className={`text-xl font-semibold ${toneClass(valueTone, "text-[var(--v2-text)]")}`}>{value}</span>
      {delta !== undefined && delta !== null && (
        <span className={`text-xs ${toneClass(deltaTone, "text-[var(--v2-muted)]")}`}>{delta}</span>
      )}
    </div>
  );
}
