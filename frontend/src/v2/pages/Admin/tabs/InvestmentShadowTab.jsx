import { useState } from "react";
import SectionCard from "../../../components/SectionCard";
import { useAdminInvestmentShadow } from "../../../hooks/useAdminInvestmentShadow";

const POS = "text-[var(--v2-positive)]";
const NEG = "text-[var(--v2-negative)]";
const MUTED = "text-[var(--v2-muted)]";

function Stat({ label, value, detail, tone = MUTED }) {
  return (
    <div className="rounded-lg border border-[var(--v2-border)] bg-black/20 p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${tone}`}>{value}</div>
      {detail ? <div className="mt-1 text-[10px] text-[var(--v2-muted)]">{detail}</div> : null}
    </div>
  );
}

function pct(value) {
  return value === null || value === undefined ? "—" : `${Number(value).toFixed(2)}%`;
}

export default function InvestmentShadowTab() {
  const [hours, setHours] = useState(168);
  const { data, isLoading, error, isFetching, refetch } = useAdminInvestmentShadow(hours);

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading investment shadow evidence...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load investment shadow evidence.</p>;

  const engine = data?.engine || {};
  const decisions = data?.decisions || [];
  const states = data?.by_market_state || [];
  const signals = data?.signals || [];
  const mode = engine.mode || "UNKNOWN";
  const isShadow = mode === "SHADOW";
  const graded24 = decisions.reduce((sum, row) => sum + Number(row.graded_24h || 0), 0);
  const graded72 = decisions.reduce((sum, row) => sum + Number(row.graded_72h || 0), 0);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Investment Engine</div>
          <div className="text-xs text-[var(--v2-muted)]">1–7 day market-cycle engine. Kept separate from live Opportunities and Trade Finder.</div>
        </div>
        <div className="flex gap-1">
          {[24, 168, 720].map((value) => (
            <button key={value} onClick={() => setHours(value)} className={`rounded-md border px-2 py-1 text-[10px] ${hours === value ? "border-[var(--v2-accent)]" : "border-[var(--v2-border)] text-[var(--v2-muted)]"}`}>
              {value === 24 ? "24h" : value === 168 ? "7d" : "30d"}
            </button>
          ))}
          <button onClick={() => refetch()} disabled={isFetching} className="rounded-md border border-[var(--v2-border)] px-2 py-1 text-[10px] text-[var(--v2-muted)] disabled:opacity-50">{isFetching ? "Refreshing..." : "Refresh"}</button>
        </div>
      </div>

      <SectionCard title="Publication Gate" subtitle="Calculation and publication are intentionally separate switches">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Mode" value={mode} tone={isShadow ? POS : mode === "PUBLISHING" ? POS : NEG} detail={isShadow ? "calculating, zero public card-level calls" : null} />
          <Stat label="Engine enabled" value={engine.enabled ? "YES" : "NO"} tone={engine.enabled ? POS : NEG} />
          <Stat label="Publishing" value={engine.publishing ? "YES" : "NO"} tone={engine.publishing ? NEG : POS} detail={engine.publishing ? "public calls enabled" : "fail-closed"} />
          <Stat label="Decision" value={engine.publishing ? "LIVE" : "KEEP SHADOW"} tone={engine.publishing ? NEG : POS} detail="publish only after graded evidence review" />
        </div>
      </SectionCard>

      <SectionCard title="Outcome Maturity" subtitle={`Decision-log evidence over the last ${hours === 720 ? "30 days" : hours === 168 ? "7 days" : "24 hours"}`}>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Decision groups" value={decisions.length} />
          <Stat label="Graded +24h" value={graded24.toLocaleString()} />
          <Stat label="Graded +72h" value={graded72.toLocaleString()} />
          <Stat label="Signal groups" value={signals.length} />
        </div>
      </SectionCard>

      <SectionCard title="Shadow Decisions" subtitle="Suppressed decisions are evidence too: avoiding a further crash is a correct decision">
        {!decisions.length ? <p className="text-xs text-[var(--v2-muted)]">No shadow decision evidence in this window.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]"><tr className="border-b border-[var(--v2-border)]"><th className="py-2 pr-3">Decision</th><th className="py-2 pr-3">Strategy</th><th className="py-2 pr-3">Cohort</th><th className="py-2 pr-3">n</th><th className="py-2 pr-3">24h median</th><th className="py-2">72h median</th></tr></thead>
              <tbody>{decisions.slice(0, 30).map((row, i) => <tr key={`${row.strategy}-${row.cohort}-${row.decision}-${row.reason}-${i}`} className="border-b border-[var(--v2-border)]/60"><td className="py-2 pr-3 font-medium">{row.decision}</td><td className="py-2 pr-3">{row.strategy}</td><td className="py-2 pr-3">{row.cohort}</td><td className="py-2 pr-3">{row.count}</td><td className="py-2 pr-3">{pct(row.median_roi_24h_pct)}</td><td className="py-2">{pct(row.median_roi_72h_pct)}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Market-State Outcomes" subtitle="Check whether CRASHING/BOTTOMING/INVESTABLE states actually separate later returns">
        {!states.length ? <p className="text-xs text-[var(--v2-muted)]">No graded market-state evidence yet.</p> : (
          <div className="flex flex-wrap gap-2">{states.map((row, i) => <div key={`${row.market_state}-${row.decision}-${i}`} className="rounded-lg border border-[var(--v2-border)] p-3 text-xs"><div className="font-medium">{row.market_state} · {row.decision}</div><div className="mt-1 text-[var(--v2-muted)]">n={row.count ?? row.n ?? 0} · 24h median {pct(row.median_roi_24h_pct)}</div></div>)}</div>
        )}
      </SectionCard>

      <p className="text-[10px] leading-relaxed text-[var(--v2-muted)]">Do not enable publication because a handful of shadow signals look good. The publish decision needs enough graded outcomes across both recommended and suppressed decisions, and the engine should remain fail-closed until then.</p>
    </div>
  );
}
