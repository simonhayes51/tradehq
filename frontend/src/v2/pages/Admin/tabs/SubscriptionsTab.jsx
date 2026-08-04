// src/v2/pages/Admin/tabs/SubscriptionsTab.jsx
import SectionCard from "../../../components/SectionCard";
import { useAdminSubscriptions } from "../../../hooks/useAdminSubscriptions";
import { formatCountdown } from "../../../lib/format";

const STATUS_TONE = { active: "text-[var(--v2-positive)]", trialing: "text-[var(--v2-positive)]" };

export default function SubscriptionsTab() {
  const { data, isLoading, error } = useAdminSubscriptions({ limit: 30 });

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load subscriptions.</p>;

  const items = data?.items || [];

  return (
    <SectionCard title="Subscriptions" subtitle="Stripe subscription records, most recent first">
      {items.length === 0 ? (
        <p className="text-xs text-[var(--v2-muted)]">No subscriptions recorded yet.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
          {items.map((s) => (
            <li key={s.id} className="flex items-center justify-between py-2 text-xs">
              <div>
                <p className="font-medium">{s.username || s.user_id}</p>
                <p className="text-[var(--v2-muted)] mt-0.5">
                  {s.plan_id}
                  {s.cancel_at_period_end ? " · cancels at period end" : ""}
                </p>
              </div>
              <div className="text-right">
                <p className={`font-medium ${STATUS_TONE[s.status] || "text-[var(--v2-muted)]"}`}>{s.status}</p>
                <p className="text-[var(--v2-muted)] mt-0.5">
                  {s.current_period_end ? `renews in ${formatCountdown(s.current_period_end)}` : "—"}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
