// src/v2/pages/Admin/tabs/MarketEventsTab.jsx
import { Link } from "react-router-dom";
import SectionCard from "../../../components/SectionCard";
import { useAdminMarketEvents } from "../../../hooks/useAdminMarketEvents";
import { formatCoins, formatRelativeTime } from "../../../lib/format";

export default function MarketEventsTab() {
  const { data, isLoading, error } = useAdminMarketEvents({ limit: 30 });

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load market events.</p>;

  const items = data?.items || [];

  return (
    <SectionCard title="Market Events" subtitle={`${data?.count ?? 0} most recent, all kinds`}>
      {items.length === 0 ? (
        <p className="text-xs text-[var(--v2-muted)]">No events recorded yet.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
          {items.map((e) => (
            <li key={e.id} className="flex items-center justify-between py-2 text-xs">
              <div>
                {e.kind === "sbc" ? (
                  <Link to={`/v2/sbc/${e.id}`} className="font-medium hover:text-[var(--v2-accent)]">
                    {e.title}
                  </Link>
                ) : (
                  <span className="font-medium">{e.title}</span>
                )}
                <p className="text-[var(--v2-muted)] mt-0.5">
                  {e.kind} · {e.source}
                  {e.total_cost_coins ? ` · ${formatCoins(e.total_cost_coins)} coins` : ""}
                </p>
              </div>
              <span className="text-[var(--v2-muted)]">{formatRelativeTime(e.first_seen_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
