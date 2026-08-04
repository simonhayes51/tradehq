// src/v2/pages/Admin/tabs/ApiUsageTab.jsx
import SectionCard from "../../../components/SectionCard";
import { useAdminApiUsage } from "../../../hooks/useAdminApiUsage";

export default function ApiUsageTab() {
  const { data, isLoading, error } = useAdminApiUsage({ days: 14 });

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load API usage.</p>;

  const daily = data?.daily || [];
  const topKeys = data?.top_keys || [];

  return (
    <div className="flex flex-col gap-6">
      <SectionCard title="Daily requests" subtitle="Across every Data API key, last 14 days">
        {daily.length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No API usage recorded yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
            {daily.map((d) => (
              <li key={d.day} className="flex items-center justify-between py-1.5 text-xs">
                <span>{new Date(d.day).toLocaleDateString()}</span>
                <span className="text-[var(--v2-muted)]">
                  {d.requests.toLocaleString()} requests · {d.active_keys} active keys
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard title="Top keys" subtitle="Highest volume, last 14 days">
        {topKeys.length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No usage recorded yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
            {topKeys.map((k) => (
              <li key={k.id} className="flex items-center justify-between py-1.5 text-xs">
                <div>
                  <span className="font-medium">{k.name || k.key_prefix}</span>
                  <span className="text-[var(--v2-muted)]"> · {k.username || "unknown"} · {k.tier}</span>
                </div>
                <span className="text-[var(--v2-muted)]">{k.requests.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
