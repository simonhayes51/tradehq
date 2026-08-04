// src/v2/pages/AdminSbcImports/AdminSbcImports.jsx
//
// Narrow, single-purpose admin view for Phase 2. Phase 4 promotes this
// into a tab of the full Admin area rather than duplicating it.
import SectionCard from "../../components/SectionCard";
import StatTile from "../../components/StatTile";
import { useAdminSbcImports } from "../../hooks/useAdminSbcImports";
import { formatRelativeTime } from "../../lib/format";

export default function AdminSbcImports() {
  const { data, isLoading, error } = useAdminSbcImports();

  if (isLoading) {
    return <div role="status" className="p-6 text-sm text-[var(--v2-muted)]">Loading...</div>;
  }

  if (error) {
    const status = error?.response?.status;
    return (
      <div role="alert" className="p-6 text-sm text-[var(--v2-negative)]">
        {status === 403 ? "Admin access required." : "Couldn't load SBC import status."}
      </div>
    );
  }

  const status = data?.status;

  return (
    <div className="p-6 max-w-3xl mx-auto flex flex-col gap-6">
      {/* h2, not h1: this page is also reused as a tab inside Admin.jsx,
          which already renders its own page-level h1. */}
      <h2 className="text-xl font-semibold">SBC Imports</h2>

      <SectionCard title="Collector Status">
        <div className="grid grid-cols-3 gap-4">
          <StatTile
            label="Status"
            value={status?.status || "unknown"}
            deltaTone={status?.status === "ok" ? "positive" : status?.status === "failing" ? "negative" : "neutral"}
          />
          <StatTile label="Last run" value={status?.last_run_at ? formatRelativeTime(status.last_run_at) : "—"} />
          <StatTile label="Total SBC events" value={data?.total_sbc_events ?? 0} />
        </div>
      </SectionCard>

      <SectionCard title="Daily imports (last 7 days)">
        {(data?.daily_imports || []).length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No imports recorded yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
            {data.daily_imports.map((d) => (
              <li key={d.day} className="flex items-center justify-between py-1.5 text-xs">
                <span>{new Date(d.day).toLocaleDateString()}</span>
                <span className="text-[var(--v2-muted)]">{d.new_sets} new sets</span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
