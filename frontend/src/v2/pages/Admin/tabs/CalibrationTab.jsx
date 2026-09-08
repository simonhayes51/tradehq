import { useState } from "react";
import SectionCard from "../../../components/SectionCard";
import { useAdminCalibration, useAdminCalibrationDecisions } from "../../../hooks/useAdminCalibration";

const POS = "text-[var(--v2-positive)]";
const NEG = "text-[var(--v2-negative)]";
const MUTED = "text-[var(--v2-muted)]";

function fmt(value, suffix = "") {
  return value === null || value === undefined ? "—" : `${Number(value).toFixed(1)}${suffix}`;
}

function Stat({ label, value, tone = MUTED, detail }) {
  return (
    <div className="rounded-lg border border-[var(--v2-border)] bg-black/20 p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${tone}`}>{value}</div>
      {detail ? <div className="mt-1 text-[10px] text-[var(--v2-muted)]">{detail}</div> : null}
    </div>
  );
}

function BucketTable({ title, rows = [] }) {
  if (!rows.length) return null;
  return (
    <SectionCard title={title}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">
            <tr className="border-b border-[var(--v2-border)]">
              <th className="py-2 pr-3">Band</th><th className="py-2 pr-3">n</th><th className="py-2 pr-3">Entry</th>
              <th className="py-2 pr-3">Profitable calls</th><th className="py-2 pr-3">95% CI</th><th className="py-2 pr-3">Median ROI</th><th className="py-2">MAE</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.bucket} className="border-b border-[var(--v2-border)]/60">
                <td className="py-2 pr-3 font-medium">{r.bucket}</td>
                <td className="py-2 pr-3">{r.sample_size ?? 0}</td>
                <td className="py-2 pr-3">{fmt(r.entry_rate_pct, "%")}</td>
                <td className="py-2 pr-3">{fmt(r.profitable_call_rate_pct, "%")}</td>
                <td className="py-2 pr-3">{Array.isArray(r.profitable_call_rate_ci95_pct) ? `${fmt(r.profitable_call_rate_ci95_pct[0], "%")}–${fmt(r.profitable_call_rate_ci95_pct[1], "%")}` : "—"}</td>
                <td className="py-2 pr-3">{fmt(r.median_roi_pct, "%")}</td>
                <td className="py-2">{fmt(r.avg_mae_pct, "%")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

export default function CalibrationTab() {
  const [horizon, setHorizon] = useState("24h");
  const [windowDays, setWindowDays] = useState(90);
  const report = useAdminCalibration({ horizon, windowDays });
  const decisions = useAdminCalibrationDecisions();

  if (report.isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading calibration evidence...</p>;
  if (report.error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load calibration evidence.</p>;

  const data = report.data || {};
  const dimensions = data.dimensions || {};
  const overall = (dimensions.overall || [])[0] || {};
  const candidates = data.review_candidates || [];
  const blockers = data.blockers || [];
  const integrity = data.version_integrity || {};
  const allowed = Boolean(data.calibration_allowed);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Engine Calibration</div>
          <div className="text-xs text-[var(--v2-muted)]">Outcome-backed evidence only. This screen cannot change a live threshold.</div>
        </div>
        <div className="flex gap-1">
          {["24h", "48h", "7d"].map((v) => <button key={v} onClick={() => setHorizon(v)} className={`rounded-md border px-2 py-1 text-[10px] ${horizon === v ? "border-[var(--v2-accent)]" : "border-[var(--v2-border)] text-[var(--v2-muted)]"}`}>{v}</button>)}
          {[30, 90, 180].map((v) => <button key={v} onClick={() => setWindowDays(v)} className={`rounded-md border px-2 py-1 text-[10px] ${windowDays === v ? "border-[var(--v2-accent)]" : "border-[var(--v2-border)] text-[var(--v2-muted)]"}`}>{v}d</button>)}
        </div>
      </div>

      <SectionCard title="Calibration Readiness" subtitle={`${data.engine_version || "unknown engine"} · grader ${data.grader_version || "unknown"}`}>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Allowed to review" value={allowed ? "YES" : "NO"} tone={allowed ? POS : NEG} />
          <Stat label="Calls graded" value={overall.sample_size ?? 0} detail={`${horizon} outcomes`} />
          <Stat label="Profitable calls" value={fmt(overall.profitable_call_rate_pct, "%")} detail="all calls, not just entries" />
          <Stat label="Config fingerprints" value={integrity.config_fingerprints ?? "—"} tone={integrity.config_clean === false ? NEG : POS} detail={integrity.config_clean === false ? "version contaminated" : "version/config consistent"} />
        </div>
        {blockers.length ? <div className="mt-3 rounded-lg border border-[var(--v2-border)] p-3 text-xs text-[var(--v2-negative)]">Blocked: {blockers.map((b) => b.replaceAll("_", " ")).join(", ")}</div> : null}
      </SectionCard>

      <SectionCard title="Statistically Defensible Review Candidates" subtitle="Requires minimum samples, practical effect and family-wise corrected significance">
        {!candidates.length ? (
          <p className="text-xs text-[var(--v2-muted)]">No threshold move is currently justified by the configured evidence rules.</p>
        ) : (
          <div className="space-y-2">
            {candidates.map((c, i) => (
              <div key={`${c.dimension}-${c.lower_bucket}-${c.higher_bucket}-${i}`} className="rounded-lg border border-[var(--v2-border)] p-3 text-xs">
                <div className="font-medium">{c.dimension}: {c.lower_bucket} → {c.higher_bucket}</div>
                <div className="mt-1 text-[var(--v2-muted)]">Δ profitable-call rate {fmt(c.profitable_call_rate_delta_pp, "pp")} · n={c.lower_sample}/{c.higher_sample} · p={c.p_value ?? "—"}</div>
                <div className="mt-1 text-[var(--v2-positive)]">Review only. No automatic engine change.</div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <BucketTable title="Confidence Calibration" rows={dimensions.confidence || []} />
      <BucketTable title="Liquidity Calibration" rows={dimensions.liquidity || []} />
      <BucketTable title="Expected ROI Calibration" rows={dimensions.expected_roi || []} />
      <BucketTable title="Trend State" rows={dimensions.trend_state || []} />
      <BucketTable title="Price Tier" rows={dimensions.price_tier || []} />

      <SectionCard title="Calibration Decision Audit" subtitle="Human decisions recorded separately from live configuration">
        {decisions.isLoading ? <p className="text-xs text-[var(--v2-muted)]">Loading decisions...</p> : !(decisions.data?.items || []).length ? (
          <p className="text-xs text-[var(--v2-muted)]">No calibration decisions recorded yet.</p>
        ) : (
          <div className="space-y-2">
            {(decisions.data?.items || []).slice(0, 12).map((item) => (
              <div key={item.id} className="rounded-lg border border-[var(--v2-border)] p-3 text-xs">
                <div className="flex justify-between gap-3"><span className="font-medium">{item.parameter}</span><span className="text-[var(--v2-muted)]">{item.status}</span></div>
                <div className="mt-1 text-[var(--v2-muted)]">{item.from_engine_version}{item.to_engine_version ? ` → ${item.to_engine_version}` : ""}</div>
                {item.rationale ? <div className="mt-1">{item.rationale}</div> : null}
              </div>
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
