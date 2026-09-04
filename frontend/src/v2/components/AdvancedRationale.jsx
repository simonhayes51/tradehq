// Layer 2 of the progressive-disclosure pattern: the evidence behind the
// plain-English verdict, collapsed by default (native <details> - no JS
// state needed, and it's accessible/keyboard-operable for free). Everybody
// gets the simple verdict text; this is for the "why, exactly?" reader.
//
// Renders straight off the raw FUT.GG API item shape (snake_case) since
// every field here already comes back on /opportunities, /trade-finder
// and /players/{id} today - nothing here required a backend change.
import CoinValue from "./CoinValue";
import { formatAge } from "./MarketFreshness";
import { movementLabel } from "../lib/tradeNarrative";

function pct(value) {
  if (value == null) return "—";
  const n = Number(value) * 100;
  return Number.isFinite(n) ? `${Math.round(n)}%` : "—";
}

function Metric({ label, value }) {
  return (
    <div className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2.5">
      <div className="text-[9px] font-bold tracking-wider uppercase text-[var(--v2-muted)] mb-1">{label}</div>
      <div className="text-[13px] font-bold text-[var(--v2-text)] font-mono tabular-nums">{value}</div>
    </div>
  );
}

export default function AdvancedRationale({ item, defaultOpen = false }) {
  if (!item) return null;
  const movement = movementLabel(item.trend_features?.medium_term_change);
  const freshness = item.price_age_seconds != null ? formatAge(item.price_age_seconds)
    : item.price_age_minutes != null ? formatAge(item.price_age_minutes * 60)
    : null;

  return (
    <details className="group rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)]/40 px-3 py-2.5" open={defaultOpen}>
      <summary className="flex items-center gap-1.5 cursor-pointer select-none text-[13px] font-bold text-[var(--v2-accent)] list-none [&::-webkit-details-marker]:hidden">
        <span className="inline-block transition-transform group-open:rotate-90">▸</span>
        Advanced rationale — the full numbers behind this call
      </summary>
      <div className="mt-3 flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-2.5">
          <Metric label="Fair value" value={<CoinValue value={item.fair_value} />} />
          <Metric label="Current BIN" value={<CoinValue value={item.current_bin} />} />
          <Metric label="Recent sales" value={item.sales_sample_size ?? "—"} />
          <Metric label="Sales median" value={<CoinValue value={item.sales_median} />} />
          <Metric label="Confidence" value={pct(item.confidence_score)} />
          <Metric label="Liquidity" value={pct(item.liquidity_score)} />
          <Metric label="Recent movement" value={movement ?? "—"} />
          <Metric label="Risk" value={item.risk_level ? item.risk_level.charAt(0).toUpperCase() + item.risk_level.slice(1) : "Unknown"} />
        </div>
        {item.trend_description ? (
          <div className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-3">
            <b className="block text-[10px] font-bold tracking-wider uppercase text-[var(--v2-muted)] mb-1">How we reached this</b>
            <p className="text-xs text-[var(--v2-text)] leading-relaxed">{item.trend_description}</p>
          </div>
        ) : null}
        {Array.isArray(item.signal_reasons) && item.signal_reasons.length ? (
          <ul className="text-xs text-[var(--v2-muted)] grid gap-1">
            {item.signal_reasons.map((reason, index) => (
              <li key={index} className="flex gap-2"><span className="text-[var(--v2-accent)]">•</span>{reason}</li>
            ))}
          </ul>
        ) : null}
        <p className="text-[11px] text-[var(--v2-muted)]">
          {freshness ? `Data updated ${freshness}. ` : null}EA tax (5%) is already included in every profit figure above.
        </p>
      </div>
    </details>
  );
}
