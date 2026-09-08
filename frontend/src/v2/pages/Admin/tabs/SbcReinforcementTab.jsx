import { useState } from "react";
import SectionCard from "../../../components/SectionCard";
import { useAdminSbcReinforcement } from "../../../hooks/useAdminSbcReinforcement";

const POS = "text-[var(--v2-positive)]";
const NEG = "text-[var(--v2-negative)]";
const MUTED = "text-[var(--v2-muted)]";

function pct(v) { return v === null || v === undefined ? "—" : `${Number(v).toFixed(1)}%`; }
function ratio(v) { return v === null || v === undefined ? "—" : `${Number(v).toFixed(2)}×`; }

const TONE = {
  MARKET_CONFIRMED_CANDIDATE: POS,
  CONFLICTING: NEG,
  PRICE_ONLY: MUTED,
  UNCONFIRMED: MUTED,
  PENDING: MUTED,
  INSUFFICIENT: MUTED,
  UNSUPPORTED: MUTED,
};

export default function SbcReinforcementTab() {
  const [hours, setHours] = useState(72);
  const { data, isLoading, error, isFetching, refetch } = useAdminSbcReinforcement(hours);
  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading SBC market evidence...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load SBC reinforcement evidence.</p>;

  const items = data?.items || [];
  const counts = data?.status_counts || {};
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">SBC Market Reinforcement</div>
          <div className="text-xs text-[var(--v2-muted)]">Tests broad SBC-demand hypotheses against real BIN and completed-sales behaviour.</div>
        </div>
        <div className="flex gap-1">
          {[24,72,168].map(v => <button key={v} onClick={() => setHours(v)} className={`rounded-md border px-2 py-1 text-[10px] ${hours === v ? "border-[var(--v2-accent)]" : "border-[var(--v2-border)] text-[var(--v2-muted)]"}`}>{v === 168 ? "7d" : `${v}h`}</button>)}
          <button onClick={() => refetch()} disabled={isFetching} className="rounded-md border border-[var(--v2-border)] px-2 py-1 text-[10px] text-[var(--v2-muted)] disabled:opacity-50">{isFetching ? "Refreshing..." : "Refresh"}</button>
        </div>
      </div>

      <SectionCard title="Research Guardrail" subtitle="Observation only. This does not alter SBC priority or recommendation weights.">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Metric label="Mode" value={data?.mode || "UNKNOWN"} tone={data?.mode === "SHADOW_ONLY" ? POS : NEG} />
          <Metric label="Demands checked" value={data?.demands_checked ?? 0} />
          <Metric label="Writes market status" value={data?.writes_market_status ? "YES" : "NO"} tone={data?.writes_market_status ? NEG : POS} />
          <Metric label="Changes priority" value={data?.changes_priority_weight ? "YES" : "NO"} tone={data?.changes_priority_weight ? NEG : POS} />
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {Object.entries(counts).map(([status,count]) => <span key={status} className={`rounded-md border border-[var(--v2-border)] px-2 py-1 text-[10px] ${TONE[status] || MUTED}`}>{status.replaceAll("_"," ")}: <strong>{count}</strong></span>)}
        </div>
      </SectionCard>

      <SectionCard title="Resource Demand Evidence" subtitle="A confirmation candidate requires both a material segment price rise and stronger completed-sales activity.">
        {!items.length ? <p className="text-xs text-[var(--v2-muted)]">No confirmed resource-demand hypotheses in this window.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]"><tr className="border-b border-[var(--v2-border)]"><th className="py-2 pr-3">Resource</th><th className="py-2 pr-3">Shadow state</th><th className="py-2 pr-3">Cards</th><th className="py-2 pr-3">Price Δ</th><th className="py-2 pr-3">Sales</th><th className="py-2">Reason</th></tr></thead>
              <tbody>{items.map(item => {
                const ev = item.evidence || {};
                return <tr key={item.demand_id} className="border-b border-[var(--v2-border)]/60">
                  <td className="py-2 pr-3"><div className="font-medium">{item.resource_type} {item.resource_value}</div><div className="max-w-52 truncate text-[10px] text-[var(--v2-muted)]">{item.raw_text}</div></td>
                  <td className={`py-2 pr-3 font-medium ${TONE[item.shadow_status] || MUTED}`}>{item.shadow_status?.replaceAll("_"," ")}</td>
                  <td className="py-2 pr-3">{ev.matched_cards ?? "—"}</td>
                  <td className="py-2 pr-3">{pct(ev.price_change_pct)}</td>
                  <td className="py-2 pr-3">{ratio(ev.sales_activity_ratio)}</td>
                  <td className="py-2 text-[10px] text-[var(--v2-muted)]">{item.reason}</td>
                </tr>;
              })}</tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <p className="text-[10px] leading-relaxed text-[var(--v2-muted)]">MARKET CONFIRMED CANDIDATE is deliberately not the same thing as changing the live `market_status`. The shadow method needs enough real examples and false-positive review before it is allowed to influence collection or trading decisions.</p>
    </div>
  );
}

function Metric({ label, value, tone = MUTED }) {
  return <div className="rounded-lg border border-[var(--v2-border)] bg-black/20 p-3"><div className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">{label}</div><div className={`mt-1 text-lg font-semibold ${tone}`}>{value}</div></div>;
}
