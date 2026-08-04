// src/v2/pages/Admin/tabs/PipelineHealthTab.jsx
import SectionCard from "../../../components/SectionCard";
import { useAdminPipelineHealth } from "../../../hooks/useAdminPipelineHealth";

const STATUS_TONE = { ok: "text-[var(--v2-positive)]", stale: "text-[var(--v2-negative)]", unknown: "text-[var(--v2-muted)]" };

export default function PipelineHealthTab() {
  const { data, isLoading, error } = useAdminPipelineHealth();

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load pipeline health.</p>;

  const engines = data?.engines || [];

  return (
    <SectionCard title="Internal Refresh Loops" subtitle="Fair value, analytics, recommendations, event impact, market regime">
      <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
        {engines.map((e) => (
          <li key={e.name} className="flex items-center justify-between py-2 text-xs">
            <span>{e.name}</span>
            <div className="flex items-center gap-3">
              <span className="text-[var(--v2-muted)]">
                {e.age_seconds !== null ? `${Math.round(e.age_seconds / 60)}m ago` : "never"}
              </span>
              <span className={`font-medium ${STATUS_TONE[e.status] || STATUS_TONE.unknown}`}>{e.status}</span>
            </div>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
