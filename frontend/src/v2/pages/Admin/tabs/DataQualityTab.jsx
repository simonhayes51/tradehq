import { useState } from "react";
import SectionCard from "../../../components/SectionCard";
import { useAdminDataQuality } from "../../../hooks/useAdminDataQuality";

const POSITIVE = "text-[var(--v2-positive)]";
const NEGATIVE = "text-[var(--v2-negative)]";
const MUTED = "text-[var(--v2-muted)]";

function pct(value) {
  return value === null || value === undefined ? "—" : `${Number(value).toFixed(1)}%`;
}

function age(minutes) {
  if (minutes === null || minutes === undefined) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  return `${(minutes / 60).toFixed(1)}h`;
}

function coins(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString();
}

function Stat({ label, value, tone = MUTED, detail }) {
  return (
    <div className="rounded-lg border border-[var(--v2-border)] bg-black/20 p-3 min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${tone}`}>{value}</div>
      {detail ? <div className="mt-1 text-[10px] text-[var(--v2-muted)]">{detail}</div> : null}
    </div>
  );
}

export default function DataQualityTab() {
  const [hours, setHours] = useState(24);
  const { data, isLoading, error, isFetching, refetch } = useAdminDataQuality(hours, 30);

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading data quality...</p>;
  if (error) return <p className="text-xs text-[var(--v2-negative)]">Couldn't load data quality.</p>;

  const intelligence = data?.intelligence || {};
  const shadow = data?.salesShadow || {};
  const pickup = shadow?.pickup_analysis || {};
  const cache = intelligence?.dashboard_market_cache || {};
  const repeated = pickup?.repeated_missed_cards || [];
  const reasons = Object.entries(shadow?.reason_counts || {}).sort((a, b) => b[1] - a[1]);
  const signals = Object.entries(intelligence?.signals || {}).sort((a, b) => b[1] - a[1]);
  const scannerHealthy = intelligence.status === "ok";

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Data Quality</div>
          <div className="text-xs text-[var(--v2-muted)]">
            Scanner freshness, persisted-state coverage and whether the fixed SalesSync budget is missing cards users actually care about.
          </div>
        </div>
        <div className="flex items-center gap-1">
          {[24, 72, 168].map((value) => (
            <button
              key={value}
              onClick={() => setHours(value)}
              className={`rounded-md border px-2 py-1 text-[10px] ${
                hours === value
                  ? "border-[var(--v2-accent)] text-[var(--v2-text)]"
                  : "border-[var(--v2-border)] text-[var(--v2-muted)]"
              }`}
            >
              {value === 168 ? "7d" : `${value}h`}
            </button>
          ))}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="ml-1 rounded-md border border-[var(--v2-border)] px-2 py-1 text-[10px] text-[var(--v2-muted)] disabled:opacity-50"
          >
            {isFetching ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <SectionCard title="Current Intelligence" subtitle="Scanner-persisted state used by Home, Opportunities, Trade Finder and Discord">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat
            label="Status"
            value={(intelligence.status || "unknown").toUpperCase()}
            tone={scannerHealthy ? POSITIVE : NEGATIVE}
            detail={intelligence.reason || "scanner state current"}
          />
          <Stat
            label="Coverage"
            value={pct(intelligence.current_coverage_pct)}
            tone={(intelligence.current_coverage_pct || 0) >= 90 ? POSITIVE : NEGATIVE}
            detail={`${coins(intelligence.current_rows)} / ${coins(intelligence.player_pool)} cards`}
          />
          <Stat
            label="Newest state age"
            value={age(intelligence.newest_age_minutes)}
            tone={(intelligence.newest_age_minutes ?? 999) <= 20 ? POSITIVE : NEGATIVE}
            detail="degraded above 20m"
          />
          <Stat
            label="Evaluated 24h"
            value={pct(intelligence.evaluated_24h_pct)}
            detail={`${coins(intelligence.evaluated_24h)} cards`}
          />
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-[var(--v2-muted)]">Signal population</div>
            <div className="flex flex-wrap gap-1.5">
              {signals.map(([signal, count]) => (
                <span key={signal} className="rounded-md border border-[var(--v2-border)] px-2 py-1 text-[10px]">
                  {signal}: <strong>{Number(count).toLocaleString()}</strong>
                </span>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-[var(--v2-muted)]">Transitions</div>
            <div className="text-xs text-[var(--v2-muted)]">
              <span className="text-[var(--v2-text)] font-medium">{intelligence.transitions_1h ?? 0}</span> in 1h ·{" "}
              <span className="text-[var(--v2-text)] font-medium">{intelligence.transitions_24h ?? 0}</span> in 24h
            </div>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Dashboard Current-State Cache" subtitle="Home must never fall back to market-wide live scoring">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Source" value={(cache.source || "unknown").toUpperCase()} tone={cache.source === "persisted" ? POSITIVE : NEGATIVE} />
          <Stat label="Cards" value={coins(cache.cards)} />
          <Stat label="Age" value={cache.age_seconds === null || cache.age_seconds === undefined ? "—" : `${Math.round(cache.age_seconds)}s`} />
          <Stat
            label="Live fallback"
            value={cache.live_fallback_enabled ? "ENABLED" : "OFF"}
            tone={cache.live_fallback_enabled ? NEGATIVE : POSITIVE}
          />
        </div>
      </SectionCard>

      <SectionCard title="SalesSync Information Shadow" subtitle={`Read-only comparison over ${hours === 168 ? "7 days" : `${hours} hours`}. Live budget remains unchanged.`}>
        {shadow.status !== "ok" ? (
          <p className="text-xs text-[var(--v2-muted)]">Shadow is still warming: {shadow.reason || "no observations yet"}.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Stat label="Cycles observed" value={coins(shadow.cycles)} />
              <Stat label="Live due / cycle" value={shadow.avg_live_due_per_cycle ?? "—"} detail="existing selector" />
              <Stat label="Shadow wanted / cycle" value={shadow.avg_shadow_priority_per_cycle ?? "—"} detail="not extra capacity" />
              <Stat label="Natural pickup ≤30m" value={pct(pickup.pickup_rate_pct)} tone={(pickup.pickup_rate_pct ?? 100) >= 70 ? POSITIVE : NEGATIVE} />
              <Stat
                label="Still missed"
                value={coins(pickup.still_missed_after_window)}
                detail={`${coins(pickup.measured_shadow_card_events)} mature · ${coins(pickup.pending_shadow_card_events || 0)} pending`}
              />
              <Stat label="User-driven pickup" value={pct(pickup.user_driven_pickup_rate_pct)} tone={(pickup.user_driven_pickup_rate_pct ?? 100) >= 70 ? POSITIVE : NEGATIVE} />
              <Stat label="Held-position events" value={coins(shadow.held_position_shadow_events)} />
              <Stat label="Extra FUT.GG requests" value={shadow.extra_futgg_sales_requests ?? 0} tone={shadow.extra_futgg_sales_requests === 0 ? POSITIVE : NEGATIVE} />
            </div>

            <div className="mt-4">
              <div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-[var(--v2-muted)]">Why the shadow wanted cards</div>
              <div className="flex flex-wrap gap-1.5">
                {reasons.length ? reasons.map(([reason, count]) => (
                  <span key={reason} className="rounded-md border border-[var(--v2-border)] px-2 py-1 text-[10px]">
                    {reason.replaceAll("_", " ")}: <strong>{Number(count).toLocaleString()}</strong>
                  </span>
                )) : <span className="text-xs text-[var(--v2-muted)]">No reason data yet.</span>}
              </div>
            </div>
          </>
        )}
      </SectionCard>

      <SectionCard title="Repeated Live-Scheduler Misses" subtitle="Cards the information shadow requested at least twice and the normal selector still did not pick up inside 30 minutes">
        {!repeated.length ? (
          <p className="text-xs text-[var(--v2-muted)]">No repeated misses in this window.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">
                <tr className="border-b border-[var(--v2-border)]">
                  <th className="py-2 pr-3 font-medium">Card</th>
                  <th className="py-2 pr-3 font-medium">Signal</th>
                  <th className="py-2 pr-3 font-medium">BIN</th>
                  <th className="py-2 pr-3 font-medium">Misses</th>
                  <th className="py-2 pr-3 font-medium">User-driven</th>
                  <th className="py-2 font-medium">Reasons</th>
                </tr>
              </thead>
              <tbody>
                {repeated.map((card) => (
                  <tr key={card.card_id} className="border-b border-[var(--v2-border)]/60">
                    <td className="py-2 pr-3">
                      <div className="font-medium text-[var(--v2-text)]">{card.name || `Card ${card.card_id}`}</div>
                      <div className="text-[10px] text-[var(--v2-muted)]">{card.rating || "—"} {card.rarity || ""}</div>
                    </td>
                    <td className="py-2 pr-3">{card.current_signal || "—"}</td>
                    <td className="py-2 pr-3">{coins(card.current_bin)}</td>
                    <td className="py-2 pr-3 font-semibold text-[var(--v2-negative)]">{card.missed_events}</td>
                    <td className="py-2 pr-3">{card.user_driven_events}</td>
                    <td className="py-2 text-[10px] text-[var(--v2-muted)]">
                      {Object.entries(card.reasons || {}).map(([reason, count]) => `${reason.replaceAll("_", " ")} ×${count}`).join(", ") || "older shadow data"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <p className="text-[10px] leading-relaxed text-[var(--v2-muted)]">
        This screen is evidence for reallocating the existing detailed-sales budget, not a reason to increase collector throughput. A low pickup rate on repeated user-driven misses is the signal to reserve some of the current 60 slots, while a high pickup rate means the baseline scheduler is already catching up quickly enough.
      </p>
    </div>
  );
}
