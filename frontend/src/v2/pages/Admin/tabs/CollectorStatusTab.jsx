// src/v2/pages/Admin/tabs/CollectorStatusTab.jsx
import SectionCard from "../../../components/SectionCard";
import { useAdminCollectors } from "../../../hooks/useAdminCollectors";
import { formatRelativeTime } from "../../../lib/format";

const STATUS_TONE = { ok: "text-[var(--v2-positive)]", failing: "text-[var(--v2-negative)]" };

export default function CollectorStatusTab() {
  const { data, isLoading, error } = useAdminCollectors();

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load collector status.</p>;

  const collectors = data?.collectors || [];

  return (
    <SectionCard title="auto_sync Collectors" subtitle="Every worker that has ever reported a heartbeat">
      {collectors.length === 0 ? (
        <p className="text-xs text-[var(--v2-muted)]">No collectors have reported in yet.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
          {collectors.map((c) => (
            <li key={c.worker} className="flex items-center justify-between py-2 text-xs">
              <div>
                <p className="font-medium">{c.worker}</p>
                <p className="text-[var(--v2-muted)] mt-0.5">{c.detail || "—"}</p>
              </div>
              <div className="text-right">
                <p className={`font-medium ${STATUS_TONE[c.status] || "text-[var(--v2-muted)]"}`}>{c.status}</p>
                <p className="text-[var(--v2-muted)] mt-0.5">{formatRelativeTime(c.last_run_at)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
